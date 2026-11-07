import os, json, subprocess, requests
from config import WORK_DIR, WHISPER_MODEL, GROQ_KEY

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
    with open(audio_path, "rb") as f:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            files={"file": ("audio.mp3", f, "audio/mp3")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "verbose_json",
            },
            timeout=180,
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
    
    audio_path = _extract_audio(video, vid)
    out = None
    if GROQ_KEY:
        try:
            out = _transcribe_groq(audio_path)
        except Exception as e:
            print(f"Groq whisper failed: {e}, falling back to local")
    
    if not out:
        out = _transcribe_local(audio_path)
        
    if os.path.exists(audio_path):
        try: os.remove(audio_path)
        except Exception: pass
        
    json.dump(out, open(cache, "w", encoding="utf-8"))
    return out
