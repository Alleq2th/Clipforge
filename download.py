def download(url):
    os.makedirs(WORK_DIR, exist_ok=True)
    opts = {
        "format": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best",
        "outtmpl": f"{WORK_DIR}/%(id)s.%(ext)s",
        "merge_output_format": "mp4",
        "quiet": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["all"]
            }
        },
    }
    c, status = _cookies()
    if c: opts["cookiefile"] = c
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
            path = os.path.splitext(y.prepare_filename(info))[0] + ".mp4"
            return path, info["id"]
    except Exception as e:
        raise RuntimeError(f"{status}. {str(e)[:140]}") from None
        
