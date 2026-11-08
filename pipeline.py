import os
from download import download
from transcribe import transcribe
from pick import pick
from cut import cut
from storage import cleanup_source_video, cleanup_storage

def run(url, cb=print, on_clip=None, options=None):
    opts = options or {}
    count = int(opts.get("count", 5))
    min_len = int(opts.get("min_len", 20))
    max_len = int(opts.get("max_len", 60))
    focus = opts.get("focus", "viral")
    aspect_ratio = opts.get("aspect_ratio", "9:16")
    caption_style = opts.get("caption_style", "beasty")

    # Proactively free space before starting new download
    cleanup_storage()

    cb("Downloading video (optimizing 720p)...")
    video, vid = download(url)

    try:
        cb("Transcribing audio...")
        segs = transcribe(video, vid)

        cb(f"AI discovering & ranking {count} viral moments...")
        clips = pick(segs, count=count, min_len=min_len, max_len=max_len, focus=focus)

        out = []
        total = len(clips)
        for i, c in enumerate(clips, 1):
            cb(f"Cutting clip {i} of {total} ({aspect_ratio} • {caption_style.capitalize()} style)...")
            f = cut(video, segs, c, i, vid, aspect_ratio=aspect_ratio, caption_style=caption_style)
            item = {**c, "file": os.path.basename(f), "vid": vid}
            out.append(item)
            if on_clip:
                try:
                    on_clip(item, i, total)
                except Exception:
                    pass
        cb("Done")
        return out
    finally:
        cleanup_source_video(video)
