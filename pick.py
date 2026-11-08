from config import *
from llm import ask_json

PROMPT = """You are an elite short-form video editor for TikTok, YouTube Shorts, and Instagram Reels (inspired by Opus Clip and Vizard).
Task: From this timestamped transcript, discover and extract up to {candidate_count} standout candidate moments ({lo}-{hi} seconds each).
Guideline: {focus_guide}

Evaluation Criteria:
- Opening Hook (25%): Grabs immediate curiosity within first 3s.
- High Engagement Content (20%): Entertaining, eye-opening, or dramatic.
- Emotional Factor (15%): Humour, shock, inspiration, or strong opinion.
- Story Completeness (15%): Clear setup and satisfying punchline/payoff.
- Shareability & Virality (10%): High urge for viewers to share or comment.
- Ending Payoff (10%): Natural, non-abrupt resolution.
- Audio/Dialogue Flow (5%): Fluent sentence boundaries.

Categories to discover across the video:
- "Viral Hook" (unbelievable statements, curiosity gaps)
- "Controversial" (bold debates, polarizing takes)
- "Funny" (comedy, fails, punchlines)
- "Insight" (profound quotes, lessons, tips)
- "Story" (compelling narrative with payoff)
- "Action" (intense moments, hype reactions)

Requirements:
1. Start and end cleanly at natural sentence boundaries.
2. Assign each candidate a virality score from 75 to 99 based on the criteria.
3. Provide a 1-sentence "hook_reason" explaining why viewers will watch.

Return ONLY valid JSON matching this schema:
{{"clips":[{{"start":12.5,"end":45.0,"title":"Punchy 4-7 Word Hook Title","caption":"1-2 engaging sentences describing the clip","category":"Viral Hook","score":95,"hook_reason":"Irresistible curiosity gap in the first 3 seconds with a strong payoff at the end","hashtags":["#viral","#shorts","#foryou"]}}]}}

Transcript:
{t}"""

FOCUS_GUIDES = {
    "viral": "Prioritize irresistible hooks, shocking revelations, high tension, and storytelling clips that keep viewers glued.",
    "action": "Prioritize intense action, hype gameplay moments, clutch plays, high energy reactions, and explosive drama.",
    "funny": "Prioritize hilarious moments, comedy fails, infectious laughter, jokes, and funny unexpected dialogue.",
    "insight": "Prioritize eye-opening insights, life lessons, deep quotes, expert tips, and thought-provoking statements.",
    "controversial": "Prioritize bold opinions, heated debates, counter-intuitive arguments, and polarizing statements."
}

def _deduplicate_and_rank(candidates, count):
    """
    Ranks clips by virality score, eliminates timestamp overlaps,
    and returns top 'count' unique clips ordered by their appearance in the video.
    """
    # Sort candidates by score descending
    candidates.sort(key=lambda c: int(c.get("score", 85)), reverse=True)
    selected = []

    for c in candidates:
        s, e = float(c.get("start", 0)), float(c.get("end", 0))
        if e <= s or (e - s) < 10:
            continue

        # Check overlap against already chosen clips
        has_overlap = False
        for chosen in selected:
            cs, ce = float(chosen["start"]), float(chosen["end"])
            # If overlapping by more than 8 seconds or start within 15 seconds
            overlap_dur = max(0.0, min(e, ce) - max(s, cs))
            if overlap_dur > 8.0 or abs(s - cs) < 15.0:
                has_overlap = True
                break

        if not has_overlap:
            selected.append(c)

        if len(selected) >= count:
            break

    # Re-sort final selection chronologically for smooth review
    selected.sort(key=lambda c: float(c.get("start", 0)))
    return selected

def _heuristic_clips(segs, count=5, min_len=20, max_len=60, focus="viral"):
    if not segs:
        return []
    total_duration = max(s.get("end", 0) for s in segs)
    if total_duration < min_len:
        return [{
            "start": 0.0,
            "end": max(float(total_duration), 5.0),
            "title": "Top Moment Highlight",
            "caption": "Top viral moment from this video",
            "category": "Viral Hook",
            "score": 96,
            "hook_reason": "High-interest opening statement and key takeaway",
            "hashtags": ["#shorts", "#viral", "#foryou"]
        }]

    target_count = min(count, max(1, int(total_duration // max(min_len, 15))))
    clip_len = min(max_len, max(min_len, 35.0))
    step = max(clip_len, (total_duration - clip_len) / max(1, target_count))

    categories = ["Viral Hook", "Story", "Funny", "Insight", "Controversial", "Action"]
    hooks = [
        "Immediate curiosity gap with engaging question",
        "Unexpected punchline and high emotional reaction",
        "Key insight that challenges conventional thinking",
        "High tension moment building up to clean resolution",
        "Relatable story with instant narrative hook"
    ]

    clips = []
    for i in range(target_count):
        st = max(0.0, i * step + (5.0 if i > 0 else 1.0))
        en = min(total_duration, st + clip_len)
        if en <= st or en - st < min_len - 5:
            continue
        sub = [s for s in segs if st <= s.get("start", 0) <= en]
        title_text = sub[0]["text"][:40] if sub else f"Viral Moment #{i+1}"
        cat = categories[i % len(categories)]
        score = 98 - (i * 2 % 10)

        clips.append({
            "start": round(st, 1),
            "end": round(en, 1),
            "title": title_text.strip() or f"Highlight #{i+1}",
            "caption": f"Must-watch {cat.lower()} clip from this session",
            "category": cat,
            "score": max(85, score),
            "hook_reason": hooks[i % len(hooks)],
            "hashtags": ["#shorts", "#clip", "#viral", f"#{cat.lower().replace(' ', '')}"]
        })
    return clips

def pick(segs, count=None, min_len=None, max_len=None, focus="viral"):
    n = count or CLIP_COUNT
    lo = min_len or MIN_LEN
    hi = max_len or MAX_LEN
    focus_guide = FOCUS_GUIDES.get(focus, FOCUS_GUIDES["viral"])

    # Ask for 1.8x - 2x candidates so we can rank and deduplicate the very best
    candidate_count = min(40, max(n * 2, 8))

    t = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in segs)
    try:
        data = ask_json(PROMPT.format(candidate_count=candidate_count, lo=lo, hi=hi, focus_guide=focus_guide, t=t))
        raw_clips = data["clips"] if isinstance(data, dict) and "clips" in data else data
        valid = [c for c in raw_clips if lo - 8 <= (c.get("end", 0) - c.get("start", 0)) <= hi + 25]
        if valid:
            ranked = _deduplicate_and_rank(valid, n)
            if ranked:
                return ranked
    except Exception as e:
        print(f"AI moment selection notice: {e}. Using transcript windowing fallback.")

    return _heuristic_clips(segs, count=n, min_len=lo, max_len=hi, focus=focus)
