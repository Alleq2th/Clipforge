import os
import shutil
import yt_dlp
from config import WORK_DIR
from security import validate_video_url

def _cookies():
    dst = "/tmp/cookies.txt"
    txt = os.getenv("YT_COOKIES_TEXT")
    if txt:
        try:
            with open(dst, "w") as f:
                f.write(txt.replace("\\n", "\n"))
        except Exception:
            pass
    else:
        paths = [
            p for p in (
                os.getenv("YT_COOKIES"),
                "/etc/secrets/cookies.txt",
                "cookies.txt",
                "/app/cookies.txt",
                "/etc/secrets/cookies"
            ) if p
        ]
        src = next((p for p in paths if os.path.isfile(p)), None)
        if not src:
            return None, "COOKIES: file not found"
        try:
            shutil.copy(src, dst)
        except Exception:
            pass
    try:
        n = sum("youtube.com" in l for l in open(dst, errors="ignore"))
        return dst, f"COOKIES: loaded {n} youtube cookies"
    except Exception:
        return None, "COOKIES: unreadable"

def download(url, dest_dir=None, max_duration=None, check_cancelled=None):
    """
    Downloads video cleanly into dest_dir, avoiding filename collisions across concurrent jobs.
    Validates URL against SSRF and illegal protocols.
    """
    # 1. Validate video URL against SSRF
    validate_video_url(url)

    target_dir = dest_dir or WORK_DIR
    os.makedirs(target_dir, exist_ok=True)

    if check_cancelled and check_cancelled():
        raise RuntimeError("Cancelled by user before download")

    opts = {
        "format": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "outtmpl": os.path.join(target_dir, "source_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "remote_components": ["ejs:github"],
        "quiet": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB", "a.en", "en-orig"],
        "subtitlesformat": "vtt/json3/srt",
    }

    if max_duration:
        try:
            import yt_dlp.utils
            opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(0, float(max_duration))])
            opts["force_keyframes_at_cuts"] = True
        except Exception as e:
            print(f"[DOWNLOAD] Notice setting download_ranges: {e}")

    c, status = _cookies()
    if c:
        opts["cookiefile"] = c

    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
            if not info:
                raise RuntimeError("Failed to extract video information from URL.")
            
            vid = info.get("id", "video")
            # Locate downloaded file in target_dir
            cand = os.path.splitext(y.prepare_filename(info))[0] + ".mp4"
            if os.path.exists(cand):
                return cand, vid
            
            # Fallback search inside target_dir
            for f in os.listdir(target_dir):
                if f.startswith("source_") and f.endswith(".mp4"):
                    return os.path.join(target_dir, f), vid
            
            raise RuntimeError("Downloaded video file was not found on disk.")
    except Exception as e:
        if check_cancelled and check_cancelled():
            raise RuntimeError("Cancelled by user")
        raise RuntimeError(f"Download error: {str(e)[:160]}") from None
