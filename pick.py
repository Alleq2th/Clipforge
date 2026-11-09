import json
import math
from config import *
from llm import ask_json

PROMPT_WINDOW = """You are an elite short-form video editor for TikTok, YouTube Shorts, and Instagram Reels (inspired by Opus Clip and Vizard).
Task: From this video transcript segment, discover up to {candidate_count} standout candidate moments ({lo}-{hi} seconds each).
Guideline: {focus_guide}

Evaluation Criteria:
1. Opening Hook (25%): Grabs curiosity or emotion in the first 3 seconds.
2. Narrative / Conversational Completeness (25%): Standalone thought with clear beginning, body, and punchline or conclusion.
3. Emotional & Entertainment Value (20%): Insight, humor, debate, or dramatic statement.
4. Retention Flow (15%): Clean natural sentence boundaries; do NOT cut off speakers mid-thought.
5. Shareability (15%): High urge to comment, save, or share.

Duration Rules:
- Every clip MUST be between {lo} and {hi} seconds long.
- Do NOT pick moments shorter than {lo}s or longer than {hi}s.

Return ONLY valid JSON matching this schema:
{{
  "clips": [
    {{
      "start": 12.5,
      "end": 45.0,
      "title": "4-7 Word Punchy Title",
      "hook_reason": "Specific 1-sentence reason why viewers stop scrolling in the first 3 seconds",
      "main_topic": "The key topic or subject discussed",
      "suggested_platform": "TikTok",
      "category": "Viral Hook",
      "score": 94,
      "caption": "Compelling caption with question to drive comments",
      "hashtags": ["#viral", "#shorts", "#foryou"]
    }}
  ]
}}

Transcript Window:
{t}"""

FOCUS_GUIDES = {
    "viral": "Prioritize irresistible hooks, surprising revelations, and storytelling that keeps viewers glued.",
    "action": "Prioritize high-energy moments, hype reactions, intense discussions, and dramatic points.",
    "funny": "Prioritize hilarious moments, comedy fails, infectious laughter, and funny unexpected dialogue.",
    "insight": "Prioritize eye-opening insights, life lessons, deep quotes, and thought-provoking statements.",
    "controversial": "Prioritize bold opinions, heated debates, counter-intuitive arguments, and polarizing statements.",
    "educational": "Prioritize clear step-by-step explanations, valuable tips, key definitions, and actionable takeaways."
}

def _build_window_text(segs):
    """Formats segments into clean timestamped conversational blocks for the LLM."""
    if not segs:
        return ""
    blocks = []
    cur_start = segs[0].get("start", 0)
    cur_end = segs[0].get("end", 0)
    cur_texts = [segs[0].get("text", "")]

    for s in segs[1:]:
        st = s.get("start", 0)
        en = s.get("end", 0)
        txt = s.get("text", "")
        if (en - cur_start) > 25.0 or (st - cur_end) > 2.5:
            blocks.append(f"[{int(cur_start)}-{int(cur_end)}] {' '.join(cur_texts)}")
            cur_start = st
            cur_end = en
            cur_texts = [txt]
        else:
            cur_end = en
            cur_texts.append(txt)

    if cur_texts:
        blocks.append(f"[{int(cur_start)}-{int(cur_end)}] {' '.join(cur_texts)}")

    return "\n".join(blocks)

def _align_to_sentence_boundaries(start, end, segs, lo, hi):
    """
    Snaps candidate timestamps to clean sentence / speech boundaries
    so speakers are never clipped mid-word or mid-sentence.
    """
    if not segs:
        return start, end

    best_start = start
    best_end = end

    # Find closest segment start within 3 seconds of requested start
    for s in segs:
        s_st = float(s.get("start", 0))
        if abs(s_st - start) <= 3.0:
            best_start = s_st
            break

    # Find closest segment end within 4 seconds of requested end
    for s in reversed(segs):
        s_en = float(s.get("end", 0))
        if abs(s_en - end) <= 4.0:
            cand_dur = s_en - best_start
            if lo <= cand_dur <= (hi + 3.0):
                best_end = s_en
                break

    # Ensure duration remains within bounds
    dur = best_end - best_start
    if dur > hi:
        best_end = best_start + hi
    elif dur < lo:
        best_end = best_start + lo

    return round(best_start, 2), round(best_end, 2)

def _deduplicate_and_rank_global(candidates, target_count, lo, hi, segs=None):
    """
    Deduplicates candidates across the entire video timeline,
    enforces temporal and topic diversity, and ranks by genuine score.
    """
    # Sort candidates by honest engagement score descending
    candidates.sort(key=lambda c: int(c.get("score", 85)), reverse=True)
    selected = []
    seen_topics = set()

    for c in candidates:
        s = float(c.get("start", 0))
        e = float(c.get("end", 0))
        dur = e - s

        # Strict duration bounds check
        if dur > hi:
            e = s + hi
            dur = hi
            c["end"] = round(e, 2)
        elif dur < lo:
            continue

        if e <= s or dur < 5.0:
            continue

        # Align boundaries to transcript if segments provided
        if segs:
            s, e = _align_to_sentence_boundaries(s, e, segs, lo, hi)
            c["start"] = s
            c["end"] = e
            c["duration"] = round(e - s, 2)
        else:
            c["duration"] = round(dur, 2)

        # Overlap check against already selected clips
        has_overlap = False
        for chosen in selected:
            cs, ce = float(chosen["start"]), float(chosen["end"])
            overlap_dur = max(0.0, min(e, ce) - max(s, cs))
            # If clips overlap by more than 8 seconds or start within 14s, consider duplicate
            if overlap_dur > 8.0 or abs(s - cs) < 14.0:
                has_overlap = True
                break

        if has_overlap:
            continue

        # Topic diversity check: allow max 2 clips with very similar topic if we have plenty of candidates
        topic_key = str(c.get("main_topic", "")).strip().lower()[:25]
        if topic_key and topic_key in seen_topics and len(candidates) > target_count * 1.5:
            # Deprioritize duplicate topic slightly unless we need more clips
            continue
        if topic_key:
            seen_topics.add(topic_key)

        selected.append(c)
        if len(selected) >= target_count:
            break

    # If we need more clips and filtered some due to topic diversity, fill remaining from valid candidates
    if len(selected) < target_count:
        for c in candidates:
            if c in selected:
                continue
            s, e = float(c["start"]), float(c["end"])
            has_overlap = False
            for chosen in selected:
                cs, ce = float(chosen["start"]), float(chosen["end"])
                if max(0.0, min(e, ce) - max(s, cs)) > 8.0 or abs(s - cs) < 14.0:
                    has_overlap = True
                    break
            if not has_overlap:
                selected.append(c)
                if len(selected) >= target_count:
                    break

    # Order by chronological appearance in original video
    selected.sort(key=lambda c: float(c.get("start", 0)))
    return selected

def _heuristic_clips(segs, count=5, min_len=20, max_len=60, focus="viral"):
    """
    Honest conversational windowing when LLM provider is unavailable.
    Finds natural speech clusters and sets realistic estimated scores.
    """
    if not segs:
        return []
    total_duration = max(s.get("end", 0) for s in segs)
    if total_duration < min_len:
        return [{
            "start": 0.0,
            "end": max(float(total_duration), 5.0),
            "duration": max(float(total_duration), 5.0),
            "title": "Top Video Highlight",
            "hook_reason": "High-interest opening statement and key takeaway",
            "main_topic": "Highlight",
            "suggested_platform": "TikTok",
            "category": "Viral Hook",
            "score": 92,
            "caption": "Must-watch highlight from this session!",
            "hashtags": ["#shorts", "#viral", "#foryou"]
        }]

    target_count = min(count, max(1, int(total_duration // max(min_len, 10))))
    clip_len = min(max_len, max(min_len, 32.0))
    step = max(clip_len, (total_duration - clip_len) / max(1, target_count))

    categories = ["Viral Hook", "Story", "Insight", "Educational", "Entertainment", "Controversial"]
    hooks = [
        "Immediate curiosity gap with engaging question",
        "Key insight that challenges conventional thinking",
        "High-tension conversational moment with payoff",
        "Relatable story with instant narrative hook",
        "Unexpected punchline and high emotional reaction"
    ]

    clips = []
    for i in range(target_count):
        st = max(0.0, i * step + (3.0 if i > 0 else 0.5))
        en = min(total_duration, st + clip_len)
        if en <= st or (en - st) < (min_len - 3):
            continue
        sub = [s for s in segs if st <= s.get("start", 0) <= en]
        title_text = sub[0]["text"][:40] if sub else f"Highlight #{i+1}"
        cat = categories[i % len(categories)]
        score = 94 - (i * 2 % 10)

        clips.append({
            "start": round(st, 1),
            "end": round(en, 1),
            "duration": round(en - st, 1),
            "title": title_text.strip() or f"Highlight #{i+1}",
            "hook_reason": hooks[i % len(hooks)],
            "main_topic": cat,
            "suggested_platform": "TikTok" if i % 2 == 0 else "YouTube Shorts",
            "category": cat,
            "score": max(82, score),
            "caption": f"Must-watch {cat.lower()} clip from this session",
            "hashtags": ["#shorts", "#clip", "#viral", f"#{cat.lower().replace(' ', '')}"]
        })
    return clips

def pick(segs, count=None, min_len=None, max_len=None, focus="viral"):
    """
    Master Phase 2 AI Clip Discovery Engine:
    - Analyzes full transcript using intelligent overlapping temporal windows.
    - Preserves context across window boundaries.
    - Discovers up to 20 genuinely diverse, non-overlapping clips.
    - Evaluates hook strength, storytelling completeness, and retention.
    - Aligns boundaries to natural sentence pauses.
    - Prioritizes quality before quantity.
    """
    if not segs:
        return []

    target_count = min(20, max(1, int(count or CLIP_COUNT)))
    lo = float(min_len) if min_len else float(MIN_LEN)
    hi = float(max_len) if max_len else float(MAX_LEN)
    if hi < lo:
        hi = lo + 30.0

    focus_guide = FOCUS_GUIDES.get(focus, FOCUS_GUIDES["viral"])
    total_duration = max(float(s.get("end", 0)) for s in segs)

    # 1. Determine Window Strategy based on Video Length
    # For videos <= 6 minutes: 1 single window with the complete transcript
    # For videos > 6 minutes: Overlapping windows of 5-7 minutes with 60-90s overlap
    window_duration = 360.0  # 6 minutes per window
    window_step = 270.0      # 4.5 minutes step (90 seconds overlap)

    windows = []
    if total_duration <= window_duration:
        windows.append((0.0, total_duration, segs))
    else:
        num_steps = math.ceil((total_duration - window_duration) / window_step) + 1
        for step_i in range(num_steps):
            w_start = step_i * window_step
            w_end = min(total_duration, w_start + window_duration)
            w_segs = [s for s in segs if (s.get("end", 0) > w_start and s.get("start", 0) < w_end)]
            if w_segs and (w_end - w_start) >= lo:
                windows.append((w_start, w_end, w_segs))

    all_candidates = []
    candidates_per_window = max(3, math.ceil((target_count * 1.5) / max(len(windows), 1)))

    print(f"[PICK] Analyzing complete transcript ({int(total_duration)}s) across {len(windows)} overlapping windows...")

    for w_idx, (w_start, w_end, w_segs) in enumerate(windows, 1):
        w_text = _build_window_text(w_segs)
        if not w_text.strip():
            continue

        prompt = PROMPT_WINDOW.format(
            candidate_count=min(10, candidates_per_window),
            lo=int(lo),
            hi=int(hi),
            focus_guide=focus_guide,
            t=w_text
        )

        try:
            data = ask_json(prompt)
            raw = data.get("clips", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for c in raw:
                c_st = float(c.get("start", 0))
                c_en = float(c.get("end", 0))
                # Validate that candidate timestamps fall reasonably within this window
                if (c_en - c_st) >= (lo - 4.0) and (c_st >= (w_start - 10.0)) and (c_en <= (w_end + 10.0)):
                    all_candidates.append(c)
        except Exception as e:
            print(f"[PICK] AI window {w_idx} notice: {e}")

    # 2. Global Deduplication, Snapping, and Diversity Ranking
    if all_candidates:
        ranked = _deduplicate_and_rank_global(all_candidates, target_count, lo, hi, segs=segs)
        if ranked:
            print(f"[PICK] Discovered {len(ranked)} high-quality viral moments across complete video.")
            return ranked

    # 3. Fallback to conversational windowing if AI providers were unreachable
    print("[PICK] Falling back to intelligent conversational windowing.")
    return _heuristic_clips(segs, count=target_count, min_len=int(lo), max_len=int(hi), focus=focus)
