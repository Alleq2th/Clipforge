import os, json
from faster_whisper import WhisperModel
from config import WORK_DIR, WHISPER_MODEL

def transcribe(video, vid):
    cache = f"{WORK_DIR}/{vid}.json"
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))
    m = WhisperModel(WHISPER_MODEL, compute_type="int8")
    segs, _ = m.transcribe(video, vad_filter=True)
    out = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs]
    json.dump(out, open(cache, "w", encoding="utf-8"))
    return out
