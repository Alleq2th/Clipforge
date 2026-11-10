"""ClipForge API.

Scoped to clipping only. The faceless/voice-profile side of the old app is left
untouched in the repo (faceless.py, voice_profiles.py) but is no longer wired
into the new flow.
"""
import json
import os
import time

from flask import Flask, jsonify, request, send_from_directory

import queue_manager as qm
from config import (
    DEFAULT_CAPTION_STYLE,
    MAX_VIDEOS_PER_JOB,
    OUT_DIR,
    WORK_DIR,
)
from engine import captions, reframe
from engine.pipeline import run as run_pipeline
from security import safe_join_path, validate_video_url

app = Flask(__name__, static_folder="static", static_url_path="")
_start = time.time()

STAGE_META = [
    {"id": "ingest", "label": "Ingest"},
    {"id": "asr", "label": "Transcribe"},
    {"id": "scenes", "label": "Scenes"},
    {"id": "moments", "label": "MomentRank"},
    {"id": "render", "label": "Render"},
]


def _runner(sources, options, cb, is_cancelled):
    """Adapter so queue_manager does not need to know the pipeline signature."""
    return run_pipeline(
        sources, options.get("_job_id", "job"),
        options=options, cb=cb, check_cancelled=is_cancelled,
    )


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/health")
def health():
    from config import GROQ_KEY, GEMINI_KEY, OPENAI_KEY, XAI_KEY
    return jsonify({
        "status": "ok",
        "uptime": int(time.time() - _start),
        "version": "3.0.0",
        "stage": STAGE_META,
        "engines": {
            "moment_rank": {k: bool(v) for k, v in {
                "gemini": GEMINI_KEY, "groq": GROQ_KEY,
                "openai": OPENAI_KEY, "xai": XAI_KEY,
            }.items()},
            "asr": "groq-whisper" if GROQ_KEY else "faster-whisper-local",
            "reframe": list(reframe.MODES),
        },
    })


@app.get("/api/options")
def options_api():
    """Everything the UI needs to build its controls, in one call."""
    return jsonify({
        "aspects": [
            {"id": "9:16", "label": "9:16", "note": "Reels, Shorts, TikTok"},
            {"id": "4:5", "label": "4:5", "note": "Feed"},
            {"id": "1:1", "label": "1:1", "note": "Square"},
            {"id": "16:9", "label": "16:9", "note": "Landscape"},
        ],
        "reframe_modes": [
            {"id": k, "label": reframe.MODE_LABELS[k]} for k in reframe.MODES
        ],
        "caption_styles": captions.style_list(),
        "focus": [
            {"id": "viral", "label": "Viral hooks"},
            {"id": "insight", "label": "Insight"},
            {"id": "funny", "label": "Comedy"},
            {"id": "controversial", "label": "Debate"},
            {"id": "educational", "label": "Educational"},
            {"id": "action", "label": "High energy"},
        ],
        "durations": [
            {"id": "under30", "label": "Under 30s", "min": 15, "max": 30},
            {"id": "30to60", "label": "30-60s", "min": 30, "max": 60},
            {"id": "1to3m", "label": "1-3 min", "min": 60, "max": 180},
        ],
        "max_sources": MAX_VIDEOS_PER_JOB,
        "default_caption_style": DEFAULT_CAPTION_STYLE,
    })


def _parse_duration(value, min_len=None, max_len=None):
    table = {"under30": (15, 30), "30to60": (30, 60), "1to3m": (60, 180), "auto": (20, 60)}
    if value in table:
        return table[value]
    try:
        lo, hi = int(min_len or 20), int(max_len or 60)
        return max(5, lo), max(lo + 5, hi)
    except (TypeError, ValueError):
        return 20, 60


@app.post("/api/jobs")
def create_job():
    data = request.get_json(silent=True) or {}
    sources = data.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    sources = [str(s).strip() for s in sources if str(s).strip()][:MAX_VIDEOS_PER_JOB]

    if not sources:
        return jsonify({"error": "Add at least one video link."}), 400

    for s in sources:
        if s.startswith(("http://", "https://")):
            try:
                validate_video_url(s)
            except ValueError as e:
                return jsonify({"error": f"{e}"}), 400

    lo, hi = _parse_duration(data.get("duration"), data.get("min_len"), data.get("max_len"))
    opts = {
        "count": max(1, min(20, int(data.get("count", 5)))),
        "min_len": lo,
        "max_len": hi,
        "focus": data.get("focus", "viral"),
        "aspect_ratio": data.get("aspect_ratio", "9:16"),
        "caption_style": data.get("caption_style", DEFAULT_CAPTION_STYLE),
        "reframe_mode": data.get("reframe_mode", "track"),
    }

    jid = qm.create(sources, options=opts, title=data.get("title", ""))
    opts["_job_id"] = jid
    qm.update(jid, options=opts)
    qm.submit(jid, _runner, sources, opts)
    return jsonify({"id": jid, "sources": len(sources), "options": opts})


@app.get("/api/jobs/<jid>")
def job_status(jid):
    j = qm.get(jid)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(j)


@app.post("/api/jobs/<jid>/cancel")
def job_cancel(jid):
    if not qm.get(jid):
        return jsonify({"error": "Job not found"}), 404
    qm.cancel(jid)
    return jsonify({"ok": True})


@app.get("/api/jobs")
def job_list():
    return jsonify({"jobs": qm.list_jobs(limit=30)})


@app.post("/api/jobs/<jid>/restyle")
def job_restyle(jid):
    """Re-renders a finished job's clips with a different caption style.

    Reuses the cached transcript and the stored clip candidates, so this is a
    render-only pass - no re-download, no re-transcription.
    """
    job = qm.get(jid)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    data = request.get_json(silent=True) or {}
    style = data.get("caption_style")
    if style not in captions.STYLES:
        return jsonify({"error": "Unknown caption style."}), 400

    opts = dict(job.get("options") or {})
    opts["caption_style"] = style
    opts["_job_id"] = jid

    # Reset the job so the UI can poll it like a fresh run.
    qm.update(
        jid,
        status="processing", stage="render", message="Re-rendering captions",
        progress=0, clips=[], cancelled=False, done=False, errors=[],
        options=opts,
    )
    qm.submit(jid, _runner, job.get("sources") or [], opts)
    return jsonify({"ok": True, "id": jid, "caption_style": style})


@app.get("/clips/<path:filepath>")
def serve_clip(filepath):
    try:
        full = safe_join_path(OUT_DIR, filepath)
    except PermissionError:
        return jsonify({"error": "Denied"}), 403
    if not os.path.isfile(full):
        return jsonify({"error": "Not found"}), 404

    download = request.args.get("dl") == "1"
    resp = send_from_directory(
        os.path.dirname(full), os.path.basename(full), as_attachment=download
    )
    if download:
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{os.path.basename(full)}"'
        )
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), threaded=True)
