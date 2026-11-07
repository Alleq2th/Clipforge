from config import *
from llm import ask_json

PROMPT = """You are a viral short-form video editor. From this timestamped transcript, pick the {n} best self-contained moments ({lo}-{hi} seconds each) with a strong hook, emotion, or insight. Clips must not overlap and must start/end at sentence boundaries.
Return ONLY JSON: {{"clips":[{{"start":sec,"end":sec,"title":"...","caption":"...","hashtags":["#a","#b"]}}]}}
Transcript:
{t}"""

def _heuristic_clips(segs):
    if not segs:
        return []
    total_duration = max(s.get("end", 0) for s in segs)
    if total_duration < MIN_LEN:
        return [{
            "start": 0.0,
            "end": max(float(total_duration), 5.0),
            "title": "Highlight",
            "caption": "Top moment from this clip",
            "hashtags": ["#shorts", "#viral"]
        }]
    target_count = min(CLIP_COUNT, max(1, int(total_duration // MIN_LEN)))
    clip_len = 35.0
    step = max(clip_len, (total_duration - clip_len) / max(1, target_count))
    clips = []
    for i in range(target_count):
        st = max(0.0, i * step + (5.0 if i > 0 else 2.0))
        en = min(total_duration, st + clip_len)
        if en <= st or en - st < MIN_LEN - 5:
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

def pick(segs):
    t = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in segs)
    try:
        data = ask_json(PROMPT.format(n=CLIP_COUNT, lo=MIN_LEN, hi=MAX_LEN, t=t))
        clips = data["clips"] if isinstance(data, dict) and "clips" in data else data
        valid = [c for c in clips if MIN_LEN - 5 <= c["end"] - c["start"] <= MAX_LEN + 15]
        if valid:
            return valid
    except Exception as e:
        print(f"AI moment selection notice: {e}. Using transcript windowing fallback.")
    return _heuristic_clips(segs)
