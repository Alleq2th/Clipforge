from config import *
from llm import ask_json

PROMPT = """You are a viral short-form video editor. From this timestamped transcript, pick the {n} best self-contained moments ({lo}-{hi} seconds each) with a strong hook, emotion, or insight. Clips must not overlap and must start/end at sentence boundaries.
Return ONLY JSON: {{"clips":[{{"start":sec,"end":sec,"title":"...","caption":"...","hashtags":["#a","#b"]}}]}}
Transcript:
{t}"""

def pick(segs):
    t = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in segs)
    data = ask_json(PROMPT.format(n=CLIP_COUNT, lo=MIN_LEN, hi=MAX_LEN, t=t))
    clips = data["clips"] if isinstance(data, dict) else data
    return [c for c in clips if MIN_LEN - 5 <= c["end"] - c["start"] <= MAX_LEN + 15]
