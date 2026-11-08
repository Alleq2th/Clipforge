import os, shutil, yt_dlp
from config import WORK_DIR

def _cookies():
    dst = "/tmp/cookies.txt"
    txt = os.getenv("YT_COOKIES_TEXT")
    if txt:
        open(dst, "w").write(txt.replace("\\n", "\n"))
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
        shutil.copy(src, dst)
    n = sum("youtube.com" in l for l in open(dst, errors="ignore"))
    return dst, f"COOKIES: loaded {n} youtube cookies"

def download(url):
    os.makedirs(WORK_DIR, exist_ok=True)
    # Download 720p maximum to accelerate processing + extract subtitles instantly
    opts = {
        "format": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "outtmpl": f"{WORK_DIR}/%(id)s.%(ext)s",
        "merge_output_format": "mp4",
        "remote_components": ["ejs:github"],
        "quiet": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB", "a.en", "en-orig"],
        "subtitlesformat": "vtt/json3/srt",
    }
    c, status = _cookies()
    if c:
        opts["cookiefile"] = c
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
            path = os.path.splitext(y.prepare_filename(info))[0] + ".mp4"
            return path, info["id"]
    except Exception as e:
        raise RuntimeError(f"{status}. {str(e)[:140]}") from None
