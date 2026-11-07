import os, yt_dlp
from config import WORK_DIR

def download(url):
    os.makedirs(WORK_DIR, exist_ok=True)
    opts = {"format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "outtmpl": f"{WORK_DIR}/%(id)s.%(ext)s",
            "merge_output_format": "mp4", "quiet": True}
    if os.getenv("YT_COOKIES"): opts["cookiefile"] = os.getenv("YT_COOKIES")  # helps when YouTube blocks servers
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        return os.path.splitext(y.prepare_filename(info))[0] + ".mp4", info["id"]
