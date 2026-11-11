import sqlite3
import json
import time
import os
import threading
from config import WORK_DIR

_lock = threading.Lock()

def get_db_path():
    os.makedirs(WORK_DIR, exist_ok=True)
    return os.path.join(WORK_DIR, "clipforge_jobs.db")

def get_connection():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for high concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    with _lock:
        conn = get_connection()
        try:
            with conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL DEFAULT 'clipper',
                    url TEXT,
                    title TEXT,
                    thumbnail TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    stage TEXT NOT NULL DEFAULT 'Queued in line...',
                    progress INTEGER NOT NULL DEFAULT 0,
                    options TEXT,
                    clips TEXT,
                    candidates TEXT,
                    error TEXT,
                    done INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    created_at REAL,
                    updated_at REAL
                );
                """)
                # Migration: add candidates column if missing in existing database
                try:
                    conn.execute("ALTER TABLE jobs ADD COLUMN candidates TEXT;")
                except Exception:
                    pass

                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);")

                conn.execute("""
                CREATE TABLE IF NOT EXISTS clips (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    clip_index INTEGER NOT NULL,
                    title TEXT,
                    file TEXT,
                    duration REAL,
                    score INTEGER,
                    category TEXT,
                    hook_reason TEXT,
                    caption TEXT,
                    hashtags TEXT,
                    ok INTEGER NOT NULL DEFAULT 1,
                    error TEXT,
                    created_at REAL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_job ON clips(job_id);")
        finally:
            conn.close()

def create_job(jid, job_type="clipper", url="", title="Video Batch", thumbnail="", options=None, candidates=None):
    init_db()
    now = time.time()
    options_json = json.dumps(options or {})
    clips_json = json.dumps([])
    candidates_json = json.dumps(candidates or [])
    with _lock:
        conn = get_connection()
        try:
            with conn:
                conn.execute("""
                INSERT INTO jobs (id, type, url, title, thumbnail, status, stage, progress, options, clips, candidates, error, done, cancelled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', 'Queued in processing pool...', 0, ?, ?, ?, NULL, 0, 0, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url,
                    title = excluded.title,
                    thumbnail = excluded.thumbnail,
                    options = excluded.options,
                    candidates = excluded.candidates,
                    updated_at = excluded.updated_at;
                """, (jid, job_type, url, title, thumbnail, options_json, clips_json, candidates_json, now, now))
        finally:
            conn.close()
    return get_job(jid)

def update_job(jid, status=None, stage=None, progress=None, clips=None, candidates=None, error=None, done=None, cancelled=None):
    now = time.time()
    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if stage is not None:
        updates.append("stage = ?")
        params.append(stage)
    if progress is not None:
        updates.append("progress = ?")
        params.append(int(progress))
    if clips is not None:
        updates.append("clips = ?")
        params.append(json.dumps(clips))
    if candidates is not None:
        updates.append("candidates = ?")
        params.append(json.dumps(candidates))
    if error is not None:
        updates.append("error = ?")
        params.append(error)
    if done is not None:
        updates.append("done = ?")
        params.append(1 if done else 0)
    if cancelled is not None:
        updates.append("cancelled = ?")
        params.append(1 if cancelled else 0)

    if not updates:
        return

    updates.append("updated_at = ?")
    params.append(now)
    params.append(jid)

    sql = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?;"

    with _lock:
        conn = get_connection()
        try:
            with conn:
                conn.execute(sql, params)
        finally:
            conn.close()

def get_job(jid):
    init_db()
    with _lock:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM jobs WHERE id = ?;", (jid,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            res["done"] = bool(res["done"])
            res["cancelled"] = bool(res["cancelled"])
            try:
                res["options"] = json.loads(res["options"]) if res.get("options") else {}
            except Exception:
                res["options"] = {}
            try:
                res["clips"] = json.loads(res["clips"]) if res.get("clips") else []
            except Exception:
                res["clips"] = []
            try:
                res["candidates"] = json.loads(res["candidates"]) if "candidates" in res and res.get("candidates") else []
            except Exception:
                res["candidates"] = []
            return res
        finally:
            conn.close()

def list_jobs(limit=50):
    init_db()
    with _lock:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?;", (limit,))
            rows = cur.fetchall()
            out = []
            for row in rows:
                r = dict(row)
                r["done"] = bool(r["done"])
                r["cancelled"] = bool(r["cancelled"])
                try: r["options"] = json.loads(r["options"]) if r.get("options") else {}
                except Exception: r["options"] = {}
                try: r["clips"] = json.loads(r["clips"]) if r.get("clips") else []
                except Exception: r["clips"] = []
                try: r["candidates"] = json.loads(r["candidates"]) if "candidates" in r and r.get("candidates") else []
                except Exception: r["candidates"] = []
                out.append(r)
            return out
        finally:
            conn.close()

def save_clip_record(jid, clip_index, clip_data):
    init_db()
    cid = f"{jid}_{clip_index}"
    now = time.time()
    with _lock:
        conn = get_connection()
        try:
            with conn:
                conn.execute("""
                INSERT OR REPLACE INTO clips (id, job_id, clip_index, title, file, duration, score, category, hook_reason, caption, hashtags, ok, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    cid,
                    jid,
                    clip_index,
                    clip_data.get("title", f"Clip #{clip_index}"),
                    clip_data.get("file", ""),
                    float(clip_data.get("end", 0) - clip_data.get("start", 0)),
                    int(clip_data.get("score", 90)),
                    clip_data.get("category", "Viral Hook"),
                    clip_data.get("hook_reason", ""),
                    clip_data.get("caption", ""),
                    json.dumps(clip_data.get("hashtags", [])),
                    1 if clip_data.get("ok", True) else 0,
                    clip_data.get("error"),
                    now
                ))
        finally:
            conn.close()

    # Also update job's clips list
    job = get_job(jid)
    if job:
        existing_clips = job.get("clips", [])
        idx = next((i for i, c in enumerate(existing_clips) if c.get("index") == clip_index), None)
        if idx is not None:
            existing_clips[idx] = clip_data
        else:
            existing_clips.append(clip_data)
        update_job(jid, clips=existing_clips)

def get_job_clips(jid):
    init_db()
    with _lock:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM clips WHERE job_id = ? ORDER BY clip_index ASC;", (jid,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

def get_active_job_ids():
    init_db()
    with _lock:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM jobs WHERE done = 0 AND cancelled = 0;")
            return [r["id"] for r in cur.fetchall()]
        finally:
            conn.close()
