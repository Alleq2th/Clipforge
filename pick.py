from config import *
from llm import ask_json

PROMPT = """You are a viral short-form video editor for TikTok, YouTube Shorts, and Instagram Reels.
Task: From this timestamped transcript, identify and extract the {n} best self-contained moments ({lo}-{hi} seconds each).
Guideline: {focus_guide}
Requirements:
1. Each clip MUST have a compelling hook within the first 3 seconds and an emotional or exciting payoff.
2. Clips must not overlap.
3. Start and end cleanly at sentence boundaries.
Return ONLY valid JSON matching this schema:
{{"clips":[{{"start":12.5,"end":45.0,"title":"Catchy 4-7 word title","caption":"1-2 engaging sentences describing the clip","hashtags":["#tag1","#tag2","#viral"]}}]}}

Transcript:
{t}"""

FOCUS_GUIDES = {
    "action": "Prioritize intense action, hype gameplay moments, clutch plays, high energy reactions, and explosive drama.",
    "funny": "Prioritize hilarious moments, comedy fails, infectious laughter, jokes, and funny unexpected dialogue.",
    "insight": "Prioritize eye-opening insights, life lessons, deep quotes, expert tips, and thought-provoking statements.",
    "viral": "Prioritize irresistible hooks, shocking revelations, high tension, and storytelling clips that keep viewers glued."
}

def _heuristic_clips(segs, count=5, min_len=20, max_len=60):
    if not segs:
        return []
    total_duration = max(s.get("end", 0) for s in segs)
    if total_duration < min_len:
        return [{
            "start": 0.0,
            "end": max(float(total_duration), 5.0),
            "title": "Top Moment Highlight",
            "caption": "Top moment from this clip",
            "hashtags": ["#shorts", "#viral", "#gaming"]
        }]
    target_count = min(count, max(1, int(total_duration // min_len)))
    clip_len = min(max_len, max(min_len, 35.0))
    step = max(clip_len, (total_duration - clip_len) / max(1, target_count))
    clips = []
    for i in range(target_count):
        st = max(0.0, i * step + (5.0 if i > 0 else 2.0))
        en = min(total_duration, st + clip_len)
        if en <= st or en - st < min_len - 5:
            continue
        sub = [s for s in segs if st <= s.get("start", 0) <= en]
        title_text = sub[0]["text"][:40] if sub else f"Highlight #{i+1}"
        clips.append({
            "start": round(st, 1),
            "end": round(en, 1),
            "title": title_text.strip() or f"Highlight #{i+1}",
            "caption": "Must-watch moment from this video",
            "hashtags": ["#shorts", "#clip", "#viral"]
        })
    return clips

def pick(segs, count=None, min_len=None, max_len=None, focus="viral"):
    n = count or CLIP_COUNT
    lo = min_len or MIN_LEN
    hi = max_len or MAX_LEN
    focus_guide = FOCUS_GUIDES.get(focus, FOCUS_GUIDES["viral"])

    t = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in segs)
    try:
        data = ask_json(PROMPT.format(n=n, lo=lo, hi=hi, focus_guide=focus_guide, t=t))
        clips = data["clips"] if isinstance(data, dict) and "clips" in data else data
        valid = [c for c in clips if lo - 5 <= c["end"] - c["start"] <= hi + 20]
        if valid:
            return valid[:n]
    except Exception as e:
        print(f"AI moment selection notice: {e}. Using transcript windowing fallback.")
    return _heuristic_clips(segs, count=n, min_len=lo, max_len=hi)
