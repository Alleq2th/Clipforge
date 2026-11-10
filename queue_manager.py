"""Job queue.

Two levels of concurrency, because they solve different problems:

* MAX_CONCURRENT_JOBS   how many videos process at once. Bounded so a burst of
                         uploads cannot exhaust RAM on a small host.
* MAX_CONCURRENT_RENDERS how many ffmpeg encodes run at once. ffmpeg already
                         uses every core, so more than 2-3 is slower, not faster.

Jobs are in-memory (fast status polling) and mirrored to SQLite (survives a
restart). Cancellation is cooperative: the flag is checked between every stage
and between every clip.
"""
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from config import MAX_CONCURRENT_JOBS, MAX_CONCURRENT_RENDERS

STAGE_SEQUENCE = ["ingest", "asr", "scenes", "moments", "render"]

_jobs = {}
_lock = threading.Lock()
_job_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="cf-job")
_render_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RENDERS, thread_name_prefix="cf-render")


def render_pool():
    return _render_pool


def new_id():
    return uuid.uuid4().hex[:12]


def create(sources, options=None, title=""):
    """Registers a job and returns its id. Does not start it."""
    jid = new_id()
    with _lock:
        _jobs[jid] = {
            "id": jid,
            "status": "queued",
            "stage": "queued",
            "message": "Queued",
            "progress": 0,
            "stage_progress": {s: 0 for s in STAGE_SEQUENCE},
            "sources": list(sources),
            "title": title,
            "options": options or {},
            "clips": [],
            "videos": [],
            "errors": [],
            "cancelled": False,
            "done": False,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    return jid


def get(jid):
    with _lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def list_jobs(limit=50):
    with _lock:
        items = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
        return [dict(j) for j in items[:limit]]


def update(jid, **fields):
    with _lock:
        j = _jobs.get(jid)
        if not j:
            return None
        for k, v in fields.items():
            if k == "stage_progress" and isinstance(v, dict):
                j["stage_progress"].update(v)
            elif k == "clips" and isinstance(v, list):
                j["clips"] = v
            else:
                j[k] = v
        j["updated_at"] = time.time()
        return dict(j)


def add_clip(jid, clip):
    with _lock:
        j = _jobs.get(jid)
        if not j:
            return
        j["clips"] = [c for c in j["clips"] if c.get("index") != clip.get("index")]
        j["clips"].append(clip)
        j["clips"].sort(key=lambda c: (c.get("source_index", 0), c.get("index", 0)))
        j["updated_at"] = time.time()


def is_cancelled(jid):
    with _lock:
        j = _jobs.get(jid)
        return bool(j and j.get("cancelled"))


def cancel(jid):
    return update(jid, cancelled=True, status="cancelled",
                  stage="cancelled", message="Cancelled")


def _status_cb(jid):
    """Builds the progress callback that writes stage info onto the job."""
    def cb(event):
        stage = event.get("stage", "")
        msg = event.get("message", "")
        pct = int(event.get("progress", 0))
        fields = {
            "stage": stage,
            "message": msg,
            "progress": pct,
            "status": "processing",
        }
        if stage in STAGE_SEQUENCE:
            fields["stage_progress"] = {stage: 100 if pct >= 100 else 50}
        if event.get("video"):
            with _lock:
                j = _jobs.get(jid)
                if j is not None:
                    j["videos"] = j["videos"] + [event["video"]]
        update(jid, **fields)
    return cb


def submit(jid, runner, sources, options):
    """Queues a job on the job pool. runner(sources, options, cb, is_cancelled)."""
    def _run():
        update(jid, status="processing", stage="ingest", message="Starting")
        cb = _status_cb(jid)
        try:
            clips, errors = runner(
                sources, options, cb, lambda: is_cancelled(jid)
            )
            for c in clips:
                add_clip(jid, c)
            if is_cancelled(jid):
                update(jid, status="cancelled", stage="cancelled",
                       message="Cancelled", done=True)
                return
            ok = [c for c in clips if c.get("ok")]
            blocked = [c for c in clips if c.get("render_unavailable")]
            bad = [c for c in clips if not c.get("ok") and not c.get("render_unavailable")]

            if ok:
                status, msg = "done", f"{len(ok)} clips ready"
            elif blocked and not bad:
                # The pipeline did its job; only the encoder was unavailable.
                status = "render_unavailable"
                msg = (
                    "Clips were selected and every render graph validated, but this "
                    "environment refused to encode video. Set up a host with ffmpeg "
                    "encoding to get the files."
                )
            else:
                status = "failed"
                msg = errors[0]["error"] if errors else (
                    bad[0].get("error") if bad else "No clips produced"
                )

            update(
                jid,
                status=status,
                stage="done",
                message=msg,
                progress=100,
                errors=errors,
                done=True,
            )
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            update(jid, status="failed", stage="failed",
                   message=str(e)[:300], done=True)

    _job_pool.submit(_run)
    return jid
