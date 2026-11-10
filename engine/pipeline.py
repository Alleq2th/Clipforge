"""Pipeline orchestration.

One job = up to MAX_VIDEOS_PER_JOB sources. Each source runs the full chain and
reports progress per stage, so the UI can show what is actually happening
instead of a fake spinner.

Stage weights are proportional to real wall-clock cost on a 1-hour source:
  ingest 8% | asr 30% | scenes 7% | moments 10% | render 45%
"""
import json
import os
import time
import traceback

from config import (
    DEFAULT_CAPTION_STYLE,
    MAX_VIDEOS_PER_JOB,
    OUT_DIR,
    WORK_DIR,
)
from engine import asr, captions, ingest, moments, reframe, render, scenes

STAGES = [
    ("ingest", "Fetching source", 8),
    ("asr", "Transcribing speech", 30),
    ("scenes", "Detecting shot changes", 7),
    ("moments", "Ranking moments", 10),
    ("render", "Rendering clips", 45),
]


def _job_dirs(job_id):
    work = os.path.join(WORK_DIR, "jobs", str(job_id))
    out = os.path.join(OUT_DIR, str(job_id))
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    return work, out


def _emit(cb, stage, message, pct, **extra):
    if cb:
        try:
            cb({"stage": stage, "message": message, "progress": int(pct), **extra})
        except Exception:  # noqa: BLE001 - a bad callback must not kill the job
            pass


def run_source(source, job_id, options=None, cb=None, check_cancelled=None, index=0, total=1):
    """Runs the full chain for one source. Returns a list of clip dicts."""
    opts = options or {}
    count = int(opts.get("count", 5))
    min_len = int(opts.get("min_len", 20))
    max_len = int(opts.get("max_len", 60))
    focus = opts.get("focus", "viral")
    aspect = opts.get("aspect_ratio", "9:16")
    style = opts.get("caption_style", DEFAULT_CAPTION_STYLE)
    mode = opts.get("reframe_mode", "track")

    work, out = _job_dirs(job_id)
    src_work = os.path.join(work, f"src{index}")
    os.makedirs(src_work, exist_ok=True)

    def cancelled():
        return bool(check_cancelled and check_cancelled())

    def stage_pct(stage_name, frac=0.0):
        """Maps a stage + fraction to overall progress for this source."""
        done = 0
        for name, _label, weight in STAGES:
            if name == stage_name:
                return done + weight * max(0.0, min(1.0, frac))
            done += weight
        return done

    def overall(stage_name, frac=0.0):
        per_source = stage_pct(stage_name, frac)
        return (index * 100 + per_source) / max(1, total)

    # ---------------------------------------------------------------- ingest
    _emit(cb, "ingest", f"Fetching source {index + 1} of {total}", overall("ingest", 0.1))
    meta = ingest.ingest(source, dest_dir=src_work, check_cancelled=cancelled)
    if cancelled():
        return []
    _emit(cb, "ingest", f"Got {meta['title'][:60]}", overall("ingest", 1.0),
          video={"title": meta["title"], "author": meta["author"],
                 "thumbnail": meta["thumbnail"], "duration": meta["duration"],
                 "platform": meta["platform"]})

    # ------------------------------------------------------------------- asr
    _emit(cb, "asr", "Transcribing with word-level timestamps", overall("asr", 0.05))
    transcript = asr.transcribe(
        meta["path"], src_work, out_dir=out, check_cancelled=cancelled
    )
    if cancelled():
        return []
    segments = transcript["segments"]
    _emit(cb, "asr", f"{transcript['word_count']} words via {transcript['source']}",
          overall("asr", 1.0))

    # ---------------------------------------------------------------- scenes
    _emit(cb, "scenes", "Detecting shot changes", overall("scenes", 0.2))
    cuts = scenes.detect(meta["path"], fps=meta.get("fps", 30.0))
    _emit(cb, "scenes", f"{max(0, len(cuts) - 1)} shot changes", overall("scenes", 1.0))

    # --------------------------------------------------------------- moments
    _emit(cb, "moments", f"MomentRank scoring {count} clips", overall("moments", 0.2))
    picks = moments.select(
        segments, count=count, min_len=min_len, max_len=max_len,
        focus=focus, cuts=cuts,
    )
    if not picks:
        raise RuntimeError("No usable moments found in this video.")

    with open(os.path.join(out, f"candidates_{index}.json"), "w", encoding="utf-8") as f:
        json.dump(picks, f, indent=2)
    _emit(cb, "moments", f"{len(picks)} moments selected", overall("moments", 1.0))

    # ---------------------------------------------------------------- render
    src_w = meta.get("width") or 1920
    src_h = meta.get("height") or 1080
    out_w, out_h = render.dimensions(aspect)

    results = []
    n = len(picks)
    for i, pick in enumerate(picks, 1):
        if cancelled():
            break

        frac = (i - 1) / max(1, n)
        _emit(cb, "render", f"Rendering clip {i} of {n}", overall("render", frac),
              clip_index=i, clip_total=n)

        try:
            plan = reframe.plan(
                meta["path"], pick["start"], pick["end"], mode=mode,
                src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            )

            ass_path = None
            if style and style not in ("none", "off", ""):
                ass_path = os.path.join(src_work, f"clip{i}.ass")
                captions.write_ass(
                    segments, pick["start"], pick["end"], ass_path,
                    style=style, width=out_w, height=out_h,
                )

            sendcmd = None
            if plan["mode"] == "track" and plan["crop_path"]:
                sendcmd = os.path.join(src_work, f"clip{i}.cmd")
                reframe.write_sendcmd(
                    sendcmd, plan["crop_path"], out_w, out_h, src_w, src_h
                )

            filename = f"clip_{index + 1}_{i:02d}.mp4"
            out_path = os.path.join(out, filename)
            render.render(
                meta["path"], out_path, pick["start"], pick["end"],
                mode=plan["mode"], aspect=aspect, ass_path=ass_path,
                sendcmd=sendcmd, src_w=src_w, src_h=src_h,
            )

            results.append({
                **pick,
                "file": filename,
                "index": i,
                "ok": True,
                "reframe_mode": plan["mode"],
                "reframe_reason": plan["reason"],
                "caption_style": style,
                "aspect_ratio": aspect,
                "source_title": meta["title"],
                "source_index": index,
                "download_url": f"/clips/{job_id}/{filename}?dl=1",
                "stream_url": f"/clips/{job_id}/{filename}",
            })
        except render.RenderUnavailable as e:
            # The graph is right, the host cannot encode. Say so plainly rather
            # than calling it a broken clip.
            print(f"[PIPELINE] clip {i}: encode unavailable - {e}")
            results.append({
                **pick,
                "file": "",
                "index": i,
                "ok": False,
                "render_unavailable": True,
                "error": str(e)[:300],
                "source_title": meta["title"],
                "source_index": index,
            })
        except Exception as e:  # noqa: BLE001 - one bad clip must not kill the batch
            print(f"[PIPELINE] clip {i} failed: {e}")
            results.append({
                **pick,
                "file": "",
                "index": i,
                "ok": False,
                "error": str(e)[:300],
                "source_title": meta["title"],
                "source_index": index,
            })

    _emit(cb, "render", f"Rendered {sum(1 for r in results if r['ok'])} of {n}",
          overall("render", 1.0))
    return results


def run(sources, job_id, options=None, cb=None, check_cancelled=None):
    """Runs every source in a job. Returns (clips, errors)."""
    if isinstance(sources, str):
        sources = [sources]
    sources = [s for s in sources if s and str(s).strip()][:MAX_VIDEOS_PER_JOB]
    if not sources:
        raise ValueError("No video sources given.")

    all_clips, errors = [], []
    for i, src in enumerate(sources):
        if check_cancelled and check_cancelled():
            break
        try:
            all_clips.extend(
                run_source(src, job_id, options=options, cb=cb,
                           check_cancelled=check_cancelled, index=i, total=len(sources))
            )
        except Exception as e:  # noqa: BLE001
            print(f"[PIPELINE] source {i} failed: {e}\n{traceback.format_exc()}")
            errors.append({"source": str(src), "error": str(e)[:300]})

    return all_clips, errors
