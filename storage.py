import os, shutil, time
from config import WORK_DIR, OUT_DIR

def get_dir_size_mb(path):
    if not os.path.exists(path):
        return 0.0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except Exception:
                    pass
    return total / (1024 * 1024)

def cleanup_source_video(video_path):
    """
    Deletes raw full-length downloaded video immediately after clips are cut.
    This frees up 300MB - 1GB instantly to protect Render's ephemeral disk.
    """
    if video_path and os.path.exists(video_path):
        try:
            os.remove(video_path)
            print(f"Cleaned source video {video_path} to conserve storage.")
        except Exception as e:
            print(f"Notice cleaning source video: {e}")

def cleanup_storage(max_mb=400, max_saved_jobs=6):
    """
    Prevents storage crashes on Render free tier:
    1. Removes any stray .mp4, .mp3, or .part files in WORK_DIR older than 5 minutes.
    2. Prunes oldest output video folders if total storage exceeds max_mb or max_saved_jobs.
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    now = time.time()
    # 1. Clean stale work files
    try:
        for f in os.listdir(WORK_DIR):
            fp = os.path.join(WORK_DIR, f)
            if os.path.isfile(fp):
                ext = f.lower()
                if ext.endswith((".mp4", ".mp3", ".part", ".ytdl")):
                    if now - os.path.getmtime(fp) > 300:
                        try: os.remove(fp)
                        except Exception: pass
    except Exception as e:
        print(f"Notice during work cleanup: {e}")

    # 2. Prune old outputs if exceeding budget
    try:
        vids = [
            os.path.join(OUT_DIR, d) for d in os.listdir(OUT_DIR)
            if os.path.isdir(os.path.join(OUT_DIR, d))
        ]
        vids.sort(key=lambda p: os.path.getmtime(p))

        total_mb = get_dir_size_mb(OUT_DIR) + get_dir_size_mb(WORK_DIR)
        while (len(vids) > max_saved_jobs or total_mb > max_mb) and vids:
            oldest = vids.pop(0)
            try:
                shutil.rmtree(oldest)
                print(f"Pruned oldest video directory {oldest} to preserve storage.")
            except Exception:
                pass
            total_mb = get_dir_size_mb(OUT_DIR) + get_dir_size_mb(WORK_DIR)
    except Exception as e:
        print(f"Notice during output pruning: {e}")
