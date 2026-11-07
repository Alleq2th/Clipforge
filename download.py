import os, shutil, yt_dlp
from config import WORK_DIR

def _cookies():
    src = os.getenv("YT_COOKIES")
    if not src:
        return None, "COOKIES: YT_COOKIES variable is not set"
    if not os.path.exists(src):
        return None, f"COOKIES: file not found at {src}"
    shutil.copy(src, "/tmp/cookies.txt")
    n = sum("youtube.com" in l for l in open("/tmp/cookies.txt", errors="ignore"))
    return "/tmp/cookies.txt", f"COOKIES: loaded {n} youtube cookies"

def download(url):
    os.makedirs(WORK_DIR, exist_ok=True)
    opts = {"format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "outtmpl": f"{WORK_DIR}/%(id)s.%(ext)s",
            "merge_output_format": "mp4", "quiet": True}
    c, status = _cookies()
    if c: opts["cookiefile"] = c
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
            path = os.path.splitext(y.prepare_filename(info))[0] + ".mp4"
            return path, info["id"]
    except Exception as e:
        raise RuntimeError(f"{status}. {str(e)[:140]}") from None
        
