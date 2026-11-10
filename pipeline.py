import os
import json
import concurrent.futures
from download import download
from transcribe import transcribe
from pick import pick
from cut import cut
from storage import get_job_work_dir, get_job_out_dir, cleanup_job_work_dir, cleanup_storage, get_storage_provider
from jobs_db import update_job, save_clip_record
from config import MAX_CLIP_WORKERS, DEFAULT_EXPORT_PROFILE

def _render_clip_worker(video, segs, c, i, vid, aspect_ratio, caption_style, framing, out_dir, jid, job_id, export_profile=None):
    """Renders an individual clip cleanly; returns (index, item_dict, error)."""
    try:
        f = cut(
            video, segs, c, i, vid,
            aspect_ratio=aspect_ratio,
            caption_style=caption_style,
            framing=framing,
            out_dir=out_dir,
            jid=jid,
            export_profile=export_profile
        )
        # Offload to durable cloud storage if configured (Supabase / S3)
        provider = get_storage_provider()
        rel_dest = f"{job_id}/{os.path.basename(f)}"
        cloud_url = provider.save_clip(f, rel_dest)
        download_url = cloud_url if cloud_url else f"/clips/{job_id}/{os.path.basename(f)}?dl=1"
        stream_url = cloud_url if cloud_url else f"/clips/{job_id}/{os.path.basename(f)}"

        item = {
            **c,
            "file": os.path.basename(f),
            "vid": str(job_id),
            "index": i,
            "ok": True,
            "download_url": download_url,
            "stream_url": stream_url,
            "cloud_url": cloud_url or ""
        }
        return i, item, None
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
        return i, err_item, e

def run(url, jid=None, cb=print, on_clip=None, options=None, check_cancelled=None):
    opts = options or {}
    count = int(opts.get("count", 5))
    min_len = int(opts.get("min_len", 20))
    max_len = int(opts.get("max_len", 60))
    focus = opts.get("focus", "viral")
    aspect_ratio = opts.get("aspect_ratio", "9:16")
    caption_style = opts.get("caption_style", "beasty")
    framing = opts.get("framing", "crop")
    export_profile = opts.get("export_profile") or opts.get("resolution") or DEFAULT_EXPORT_PROFILE

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

        total = len(clips)
        out_map = {}
        completed_count = 0

        # Concurrency control with safe container limits (default 2 workers, bounded [1, 4])
        raw_workers = opts.get("max_workers", MAX_CLIP_WORKERS)
        max_workers = max(1, min(4, int(raw_workers)))

        # Attempt concurrent rendering when multiple clips are requested
        if max_workers > 1 and total > 1:
            try:
                print(f"[PIPELINE] Concurrent rendering enabled: processing {total} clips using {max_workers} worker threads.")
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="clip-render") as executor:
                    future_to_i = {}
                    for i, c in enumerate(clips, 1):
                        if check_cancelled and check_cancelled():
                            break
                        fut = executor.submit(
                            _render_clip_worker,
                            video, segs, c, i, vid,
                            aspect_ratio, caption_style, framing,
                            out_dir, jid, job_id, export_profile
                        )
                        future_to_i[fut] = i

                    for fut in concurrent.futures.as_completed(future_to_i):
                        i = future_to_i[fut]
                        if check_cancelled and check_cancelled():
                            continue
                        try:
                            clip_idx, item, err = fut.result()
                        except Exception as exc:
                            print(f"[PIPELINE] Worker thread raised for clip {i}: {exc}")
                            c = clips[i - 1]
                            item = {
                                **c,
                                "file": "",
                                "vid": str(job_id),
                                "index": i,
                                "ok": False,
                                "error": str(exc),
                                "download_url": "",
                                "stream_url": ""
                            }

                        out_map[item["index"]] = item
                        completed_count += 1
                        progress_val = int(60 + (completed_count / max(total, 1)) * 35)
                        update_stage(f"Rendered clip {completed_count} of {total} ({aspect_ratio} • {framing})...", progress_val)

                        if jid:
                            save_clip_record(jid, item["index"], item)

                        if on_clip:
                            try:
                                on_clip(item, completed_count, total)
                            except Exception:
                                pass
            except Exception as e:
                print(f"[PIPELINE] Concurrent render pool notice ({e}); safely falling back to serial pipeline.")

        # Safe fallback: serially render any clip not already rendered
        for i, c in enumerate(clips, 1):
            if i in out_map:
                continue
            if check_cancelled and check_cancelled():
                update_stage("Cancelled by user", 0)
                break

            completed_count += 1
            progress_val = int(60 + (completed_count / max(total, 1)) * 35)
            update_stage(f"Rendering clip {i} of {total} ({aspect_ratio} • {framing})...", progress_val)

            _, item, _ = _render_clip_worker(
                video, segs, c, i, vid,
                aspect_ratio, caption_style, framing,
                out_dir, jid, job_id, export_profile
            )
            out_map[i] = item

            if jid:
                save_clip_record(jid, i, item)

            if on_clip:
                try:
                    on_clip(item, completed_count, total)
                except Exception:
                    pass

        out = [out_map[i] for i in range(1, total + 1) if i in out_map]

        if check_cancelled and check_cancelled():
            update_stage("Cancelled by user", 0)
        else:
            update_stage("Completed", 100)

        return out
    finally:
        # Clean up temporary work directory (raw downloads, temp audio)
        cleanup_job_work_dir(job_id)
