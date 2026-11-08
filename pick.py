from config import *
from llm import ask_json

PROMPT = """You are an elite short-form video editor for TikTok, YouTube Shorts, and Instagram Reels (inspired by Opus Clip and Vizard).
Task: From this condensed transcript, discover and extract up to {candidate_count} standout candidate moments ({lo}-{hi} seconds each).
Guideline: {focus_guide}

Evaluation Criteria:
- Opening Hook (25%): Grabs curiosity in first 3s.
- High Engagement (20%): Entertaining, eye-opening, or dramatic.
- Emotional Factor (15%): Humour, shock, inspiration, or strong opinion.
- Story Completeness (15%): Clear setup and punchline/payoff.
- Shareability (10%): High urge to comment or share.
- Audio Flow (15%): Clean sentence boundaries.

Requirements:
1. Provide a 4-7 word punchy title.
2. Provide a 1-sentence "hook_reason" explaining why viewers will stop scrolling.
3. Assign score between 80 and 99.

Return ONLY valid JSON matching this schema:
{{"clips":[{{"start":12.5,"end":45.0,"title":"Punchy Hook Title","caption":"Engaging description","category":"Viral Hook","score":95,"hook_reason":"Curiosity gap in first 3s with payoff","hashtags":["#viral","#shorts","#foryou"]}}]}}

Condensed Transcript:
{t}"""

FOCUS_GUIDES = {
    "viral": "Prioritize irresistible hooks, shocking revelations, and storytelling that keeps viewers glued.",
    "action": "Prioritize intense action, hype moments, clutch plays, and high energy reactions.",
    "funny": "Prioritize hilarious moments, comedy fails, infectious laughter, and funny unexpected dialogue.",
    "insight": "Prioritize eye-opening insights, life lessons, deep quotes, and thought-provoking statements.",
    "controversial": "Prioritize bold opinions, heated debates, counter-intuitive arguments, and polarizing statements."
}

def _condense_transcript(segs, target_blocks=60):
    # Condense hundreds of micro-segments into readable ~15-20s conversational blocks
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
        if (en - cur_start) > 20.0 or (st - cur_end) > 3.0:
            blocks.append(f"[{int(cur_start)}-{int(cur_end)}] {' '.join(cur_texts)}")
            cur_start = st
            cur_end = en
            cur_texts = [txt]
        else:
            cur_end = en
            cur_texts.append(txt)

    if cur_texts:
        blocks.append(f"[{int(cur_start)}-{int(cur_end)}] {' '.join(cur_texts)}")

    # If transcript is still huge, sample evenly to stay fast
    if len(blocks) > target_blocks:
        step = len(blocks) / float(target_blocks)
        sampled = [blocks[int(i * step)] for i in range(target_blocks)]
        return "\n".join(sampled)
    return "\n".join(blocks)

def _deduplicate_and_rank(candidates, count):
    candidates.sort(key=lambda c: int(c.get("score", 85)), reverse=True)
    selected = []

    for c in candidates:
        s, e = float(c.get("start", 0)), float(c.get("end", 0))
        if e <= s or (e - s) < 10:
            continue

        has_overlap = False
        for chosen in selected:
            cs, ce = float(chosen["start"]), float(chosen["end"])
            overlap_dur = max(0.0, min(e, ce) - max(s, cs))
            if overlap_dur > 8.0 or abs(s - cs) < 15.0:
                has_overlap = True
                break

        if not has_overlap:
            selected.append(c)

        if len(selected) >= count:
            break

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

    # Keep candidate count tight for high-speed AI response
    candidate_count = min(22, max(n + 3, 6))

    # Condense transcript so LLM finishes in 5-10s instead of hanging for 2 minutes
    t = _condense_transcript(segs, target_blocks=50)
    try:
        data = ask_json(PROMPT.format(candidate_count=candidate_count, lo=lo, hi=hi, focus_guide=focus_guide, t=t))
        raw_clips = data["clips"] if isinstance(data, dict) and "clips" in data else data
        valid = [c for c in raw_clips if lo - 8 <= (c.get("end", 0) - c.get("start", 0)) <= hi + 25]
        if valid:
            ranked = _deduplicate_and_rank(valid, n)
            if ranked:
                return ranked
    except Exception as e:
        print(f"AI moment selection notice: {e}. Using fast transcript windowing.")

    return _heuristic_clips(segs, count=n, min_len=lo, max_len=hi, focus=focus)
