import os, json, re, subprocess, glob, requests
from config import WORK_DIR, WHISPER_MODEL, GROQ_KEY

def _parse_vtt(vtt_path):
    time_re = re.compile(r"(\d{2}:)?(\d{2}):(\d{2})[\.,](\d{3})\s*-->\s*(\d{2}:)?(\d{2}):(\d{2})[\.,](\d{3})")
    def to_secs(h, m, s, ms):
        hours = int(h[:-1]) if h else 0
        return hours * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    segments = []
    try:
        with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
            blocks = f.read().split("\n\n")
        for b in blocks:
            lines = [l.strip() for l in b.split("\n") if l.strip()]
            for idx, line in enumerate(lines):
                m = time_re.search(line)
                if m:
                    st = to_secs(m.group(1), m.group(2), m.group(3), m.group(4))
                    en = to_secs(m.group(5), m.group(6), m.group(7), m.group(8))
                    text_lines = lines[idx+1:]
                    clean_text = " ".join(re.sub(r"<[^>]+>", "", l) for l in text_lines).strip()
                    if clean_text:
                        segments.append({"start": round(st, 2), "end": round(en, 2), "text": clean_text})
                    break
    except Exception as e:
        print(f"Error parsing VTT: {e}")
    return segments

def _parse_json3(json3_path):
    segments = []
    try:
        with open(json3_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        for ev in data.get("events", []):
            if "segs" not in ev:
                continue
            start = float(ev.get("tStartMs", 0)) / 1000.0
            dur = float(ev.get("dDurationMs", 0)) / 1000.0
            end = start + dur
            text = "".join(s.get("utf8", "") for s in ev["segs"]).strip()
            if text and text != "\n":
                segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    except Exception as e:
        print(f"Error parsing JSON3: {e}")
    return segments

def _find_instant_subtitles(vid):
    # Search for downloaded YouTube subtitle files (.vtt or .json3)
    patterns = [
        f"{WORK_DIR}/{vid}*.vtt",
        f"{WORK_DIR}/{vid}*.json3",
        f"{WORK_DIR}/*{vid}*.vtt",
        f"{WORK_DIR}/*{vid}*.json3"
    ]
    for p in patterns:
        matches = glob.glob(p)
        for m in matches:
            if m.endswith(".vtt"):
                segs = _parse_vtt(m)
                if segs: return segs
            elif m.endswith(".json3"):
                segs = _parse_json3(m)
                if segs: return segs
    return None

def _extract_audio(video, vid):
    os.makedirs(WORK_DIR, exist_ok=True)
    audio_path = f"{WORK_DIR}/{vid}.mp3"
    if not os.path.exists(audio_path):
        cmd = [
            "ffmpeg", "-y", "-i", os.path.abspath(video),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
            "-f", "mp3", os.path.abspath(audio_path)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return audio_path

def _transcribe_groq(audio_path):
    if not GROQ_KEY:
        return None
    # If audio is > 24MB, Groq API rejects it. In that case, return None to handle gracefully
    if os.path.getsize(audio_path) > 24 * 1024 * 1024:
        print("Audio exceeds Groq 25MB limit")
        return None
    with open(audio_path, "rb") as f:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            files={"file": ("audio.mp3", f, "audio/mp3")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "verbose_json",
            },
            timeout=120,
        )
    r.raise_for_status()
    data = r.json()
    segments = data.get("segments", [])
    if not segments and data.get("text"):
        return [{"start": 0.0, "end": 30.0, "text": data["text"].strip()}]
    return [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()} for s in segments]

def _transcribe_local(audio_path):
    from faster_whisper import WhisperModel
    m = WhisperModel(WHISPER_MODEL, compute_type="int8")
    segs, _ = m.transcribe(audio_path, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs]

def transcribe(video, vid):
    cache = f"{WORK_DIR}/{vid}.json"
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))

    # 1. Check for Instant Subtitles (0.01 seconds) - 95%+ of YouTube videos have these!
    instant_segs = _find_instant_subtitles(vid)
    if instant_segs and len(instant_segs) >= 5:
        json.dump(instant_segs, open(cache, "w", encoding="utf-8"))
        return instant_segs

    # 2. Fast cloud transcription via Groq Whisper (5-10 seconds)
    audio_path = _extract_audio(video, vid)
    out = None
    if GROQ_KEY:
        try:
            out = _transcribe_groq(audio_path)
        except Exception as e:
            print(f"Groq whisper notice: {e}, falling back")

    # 3. Local Whisper model fallback
    if not out:
        out = _transcribe_local(audio_path)

    if os.path.exists(audio_path):
        try: os.remove(audio_path)
        except Exception: pass

    json.dump(out, open(cache, "w", encoding="utf-8"))
    return out
