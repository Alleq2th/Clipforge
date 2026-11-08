import os
from download import download
from transcribe import transcribe
from pick import pick
from cut import cut

def run(url, cb=print, options=None):
    opts = options or {}
    count = int(opts.get("count", 5))
    min_len = int(opts.get("min_len", 20))
    max_len = int(opts.get("max_len", 60))
    focus = opts.get("focus", "viral")

    cb("Downloading video (optimizing 720p)...")
    video, vid = download(url)

    cb("Transcribing audio...")
    segs = transcribe(video, vid)

    cb(f"AI detecting {count} {focus} moments...")
    clips = pick(segs, count=count, min_len=min_len, max_len=max_len, focus=focus)

    out = []
    total = len(clips)
    for i, c in enumerate(clips, 1):
        cb(f"Cutting clip {i} of {total} (9:16 + captions)...")
        f = cut(video, segs, c, i, vid)
        out.append({**c, "file": os.path.basename(f), "vid": vid})
    cb("Done")
    return out
