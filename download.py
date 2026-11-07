import os, shutil, yt_dlp
from config import WORK_DIR

def _cookies():
    src = os.getenv("YT_COOKIES")
    if src and os.path.exists(src):
        shutil.copy(src, "/tmp/cookies.txt")
        return "/tmp/cookies.txt"

def download(url):
    os.makedirs(WORK_DIR, exist_ok=True)
    opts = {"format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "outtmpl": f"{WORK_DIR}/%(id)s.%(ext)s",
            "merge_output_format": "mp4", "quiet": True}
    c = _cookies()
    if c: opts["cookiefile"] = c
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        path = os.path.splitext(y.prepare_filename(info))[0] + ".mp4"
        return path, info["id"]
