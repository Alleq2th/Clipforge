import os
import json
import re
import subprocess
import glob
import requests
from config import WORK_DIR, WHISPER_MODEL, GROQ_KEY

_local_whisper_model = None

def _get_local_whisper():
    global _local_whisper_model
    if _local_whisper_model is None:
        from faster_whisper import WhisperModel
        print(f"[TRANSCRIBE] Initializing local faster-whisper model ({WHISPER_MODEL})...")
        _local_whisper_model = WhisperModel(WHISPER_MODEL, compute_type="int8")
    return _local_whisper_model

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
                        segments.append({
                            "start": round(st, 2),
                            "end": round(en, 2),
                            "text": clean_text,
                            "words": []
                        })
                    break
    except Exception as e:
        print(f"[TRANSCRIBE] Error parsing VTT: {e}")
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
            words = []
            cur_w_start = start
            for seg in ev["segs"]:
                w_txt = seg.get("utf8", "").strip()
                t_offset = float(seg.get("tOffsetMs", 0)) / 1000.0
                if w_txt:
                    words.append({
                        "word": w_txt,
                        "start": round(start + t_offset, 2),
                        "end": round(start + t_offset + 0.35, 2)
                    })
            if text and text != "\n":
                segments.append({
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "text": text,
                    "words": words
                })
    except Exception as e:
        print(f"[TRANSCRIBE] Error parsing JSON3: {e}")
    return segments

def _find_instant_subtitles(search_dir):
    patterns = [
        os.path.join(search_dir, "*.vtt"),
        os.path.join(search_dir, "*.json3"),
    ]
    for p in patterns:
        matches = glob.glob(p)
        for m in matches:
            if m.endswith(".vtt"):
                segs = _parse_vtt(m)
                if segs and len(segs) >= 3:
                    return segs
            elif m.endswith(".json3"):
                segs = _parse_json3(m)
                if segs and len(segs) >= 3:
                    return segs
    return None

def _extract_audio(video, target_dir):
    os.makedirs(target_dir, exist_ok=True)
    audio_path = os.path.join(target_dir, "extracted_audio.mp3")
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
    if os.path.getsize(audio_path) > 24 * 1024 * 1024:
        print("[TRANSCRIBE] Audio exceeds Groq 24MB limit, falling back to local model.")
        return None
    with open(audio_path, "rb") as f:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            files={"file": ("audio.mp3", f, "audio/mp3")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["segment", "word"],
            },
            timeout=120,
        )
    r.raise_for_status()
    data = r.json()
    segments = data.get("segments", [])
    raw_words = data.get("words", [])

    if not segments and data.get("text"):
        return [{"start": 0.0, "end": 30.0, "text": data["text"].strip(), "words": []}]

    out = []
    for s in segments:
        st = float(s["start"])
        en = float(s["end"])
        seg_words = [
            {"word": w.get("word", "").strip(), "start": round(float(w.get("start", st)), 2), "end": round(float(w.get("end", en)), 2)}
            for w in raw_words
            if st <= float(w.get("start", st)) <= en
        ]
        out.append({
            "start": round(st, 2),
            "end": round(en, 2),
            "text": s["text"].strip(),
            "words": seg_words
        })
    return out

def _transcribe_local(audio_path):
    m = _get_local_whisper()
    segs, _ = m.transcribe(audio_path, vad_filter=True, word_timestamps=True)
    out = []
    for s in segs:
        words = []
        if hasattr(s, "words") and s.words:
            for w in s.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 2),
                    "end": round(w.end, 2)
                })
        out.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "words": words
        })
    return out

def transcribe(video, vid, work_dir=None, out_dir=None, check_cancelled=None):
    job_work = work_dir or WORK_DIR
    cache = os.path.join(out_dir or job_work, "transcript.json")

    # If cached transcript already exists, return it instantly
    if os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if cached and len(cached) > 0:
                    print(f"[TRANSCRIBE] Loaded cached transcript with {len(cached)} segments.")
                    return cached
        except Exception:
            pass

    if check_cancelled and check_cancelled():
        raise RuntimeError("Cancelled by user before transcription")

    # 1. Check for Instant Subtitles from yt-dlp
    instant_segs = _find_instant_subtitles(job_work)
    if instant_segs and len(instant_segs) >= 5:
        try:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(instant_segs, f, indent=2)
        except Exception:
            pass
        return instant_segs

    # 2. Extract audio
    audio_path = _extract_audio(video, job_work)

    if check_cancelled and check_cancelled():
        raise RuntimeError("Cancelled by user during audio extraction")

    # 3. Transcribe via Groq (Fast Cloud) or Local Whisper
    out = None
    if GROQ_KEY:
        try:
            out = _transcribe_groq(audio_path)
        except Exception as e:
            print(f"[TRANSCRIBE] Groq Whisper notice: {e}, falling back to local model.")

    if not out:
        out = _transcribe_local(audio_path)

    # Clean temporary audio file to preserve disk space
    if os.path.exists(audio_path):
        try: os.remove(audio_path)
        except Exception: pass

    # Persist transcript in out_dir so any retries never have to re-transcribe
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
    except Exception as e:
        print(f"[TRANSCRIBE] Notice saving transcript cache: {e}")

    return out
