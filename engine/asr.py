"""Stage 2 - speech recognition.

Produces word-level timestamps, which everything downstream depends on:
caption sync, cut-point snapping, and karaoke timing. Segment-level timestamps
are not good enough - they drift by up to a second inside a sentence.

Two backends, chosen automatically:
  * Groq whisper-large-v3-turbo  - cloud, ~10x faster, needs GROQ_API_KEY
  * faster-whisper               - local CPU, always available

Platform subtitles (YouTube json3/vtt) are used when present because they are
free and instant, but only if they carry word-level timing.
"""
import glob
import json
import os
import re
import subprocess

from config import (
    ASR_PREFER_CLOUD,
    GROQ_KEY,
    WHISPER_BEAM,
    WHISPER_COMPUTE,
    WHISPER_MODEL,
)

_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel
        _local_model = WhisperModel(WHISPER_MODEL, compute_type=WHISPER_COMPUTE)
    return _local_model


def _has_audio(video):
    """True if the file has at least one audio stream."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", video],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return bool(out)
    except (subprocess.SubprocessError, OSError):
        return False


def _extract_audio(video, work_dir):
    """16 kHz mono mp3 - what every ASR backend wants, and small enough to upload."""
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(work_dir, "audio.mp3")
    if os.path.exists(out) and os.path.getsize(out) > 1024:
        return out
    if not _has_audio(video):
        raise RuntimeError(
            "This video has no audio track, so there is no speech to transcribe. "
            "ClipForge needs spoken audio to find moments."
        )
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin", "-i", os.path.abspath(video),
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                "-f", "mp3", os.path.abspath(out),
            ],
            check=True, capture_output=True, timeout=1800,
        )
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"Could not extract audio: {tail}") from None
    return out


# ------------------------------------------------------------------ subtitles
_TIME_RE = re.compile(
    r"(\d{2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}:)?(\d{2}):(\d{2})[.,](\d{3})"
)


def _secs(h, m, s, ms):
    return (int(h[:-1]) if h else 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_vtt(path):
    segments = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return []

    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            m = _TIME_RE.search(line)
            if not m:
                continue
            start = _secs(m.group(1), m.group(2), m.group(3), m.group(4))
            end = _secs(m.group(5), m.group(6), m.group(7), m.group(8))
            text = " ".join(re.sub(r"<[^>]+>", "", l) for l in lines[i + 1:]).strip()
            if text:
                segments.append({"start": round(start, 3), "end": round(end, 3),
                                 "text": text, "words": []})
            break
    return segments


def _parse_json3(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []

    segments = []
    for ev in data.get("events", []):
        if "segs" not in ev:
            continue
        start = float(ev.get("tStartMs", 0)) / 1000.0
        end = start + float(ev.get("dDurationMs", 0)) / 1000.0
        text = "".join(s.get("utf8", "") for s in ev["segs"]).strip()
        words = []
        for seg in ev["segs"]:
            w = seg.get("utf8", "").strip()
            if not w:
                continue
            ws = start + float(seg.get("tOffsetMs", 0)) / 1000.0
            words.append({"word": w, "start": round(ws, 3), "end": round(ws + 0.3, 3)})
        if text and text != "\n":
            segments.append({"start": round(start, 3), "end": round(end, 3),
                             "text": text, "words": words})
    return segments


def _platform_subtitles(work_dir):
    """Returns word-timed segments from downloaded subtitles, or None."""
    for pattern, parser in (("*.json3", _parse_json3), ("*.vtt", _parse_vtt)):
        for path in sorted(glob.glob(os.path.join(work_dir, pattern))):
            segs = parser(path)
            if len(segs) >= 5 and any(s.get("words") for s in segs):
                return segs
    return None


# ---------------------------------------------------------------------- cloud
def _transcribe_groq(audio_path):
    import requests

    if not GROQ_KEY:
        return None
    if os.path.getsize(audio_path) > 24 * 1024 * 1024:
        return None  # caller falls back to local

    with open(audio_path, "rb") as f:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            files={"file": ("audio.mp3", f, "audio/mpeg")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["segment", "word"],
            },
            timeout=300,
        )
    r.raise_for_status()
    data = r.json()

    raw_words = data.get("words") or []
    out = []
    for s in data.get("segments") or []:
        st, en = float(s["start"]), float(s["end"])
        words = [
            {"word": w.get("word", "").strip(),
             "start": round(float(w.get("start", st)), 3),
             "end": round(float(w.get("end", en)), 3)}
            for w in raw_words
            if st - 0.05 <= float(w.get("start", st)) <= en + 0.05
        ]
        out.append({"start": round(st, 3), "end": round(en, 3),
                    "text": (s.get("text") or "").strip(), "words": words})

    if not out and data.get("text"):
        out = [{"start": 0.0, "end": 30.0, "text": data["text"].strip(), "words": []}]
    return out


# ---------------------------------------------------------------------- local
def _transcribe_local(audio_path):
    model = _get_local_model()
    segs, _info = model.transcribe(
        audio_path,
        vad_filter=True,
        word_timestamps=True,
        beam_size=WHISPER_BEAM,
        condition_on_previous_text=False,
    )
    out = []
    for s in segs:
        words = [
            {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
            for w in (getattr(s, "words", None) or [])
            if w.word and w.word.strip()
        ]
        out.append({"start": round(s.start, 3), "end": round(s.end, 3),
                    "text": (s.text or "").strip(), "words": words})
    return out


def transcribe(video, work_dir, out_dir=None, check_cancelled=None, force=False):
    """Returns {segments, language, source, duration}.

    Cached in out_dir/transcript.json so a retry never re-transcribes.
    """
    cache = os.path.join(out_dir or work_dir, "transcript.json")
    if not force and os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("segments"):
                return cached
        except (OSError, ValueError):
            pass

    if check_cancelled and check_cancelled():
        raise RuntimeError("Cancelled before transcription.")

    segments = _platform_subtitles(work_dir)
    source = "platform-subtitles"

    if not segments:
        audio = _extract_audio(video, work_dir)
        if check_cancelled and check_cancelled():
            raise RuntimeError("Cancelled during audio extraction.")

        segments = None
        if ASR_PREFER_CLOUD and GROQ_KEY:
            try:
                segments = _transcribe_groq(audio)
                source = "groq-whisper-large-v3-turbo"
            except Exception as e:  # noqa: BLE001 - any failure means fall back
                print(f"[ASR] cloud failed ({e}); using local model")
                segments = None

        if not segments:
            segments = _transcribe_local(audio)
            source = f"faster-whisper-{WHISPER_MODEL}"

        try:
            os.remove(audio)
        except OSError:
            pass

    if not segments:
        raise RuntimeError("No speech could be transcribed from this video.")

    result = {
        "segments": segments,
        "source": source,
        "language": "en",
        "duration": round(max(s["end"] for s in segments), 3),
        "word_count": sum(len(s.get("words") or []) for s in segments),
    }

    try:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(result, f)
    except OSError:
        pass

    return result
