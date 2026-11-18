"""Accounts, plans and monthly minute usage for ClipForge.

Sign-in is handled by Supabase Auth. Plans, usage and upgrade interest live in
Supabase tables (see supabase/schema.sql). Limits are enforced on the server
when a job is created, so the browser can't skip them.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from config import WORK_DIR
from security import safe_join_path

PLAN_LIMITS = {"free": 60, "creator": 300, "pro": 1000}
PLAN_NAMES = {"free": "Free", "creator": "Creator", "pro": "Pro"}
PAID_PLANS = ("creator", "pro")
STUDIO_PLANS = ("pro",)
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "gregallen542@gmail.com")
STUDIO_WORDS_PER_MINUTE = 150
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    """An error the browser can show as-is. `code` lets the page pick the right prompt."""

    def __init__(self, message, status=400, code="error", **details):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details


def _config():
    return (
        os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
        os.getenv("SUPABASE_ANON_KEY", "").strip(),
        os.getenv("SUPABASE_SERVICE_KEY", "").strip(),
    )


def accounts_configured():
    url, anon, service = _config()
    return bool(url and anon and service)


def _parse(raw):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return {"msg": raw}


def _call(method, url, headers, payload=None, timeout=15):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        print(f"[ACCOUNTS] Supabase unreachable: {e}")
        raise AccountError("Accounts are unavailable right now. Try again in a minute.", 503, "accounts_unavailable")


def _auth(path, payload=None, method="POST", token=None):
    url, anon, _ = _config()
    headers = {"apikey": anon, "Authorization": f"Bearer {token or anon}"}
    return _call(method, f"{url}{path}", headers, payload)


def _rest(method, table, query="", payload=None, prefer=None):
    url, _, service = _config()
    headers = {"apikey": service, "Authorization": f"Bearer {service}"}
    if prefer:
        headers["Prefer"] = prefer
    status, body = _call(method, f"{url}/rest/v1/{table}{query}", headers, payload)
    if status >= 400:
        print(f"[ACCOUNTS] {method} {table} failed ({status}): {body}")
        raise AccountError("Account data is unavailable right now. Try again in a minute.", 503, "data_unavailable")
    return body


def _friendly_auth_error(body, fallback):
    text = ""
    if isinstance(body, dict):
        text = str(body.get("msg") or body.get("error_description") or body.get("message") or "")
    low = text.lower()
    if "already registered" in low or "already been registered" in low:
        return "That email already has an account. Sign in instead."
    if "password" in low and ("least" in low or "short" in low or "weak" in low):
        return "Use at least 8 characters for your password."
    if "invalid" in low and "email" in low:
        return "Enter a valid email address."
    return fallback


def _session_from(body):
    if not isinstance(body, dict) or not body.get("access_token"):
        return None
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": body.get("expires_at"),
    }


_user_cache = {}


def current_user(token):
    """Return {id, email} for a valid Supabase access token, or None. Cached for a minute."""
    if not token or not accounts_configured():
        return None
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    hit = _user_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    status, body = _auth("/auth/v1/user", method="GET", token=token)
    if status != 200 or not isinstance(body, dict) or not body.get("id"):
        _user_cache.pop(key, None)
        return None
    user = {"id": body["id"], "email": body.get("email", "")}
    if len(_user_cache) > 5000:
        _user_cache.clear()
    _user_cache[key] = (time.time() + 60, user)
    return user


def _ensure_profile(user_id, email, name="", reason=""):
    rows = _rest("GET", "profiles", f"?id=eq.{user_id}&select=id")
    if rows:
        return
    _rest(
        "POST", "profiles", "?on_conflict=id",
        [{"id": user_id, "email": email, "name": name, "reason": reason, "plan": "free"}],
        prefer="resolution=ignore-duplicates,return=minimal",
    )


def _send_welcome(email, name):
    def run():
        try:
            from notifications import send_completion_email
            first = (name or "there").split()[0]
            text = (
                f"Hi {first},\n\n"
                "Welcome to ClipForge. You're in the beta, so a few rough edges are expected.\n\n"
                "If anything breaks, looks wrong, or confuses you, just reply to this email and tell us. "
                "We read every reply.\n\n"
                "Your free plan includes 60 minutes of source video each month. "
                "Paste a link or upload a video to start.\n\n"
                "Thanks for trying ClipForge,\n"
                "Allen"
            )
            send_completion_email(to_email=email, subject="Welcome to ClipForge (beta)", html_content="", text_content=text)
        except Exception as e:
            print(f"[ACCOUNTS] Welcome email skipped: {e}")

    threading.Thread(target=run, daemon=True).start()


def sign_up(name, email, password, reason):
    name = (name or "").strip()[:80]
    email = (email or "").strip().lower()
    reason = (reason or "").strip()[:200]
    if not name:
        raise AccountError("Add your name so we know who we're talking to.", 400, "invalid_input")
    if not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.", 400, "invalid_input")
    if len(password or "") < 8:
        raise AccountError("Use at least 8 characters for your password.", 400, "invalid_input")

    status, body = _auth("/auth/v1/signup", {"email": email, "password": password, "data": {"name": name}})
    if status >= 400:
        raise AccountError(_friendly_auth_error(body, "Could not create the account. Try again."), 400, "signup_failed")

    user = body.get("user") if isinstance(body, dict) and isinstance(body.get("user"), dict) else body
    if not isinstance(user, dict) or not user.get("id"):
        raise AccountError("Could not create the account. Try again.", 400, "signup_failed")

    _ensure_profile(user["id"], email, name, reason)
    session = _session_from(body)
    _send_welcome(email, name)
    return {
        "session": session,
        "needs_confirmation": session is None,
        "user": {"id": user["id"], "email": email, "name": name},
    }


def sign_in(email, password):
    email = (email or "").strip().lower()
    if not email or not password:
        raise AccountError("Enter your email and password.", 400, "invalid_input")
    status, body = _auth("/auth/v1/token?grant_type=password", {"email": email, "password": password})
    session = _session_from(body)
    if status >= 400 or not session:
        msg = str((body or {}).get("msg") or (body or {}).get("error_description") or "").lower()
        if "not confirmed" in msg:
            raise AccountError("Confirm your email first, then sign in.", 401, "invalid_login")
        raise AccountError("That email and password don't match. Try again.", 401, "invalid_login")
    user = body.get("user") or {}
    _ensure_profile(user["id"], email)
    return {"session": session, "user": {"id": user["id"], "email": email}}


def refresh_session(refresh_token):
    if not refresh_token:
        raise AccountError("Your session expired. Sign in again.", 401, "auth_required")
    status, body = _auth("/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh_token})
    session = _session_from(body)
    if status >= 400 or not session:
        raise AccountError("Your session expired. Sign in again.", 401, "auth_required")
    return {"session": session}


def _profile(user_id):
    rows = _rest("GET", "profiles", f"?id=eq.{user_id}&select=*")
    return rows[0] if rows else None


def _month_start_iso():
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _next_month_iso():
    now = datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc).isoformat()


def used_minutes(user_id):
    query = f"?user_id=eq.{user_id}&created_at=gte.{urllib.parse.quote(_month_start_iso(), safe='')}&select=minutes"
    rows = _rest("GET", "usage_events", query)
    return sum(int(r.get("minutes") or 0) for r in rows or [])


def account_status(user):
    profile = _profile(user["id"]) or {}
    plan = profile.get("plan") if profile.get("plan") in PLAN_LIMITS else "free"
    limit = PLAN_LIMITS[plan]
    used = used_minutes(user["id"])
    return {
        "email": user.get("email", ""),
        "name": profile.get("name") or "",
        "plan": plan,
        "plan_name": PLAN_NAMES[plan],
        "used_minutes": used,
        "limit_minutes": limit,
        "remaining_minutes": max(0, limit - used),
        "studio": plan in STUDIO_PLANS,
        "resets_at": _next_month_iso(),
    }


def reserve_usage(user, job_id, minutes, kind="clip", studio=False):
    """Check the plan and the monthly allowance, then record the minutes for this job.

    A job reserves the full length of its source video up front, so starting
    several jobs at once can't push a user past their limit.
    """
    status = account_status(user)
    if studio and status["plan"] not in STUDIO_PLANS:
        raise AccountError(
            "Studio is part of the Pro plan. Upgrade to make faceless videos with a neural voice and captions.",
            402, "plan_required", plan=status["plan"],
        )
    minutes = max(1, int(math.ceil(minutes)))
    if status["used_minutes"] + minutes > status["limit_minutes"]:
        left = status["remaining_minutes"]
        if left > 0:
            msg = f"This video is {minutes} min, but you have {left} min left this month."
        else:
            msg = f"You've used all {status['limit_minutes']} minutes on the {status['plan_name']} plan this month."
        raise AccountError(
            msg, 402, "limit_reached",
            plan=status["plan"], used_minutes=status["used_minutes"],
            limit_minutes=status["limit_minutes"], requested_minutes=minutes,
        )
    _rest(
        "POST", "usage_events", "",
        {"user_id": user["id"], "job_id": str(job_id), "kind": kind, "minutes": minutes},
        prefer="return=minimal",
    )
    return status


def record_upgrade_intent(user, plan):
    if plan not in PAID_PLANS:
        raise AccountError("Choose Creator or Pro.", 400, "invalid_input")
    _rest(
        "POST", "upgrade_intents", "",
        {"user_id": user["id"], "plan": plan},
        prefer="return=minimal",
    )
    name = PLAN_NAMES[plan]
    return f"Thanks, we've saved that you want {name}. Card payments open soon, and we'll email you as soon as you can upgrade."


def get_duration_minutes(url):
    """Length of the source video in whole minutes (rounded up), or None if it can't be read."""
    try:
        if url.startswith("upload://"):
            upload_id = url.replace("upload://", "").strip()
            path = safe_join_path(WORK_DIR, "uploads", f"{upload_id}.mp4")
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            seconds = float(out)
        else:
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
            seconds = float(info.get("duration") or 0)
    except Exception as e:
        print(f"[ACCOUNTS] Could not read video length: {e}")
        return None
    if seconds <= 0:
        return None
    return int(math.ceil(seconds / 60))


def studio_minutes(script):
    words = len((script or "").split())
    return max(1, int(math.ceil(words / STUDIO_WORDS_PER_MINUTE)))
