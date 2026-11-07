import os
from download import download
from transcribe import transcribe
from pick import pick
from cut import cut

def run(url, cb=print):
    cb("Downloading video...")
    video, vid = download(url)
    cb("Transcribing (this is the slow part)...")
    segs = transcribe(video, vid)
    cb("AI is picking the best moments...")
    clips = pick(segs)
    out = []
    for i, c in enumerate(clips, 1):
        cb(f"Cutting clip {i} of {len(clips)}...")
        f = cut(video, segs, c, i, vid)
        out.append({**c, "file": os.path.basename(f), "vid": vid})
    cb("Done")
    return out
