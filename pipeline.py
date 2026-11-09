import os
import json
from download import download
from transcribe import transcribe
from pick import pick
from cut import cut
from storage import get_job_work_dir, get_job_out_dir, cleanup_job_work_dir, cleanup_storage
from jobs_db import update_job, save_clip_record

def run(url, jid=None, cb=print, on_clip=None, options=None, check_cancelled=None):
    opts = options or {}
    count = int(opts.get("count", 5))
    min_len = int(opts.get("min_len", 20))
    max_len = int(opts.get("max_len", 60))
    focus = opts.get("focus", "viral")
    aspect_ratio = opts.get("aspect_ratio", "9:16")
    caption_style = opts.get("caption_style", "beasty")
    framing = opts.get("framing", "crop")

    job_id = jid or "default"
    work_dir = get_job_work_dir(job_id)
    out_dir = get_job_out_dir(job_id)

    # Proactively clean up old finished jobs without touching active jobs
    cleanup_storage()

    def update_stage(msg, progress_pct):
        cb(msg)
        if jid:
            update_job(jid, status="processing", stage=msg, progress=progress_pct)

    update_stage("Downloading source video (720p stream)...", 10)
    video, vid = download(url, dest_dir=work_dir, check_cancelled=check_cancelled)

    if check_cancelled and check_cancelled():
        cleanup_job_work_dir(job_id)
        update_stage("Cancelled by user", 0)
        return []

    try:
        update_stage("Transcribing audio & generating timestamps...", 35)
        segs = transcribe(video, vid, work_dir=work_dir, out_dir=out_dir, check_cancelled=check_cancelled)

        if check_cancelled and check_cancelled():
            update_stage("Cancelled by user", 0)
            return []

        update_stage(f"AI discovering & ranking {count} viral moments...", 55)
        clips = pick(segs, count=count, min_len=min_len, max_len=max_len, focus=focus)

        # Save discovered candidates for metadata and retry
        with open(os.path.join(out_dir, "discovered_clips.json"), "w", encoding="utf-8") as f:
            json.dump(clips, f, indent=2)

        out = []
        total = len(clips)
        for i, c in enumerate(clips, 1):
            if check_cancelled and check_cancelled():
                update_stage("Cancelled by user", 0)
                break

            progress_val = int(60 + (i / max(total, 1)) * 35)
            update_stage(f"Rendering clip {i} of {total} ({aspect_ratio} • {framing})...", progress_val)

            try:
                f = cut(
                    video, segs, c, i, vid,
                    aspect_ratio=aspect_ratio,
                    caption_style=caption_style,
                    framing=framing,
                    out_dir=out_dir,
                    jid=jid
                )
                item = {
                    **c,
                    "file": os.path.basename(f),
                    "vid": str(job_id),
                    "index": i,
                    "ok": True,
                    "download_url": f"/clips/{job_id}/{os.path.basename(f)}?dl=1",
                    "stream_url": f"/clips/{job_id}/{os.path.basename(f)}"
                }
                out.append(item)
                if jid:
                    save_clip_record(jid, i, item)

                if on_clip:
                    try:
                        on_clip(item, i, total)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[PIPELINE] Error cutting clip {i}: {e}")
                err_item = {
                    **c,
                    "file": "",
                    "vid": str(job_id),
                    "index": i,
                    "ok": False,
                    "error": str(e),
                    "download_url": "",
                    "stream_url": ""
                }
                out.append(err_item)
                if jid:
                    save_clip_record(jid, i, err_item)

        if check_cancelled and check_cancelled():
            update_stage("Cancelled by user", 0)
        else:
            update_stage("Completed", 100)

        return out
    finally:
        # Clean up temporary work directory (raw downloads, temp audio)
        cleanup_job_work_dir(job_id)
