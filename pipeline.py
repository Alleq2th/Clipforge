import os
from download import download
from transcribe import transcribe
from pick import pick
from cut import cut
from storage import cleanup_source_video, cleanup_storage

def run(url, cb=print, on_clip=None, options=None, check_cancelled=None):
    opts = options or {}
    count = int(opts.get("count", 5))
    min_len = int(opts.get("min_len", 20))
    max_len = int(opts.get("max_len", 60))
    focus = opts.get("focus", "viral")
    aspect_ratio = opts.get("aspect_ratio", "9:16")
    caption_style = opts.get("caption_style", "beasty")
    framing = opts.get("framing", "crop")

    # Proactively free space before starting new download
    cleanup_storage()

    cb("Downloading video (optimizing 720p)...")
    video, vid = download(url)

    if check_cancelled and check_cancelled():
        cleanup_source_video(video)
        cb("Cancelled by user")
        return []

    try:
        cb("Transcribing audio...")
        segs = transcribe(video, vid)

        if check_cancelled and check_cancelled():
            cb("Cancelled by user")
            return []

        cb(f"AI discovering & ranking {count} viral moments...")
        clips = pick(segs, count=count, min_len=min_len, max_len=max_len, focus=focus)

        out = []
        total = len(clips)
        for i, c in enumerate(clips, 1):
            if check_cancelled and check_cancelled():
                cb("Cancelled by user")
                break

            cb(f"Cutting clip {i} of {total} ({aspect_ratio} • {framing} framing)...")
            try:
                f = cut(video, segs, c, i, vid, aspect_ratio=aspect_ratio, caption_style=caption_style, framing=framing)
                item = {**c, "file": os.path.basename(f), "vid": vid, "index": i, "ok": True}
                out.append(item)
                if on_clip:
                    try:
                        on_clip(item, i, total)
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error cutting clip {i}: {e}")
                err_item = {**c, "file": "", "vid": vid, "index": i, "ok": False, "error": str(e)}
                out.append(err_item)

        cb("Done" if not (check_cancelled and check_cancelled()) else "Cancelled by user")
        return out
    finally:
        cleanup_source_video(video)
