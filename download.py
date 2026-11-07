import os, shutil, yt_dlp
from config import WORK_DIR

def _cookies():
    dst = "/tmp/cookies.txt"
    txt = os.getenv("YT_COOKIES_TEXT")  # fallback: cookies pasted straight into an env var
    if txt:
        open(dst, "w").write(txt.replace("\\n", "\n"))
    else:
        paths = [p for p in (os.getenv("YT_COOKIES"), "/etc/secrets/cookies.txt", "cookies.txt",
                             "/app/cookies.txt", "/etc/secrets/cookies") if p]
        src = next((p for p in paths if os.path.isfile(p)), None)
        if not src:
            def ls(d):
                try: return os.listdir(d)
                except Exception as e: return f"unreadable({type(e).__name__})"
            return None, f"COOKIES: not found. /etc/secrets={ls('/etc/secrets')} cwd={os.getcwd()} files={[f for f in ls('.') if 'cook' in f.lower()]}"
        shutil.copy(src, dst)
    n = sum("youtube.com" in l for l in open(dst, errors="ignore"))
    return dst, f"COOKIES: loaded {n} youtube cookies"

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
            
