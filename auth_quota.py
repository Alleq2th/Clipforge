"""
Clipforge Supabase Authentication, Quotas & User Isolation Module
Handles JWT verification, 60-minute quota enforcement, plan selection,
and private user library queries with SQLite local fallback.
"""

import os
import time
import json
import sqlite3
import threading
import requests
from config import WORK_DIR

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_ANON_KEY", "")
).strip()

_local_db_lock = threading.Lock()

def _get_local_db():
    os.makedirs(WORK_DIR, exist_ok=True)
    db_path = os.path.join(WORK_DIR, "clipforge_jobs.db")
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_user_tables():
    """Initializes local SQLite user tables for fallback and high-speed caching."""
    with _local_db_lock:
        conn = _get_local_db()
        try:
            with conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    id TEXT PRIMARY KEY,
                    email TEXT,
                    plan TEXT NOT NULL DEFAULT 'free',
                    plan_status TEXT NOT NULL DEFAULT 'active',
                    free_minutes_total REAL NOT NULL DEFAULT 60.0,
                    minutes_used REAL NOT NULL DEFAULT 0.0,
                    plan_selected INTEGER NOT NULL DEFAULT 0,
                    created_at REAL,
                    updated_at REAL
                );
                """)
                # Add user_id column to jobs if missing
                try:
                    conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT DEFAULT 'anonymous';")
                except Exception:
                    pass
                try:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);")
                except Exception:
                    pass
        finally:
            conn.close()

# Initialize tables at module import
init_user_tables()

def verify_user_token(auth_header):
    """
    Verifies Supabase JWT token from HTTP Authorization header.
    Returns user dict {'id': ..., 'email': ...} or None if invalid.
    """
    if not auth_header or not str(auth_header).startswith("Bearer "):
        return None

    token = str(auth_header).split("Bearer ", 1)[1].strip()
    if not token:
        return None

    # Test / development token support
    if token.startswith("test-user-") or token == "test-token":
        user_id = token if token.startswith("test-user-") else "test-user-123"
        return {"id": user_id, "email": f"{user_id}@example.com", "plan": "free"}

    if not SUPABASE_URL:
        # If no cloud URL configured, treat token as user identifier in local mode
        return {"id": token, "email": f"{token}@local.clipforge", "plan": "free"}

    try:
        url = f"{SUPABASE_URL}/auth/v1/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "apikey": SUPABASE_KEY or token
        }
        resp = requests.get(url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "id": data.get("id"),
                "email": data.get("email", ""),
                "metadata": data.get("user_metadata", {})
            }
        else:
            print(f"[AUTH] Supabase token check returned {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        print(f"[AUTH] Error verifying token with Supabase: {e}")
        return None

def get_user_profile(user_id, email=""):
    """
    Returns user profile with active plan and remaining minutes.
    Syncs with Supabase if available; uses durable local SQLite storage.
    """
    init_user_tables()
    user_id = str(user_id)
    now = time.time()

    # 1. Check local DB first
    profile = None
    with _local_db_lock:
        conn = _get_local_db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM user_profiles WHERE id = ?;", (user_id,))
            row = cur.fetchone()
            if row:
                profile = dict(row)
        finally:
            conn.close()

    # 2. Check Supabase REST API if cloud configured and not cached
    if not profile and SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{user_id}"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Accept": "application/json"
            }
            resp = requests.get(url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                rows = resp.json()
                if rows and len(rows) > 0:
                    r = rows[0]
                    profile = {
                        "id": user_id,
                        "email": r.get("email") or email,
                        "plan": r.get("plan", "free"),
                        "plan_status": r.get("plan_status", "active"),
                        "free_minutes_total": float(r.get("free_minutes_total", 60.0)),
                        "minutes_used": float(r.get("minutes_used", 0.0)),
                        "plan_selected": 1 if r.get("plan_selected_at") else 0,
                    }
        except Exception as e:
            print(f"[AUTH] Notice querying Supabase profile: {e}")

    # 3. Create default Free profile with 60 minutes if brand new user
    if not profile:
        profile = {
            "id": user_id,
            "email": email or f"{user_id[:8]}@user.clipforge",
            "plan": "free",
            "plan_status": "active",
            "free_minutes_total": 60.0,
            "minutes_used": 0.0,
            "plan_selected": 0,
            "created_at": now,
            "updated_at": now
        }
        with _local_db_lock:
            conn = _get_local_db()
            try:
                with conn:
                    conn.execute("""
                    INSERT INTO user_profiles (id, email, plan, plan_status, free_minutes_total, minutes_used, plan_selected, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (user_id, profile["email"], profile["plan"], profile["plan_status"], profile["free_minutes_total"], profile["minutes_used"], profile["plan_selected"], now, now))
            finally:
                conn.close()

    total = float(profile.get("free_minutes_total", 60.0))
    used = float(profile.get("minutes_used", 0.0))
    remaining = max(0.0, round(total - used, 1))

    return {
        "id": user_id,
        "email": profile.get("email", ""),
        "plan": profile.get("plan", "free"),
        "plan_status": "exhausted" if remaining <= 0 else profile.get("plan_status", "active"),
        "free_minutes_total": total,
        "minutes_used": round(used, 1),
        "minutes_remaining": remaining,
        "plan_selected": bool(profile.get("plan_selected", 0))
    }

def select_user_plan(user_id, plan="free"):
    """
    Records plan selection (e.g. Free 60 Minutes or Pro).
    Unlocks access to the /app clipper workspace.
    """
    user_id = str(user_id)
    # Ensure profile exists
    get_user_profile(user_id)
    plan = str(plan).lower().strip()
    now = time.time()
    total_minutes = 60.0 if plan == "free" else 300.0

    with _local_db_lock:
        conn = _get_local_db()
        try:
            with conn:
                conn.execute("""
                UPDATE user_profiles
                SET plan = ?, free_minutes_total = ?, plan_selected = 1, updated_at = ?
                WHERE id = ?;
                """, (plan, total_minutes, now, user_id))
        finally:
            conn.close()

    # Sync to Supabase if configured
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/user_profiles?id=eq.{user_id}"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            }
            body = {
                "plan": plan,
                "free_minutes_total": total_minutes,
                "plan_selected_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }
            requests.patch(url, headers=headers, json=body, timeout=5.0)
        except Exception as e:
            print(f"[AUTH] Notice syncing plan to Supabase: {e}")

    return get_user_profile(user_id)

def check_user_quota(user_id, video_duration_seconds=0):
    """
    Checks if user has sufficient quota for the requested video.
    Returns (allowed: bool, remaining_minutes: float, required_minutes: float, reason: str).
    """
    profile = get_user_profile(user_id)
    rem = profile["minutes_remaining"]

    if rem <= 0:
        return False, 0.0, 0.0, "You have used all of your 60 free minutes. Upgrade to Pro or wait until next month."

    if video_duration_seconds > 0:
        req_minutes = round(video_duration_seconds / 60.0, 1)
        if req_minutes > rem:
            return False, rem, req_minutes, f"This video is {req_minutes}m long, but you only have {rem}m remaining in your free quota."
        return True, rem, req_minutes, "Quota available."

    return True, rem, 0.0, "Quota available."

def deduct_user_quota(user_id, video_duration_seconds, jid=None):
    """
    Deducts video duration from user's free minutes balance upon clipping completion.
    """
    if video_duration_seconds <= 0:
        return get_user_profile(user_id)

    user_id = str(user_id)
    minutes = round(video_duration_seconds / 60.0, 1)
    now = time.time()

    with _local_db_lock:
        conn = _get_local_db()
        try:
            with conn:
                conn.execute("""
                UPDATE user_profiles
                SET minutes_used = minutes_used + ?,
                    plan_status = CASE WHEN (minutes_used + ?) >= free_minutes_total THEN 'exhausted' ELSE 'active' END,
                    updated_at = ?
                WHERE id = ?;
                """, (minutes, minutes, now, user_id))
        finally:
            conn.close()

    # Sync to Supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            rpc_url = f"{SUPABASE_URL}/rest/v1/rpc/deduct_user_minutes"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            }
            requests.post(rpc_url, headers=headers, json={"p_user_id": user_id, "p_minutes": minutes}, timeout=5.0)
        except Exception as e:
            print(f"[AUTH] Notice calling Supabase deduct_user_minutes: {e}")

    return get_user_profile(user_id)

def get_user_private_library(user_id):
    """
    Retrieves saved video jobs strictly owned by this user_id.
    Prevents cross-user data leakage.
    """
    user_id = str(user_id)
    init_user_tables()
    with _local_db_lock:
        conn = _get_local_db()
        try:
            cur = conn.cursor()
            cur.execute("""
            SELECT id, type, url, title, thumbnail, status, stage, progress, options, clips, error, done, created_at
            FROM jobs
            WHERE user_id = ?
            ORDER BY created_at DESC;
            """, (user_id,))
            rows = cur.fetchall()
            lib = []
            for r in rows:
                item = dict(r)
                try: item["options"] = json.loads(item["options"]) if item.get("options") else {}
                except Exception: item["options"] = {}
                try: item["clips"] = json.loads(item["clips"]) if item.get("clips") else []
                except Exception: item["clips"] = []
                lib.append(item)
            return lib
        finally:
            conn.close()
