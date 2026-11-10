"""Stage 1 - ingest.

Accepts either a local file path or a URL. URLs go through yt-dlp, which covers
YouTube, TikTok, Twitch, Instagram, X, Facebook, Rumble, Vimeo and anything else
yt-dlp supports. Local paths are used as-is so the pipeline can be tested
without touching the network.
"""
import os
import shutil
import subprocess

from config import WORK_DIR
from security import validate_video_url

# 1080p is the sweet spot: enough detail for a 1080x1920 vertical crop, small
# enough that a 1-hour source downloads in a couple of minutes.
MAX_SOURCE_HEIGHT = 1080


def _cookies():
    """Returns a path to a cookies file if one is configured, else None."""
    dst = "/tmp/clipforge_cookies.txt"
    txt = os.getenv("YT_COOKIES_TEXT")
    if txt:
        try:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(txt.replace("\\n", "\n"))
            return dst
        except OSError:
            return None

    for p in (
        os.getenv("YT_COOKIES"),
        "/etc/secrets/cookies.txt",
        "cookies.txt",
        "/app/cookies.txt",
    ):
        if p and os.path.isfile(p):
            try:
                shutil.copy(p, dst)
                return dst
            except OSError:
                return None
    return None


def probe(path):
    """Returns {duration, width, height, fps} for a media file, or {} on failure."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate:format=duration",
                "-of", "default=noprint_wrappers=1",
                path,
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}

    info = {}
    for line in out.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        info[k.strip()] = v.strip()

    def _num(key, default=0.0):
        try:
            return float(info.get(key, default))
        except (TypeError, ValueError):
            return default

    fps = 30.0
    rate = info.get("r_frame_rate", "")
    if "/" in rate:
        try:
            num, den = rate.split("/")
            if float(den):
                fps = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            pass

    return {
        "duration": _num("duration"),
        "width": int(_num("width")),
        "height": int(_num("height")),
        "fps": round(fps, 3) or 30.0,
    }


def _download(url, dest_dir, check_cancelled=None):
    import yt_dlp

    os.makedirs(dest_dir, exist_ok=True)
    # Prefer H.264 (avc1). YouTube increasingly defaults to AV1/VO9, which many
    # ffmpeg builds cannot decode without a hardware path - that shows up as
    # "Failed to get pixel format" and a dead render. H.264 always decodes.
    opts = {
        "format": (
            f"bv*[height<={MAX_SOURCE_HEIGHT}][vcodec^=avc1]+ba[ext=m4a]/"
            f"bv*[height<={MAX_SOURCE_HEIGHT}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={MAX_SOURCE_HEIGHT}]+ba/"
            f"b[height<={MAX_SOURCE_HEIGHT}]/bv*+ba/b"
        ),
        "outtmpl": os.path.join(dest_dir, "source_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Subtitles are a free, instant transcript when the platform has them.
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB", "a.en", "en-orig"],
        "subtitlesformat": "json3/vtt/srt",
    }
    ck = _cookies()
    if ck:
        opts["cookiefile"] = ck

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise RuntimeError("Could not read that URL.")

        vid = info.get("id") or "video"
        candidate = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp4"
        if os.path.exists(candidate):
            path = candidate
        else:
            found = [
                os.path.join(dest_dir, f)
                for f in os.listdir(dest_dir)
                if f.startswith("source_") and f.endswith(".mp4")
            ]
            if not found:
                raise RuntimeError("Download finished but no video file was written.")
            path = found[0]

    return path, {
        "id": str(vid),
        "title": (info.get("title") or "Untitled video").strip(),
        "author": (info.get("uploader") or info.get("channel") or "").strip(),
        "thumbnail": info.get("thumbnail") or "",
        "platform": (info.get("extractor_key") or "Web").strip(),
        "webpage_url": info.get("webpage_url") or url,
    }


def ingest(source, dest_dir=None, check_cancelled=None):
    """Resolves a source into a local video file plus metadata.

    Args:
        source: an http(s) URL, or a path to a local video file.
        dest_dir: where downloads land. Defaults to WORK_DIR/ingest.
        check_cancelled: optional callable returning True to abort.

    Returns:
        dict with path, id, title, author, thumbnail, platform, duration,
        width, height, fps, source.
    """
    if not source or not str(source).strip():
        raise ValueError("No video source given.")
    source = str(source).strip()

    if check_cancelled and check_cancelled():
        raise RuntimeError("Cancelled before ingest.")

    dest_dir = dest_dir or os.path.join(WORK_DIR, "ingest")

    if os.path.isfile(source):
        path = source
        meta = {
            "id": os.path.splitext(os.path.basename(source))[0],
            "title": os.path.basename(source),
            "author": "",
            "thumbnail": "",
            "platform": "File",
            "webpage_url": "",
        }
    else:
        validate_video_url(source)
        path, meta = _download(source, dest_dir, check_cancelled=check_cancelled)

    if check_cancelled and check_cancelled():
        raise RuntimeError("Cancelled after ingest.")

    meta.update(probe(path))
    meta["path"] = path
    meta["source"] = source
    if not meta.get("duration"):
        raise RuntimeError("That file has no readable video stream.")
    if not meta.get("width") or not meta.get("height"):
        raise RuntimeError(
            "That file has no video track - it looks like audio only. "
            "ClipForge needs a video to cut from."
        )
    return meta
