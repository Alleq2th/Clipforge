import os
import sys
import json
import time
import uuid
import urllib.parse
import urllib.request
from flask import Flask, request, jsonify, send_from_directory, send_file, redirect
from config import DEFAULT_EXPORT_PROFILE, WORK_DIR, OUT_DIR
from pipeline import run, cancel_review, set_confirmed_clips
from cut import cut
from storage import get_dir_size_mb, cleanup_storage, get_job_out_dir, cleanup_job_work_dir, get_job_work_dir, get_storage_provider
from faceless import create_faceless_video, VOICES, STYLES
from library import get_library, save_library_entry, delete_library_entry, delete_library_clip, clear_completed_entries
from notifications import send_completion_email, build_clipper_email, build_faceless_email
from voice_profiles import list_voice_profiles, create_voice_profile, delete_voice_profile, rename_voice_profile, get_voice_preview_file
from security import validate_video_url, safe_join_path
from jobs_db import init_db, create_job as db_create_job, update_job as db_update_job, get_job as db_get_job, list_jobs as db_list_jobs
from process_manager import submit_job, terminate_job_processes, MAX_CONCURRENT_JOBS
from memory_guard import get_memory_status

app = Flask(__name__, static_folder="static", static_url_path="")

# Initialize SQLite database on startup
init_db()

# In-memory caches with database fallback
jobs = {}
faceless_jobs = {}
_start_time = time.time()

def parse_duration_options(data):
    """
    Harmonizes frontend duration chips ('under30', '30to60', '1to3m', 'auto')
    with backend min_len and max_len settings.
    """
    duration_str = str(data.get("duration", "")).strip().lower()
    
    if duration_str == "under30":
        return 15, 30
    elif duration_str == "30to60":
        return 30, 60
    elif duration_str == "1to3m":
        return 60, 180
    elif duration_str == "auto":
        return 20, 60
    
    # Fallback to direct integers if provided
    try:
        min_l = int(data.get("min_len", 20))
        max_l = int(data.get("max_len", 60))
        return max(5, min_l), max(min_l + 5, max_l)
    except Exception:
        return 20, 60

def get_video_meta(url):
    if url.startswith("upload://"):
        upload_id = url.replace("upload://", "").strip()
        thumb_p = safe_join_path(WORK_DIR, "uploads", f"{upload_id}_thumb.jpg")
        has_thumb = os.path.exists(thumb_p)
        return {
            "ok": True,
            "id": upload_id,
            "title": f"Uploaded Video ({upload_id})",
            "author": "Local Upload",
            "thumbnail": f"/api/uploads/{upload_id}/thumbnail" if has_thumb else "",
            "platform": "File Upload"
        }
    domain = urllib.parse.urlparse(url).netloc.lower()

    # 1. YouTube
    if "youtube.com" in domain or "youtu.be" in domain:
        yt_id = None
        if "youtu.be" in domain:
            yt_id = url.split("youtu.be/")[1].split("?")[0].split("/")[0]
        elif "watch" in url:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            yt_id = qs.get("v", [None])[0]
        elif "shorts" in url:
            yt_id = url.split("/shorts/")[1].split("?")[0].split("/")[0]

        if yt_id:
            title = f"YouTube Video ({yt_id})"
            author = "YouTube Creator"
            try:
                oembed = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={yt_id}&format=json"
                req = urllib.request.Request(oembed, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    d = json.loads(resp.read().decode())
                    title = d.get("title", title)
                    author = d.get("author_name", author)
            except Exception:
                pass
            return {"ok": True, "id": yt_id, "title": title, "author": author, "thumbnail": f"https://i.ytimg.com/vi/{yt_id}/hqdefault.jpg", "platform": "YouTube"}

    # 2. TikTok
    if "tiktok.com" in domain:
        try:
            oembed = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
            req = urllib.request.Request(oembed, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode())
                return {"ok": True, "id": str(uuid.uuid4())[:8], "title": d.get("title") or "TikTok Viral Clip", "author": d.get("author_name") or "TikTok Creator", "thumbnail": d.get("thumbnail_url") or "", "platform": "TikTok"}
        except Exception:
            return {"ok": True, "id": str(uuid.uuid4())[:8], "title": "TikTok Video", "author": "TikTok", "thumbnail": "", "platform": "TikTok"}

    # 3. Instagram, Twitch, Facebook, Rumble, X/Twitter
    platform_name = "Web Video"
    if "twitch.tv" in domain: platform_name = "Twitch"
    elif "instagram.com" in domain: platform_name = "Instagram"
    elif "facebook.com" in domain or "fb.watch" in domain: platform_name = "Facebook"
    elif "rumble.com" in domain: platform_name = "Rumble"
    elif "twitter.com" in domain or "x.com" in domain: platform_name = "X / Twitter"

    return {
        "ok": True,
        "id": str(uuid.uuid4())[:8],
        "title": f"{platform_name} Stream / Video",
        "author": platform_name,
        "thumbnail": "",
        "platform": platform_name
    }

def save_job(jid):
    # Backward compatible JSON file save + database update
    try:
        j = jobs.get(jid, {})
        db_cur = db_get_job(jid)
        if db_cur and db_cur.get("cancelled"):
            j["cancelled"] = True
            j["status"] = "Cancelled by user"
            j["stage"] = "Cancelled by user"
        os.makedirs(os.path.join(WORK_DIR, "jobs"), exist_ok=True)
        with open(os.path.join(WORK_DIR, "jobs", f"{jid}.json"), "w", encoding="utf-8") as f:
            json.dump(j, f)
        
        db_update_job(
            jid,
            status=j.get("status"),
            stage=j.get("stage") or j.get("status"),
            clips=j.get("clips", []),
            candidates=j.get("candidates", []),
            error=j.get("error"),
            done=j.get("done", False),
            cancelled=j.get("cancelled", False)
        )
    except Exception as e:
        print(f"[SERVER] Notice saving job {jid}: {e}")

def load_job(jid):
    # Check SQLite database first for authoritative state
    db_j = db_get_job(jid)
    if db_j:
        jobs[jid] = db_j
        return db_j
    if jid in jobs:
        return jobs[jid]
    # Fallback to file on disk
    p = os.path.join(WORK_DIR, "jobs", f"{jid}.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                jobs[jid] = data
                return data
        except Exception:
            pass
    return None

def work(jid, url, options, app_url=""):
    try:
        meta = jobs.get(jid, {}).get("preview", {})
        lib_entry = {
            "id": jid,
            "type": "clipper",
            "title": meta.get("title") or "Video Batch",
            "thumbnail": meta.get("thumbnail") or "",
            "status": "Starting pipeline...",
            "done": False,
            "total_target": options.get("count", 5),
            "clips_count": 0,
            "clips": [],
            "options": options
        }
        save_library_entry(lib_entry)

        def on_status(m):
            if jid in jobs:
                jobs[jid]["status"] = m
                save_job(jid)
            lib_entry["status"] = m
            save_library_entry(lib_entry)

        def on_clip(clip_item, idx, total):
            c_file = clip_item.get("file", "")
            clip_record = {
                **clip_item,
                "download_url": f"/clips/{jid}/{c_file}?dl=1",
                "stream_url": f"/clips/{jid}/{c_file}"
            }
            if jid in jobs:
                if "clips" not in jobs[jid]:
                    jobs[jid]["clips"] = []
                jobs[jid]["clips"].append(clip_record)
                jobs[jid]["status"] = f"Generated clip {idx} of {total} • Ready to watch and download!"
                save_job(jid)

            lib_entry["clips"].append(clip_record)
            lib_entry["clips_count"] = len(lib_entry["clips"])
            lib_entry["status"] = f"Generated clip {idx} of {total} • Ready to download!"
            save_library_entry(lib_entry)

        def check_cancelled():
            return jobs.get(jid, {}).get("cancelled", False)

        clips = run(url, jid=jid, cb=on_status, on_clip=on_clip, options=options, check_cancelled=check_cancelled)

        if check_cancelled():
            if jid in jobs:
                jobs[jid]["status"] = "Cancelled by user"
                jobs[jid]["done"] = True
                save_job(jid)
            lib_entry["status"] = "Cancelled by user"
            lib_entry["done"] = True
            save_library_entry(lib_entry)
            return

        formatted_clips = []
        for c in clips:
            c_file = c.get("file", "")
            formatted_clips.append({
                **c,
                "download_url": f"/clips/{jid}/{c_file}?dl=1" if c_file else "",
                "stream_url": f"/clips/{jid}/{c_file}" if c_file else ""
            })

        if jid in jobs:
            jobs[jid]["clips"] = formatted_clips
            jobs[jid]["done"] = True
            jobs[jid]["status"] = "Done"
            save_job(jid)

        lib_entry["clips"] = formatted_clips
        lib_entry["clips_count"] = len(formatted_clips)
        lib_entry["done"] = True
        lib_entry["status"] = "Done"
        save_library_entry(lib_entry)

        recipient = options.get("email") or jobs.get(jid, {}).get("options", {}).get("email") or os.getenv("NOTIFICATION_EMAIL")
        if recipient and len(formatted_clips) > 0:
            html, text = build_clipper_email(lib_entry["title"], formatted_clips, len(formatted_clips), app_url=app_url)
            send_completion_email(to_email=recipient, subject=f"🎉 ClipForge: {len(formatted_clips)} viral clips ready!", html_content=html, text_content=text)

    except Exception as e:
        db_state = db_get_job(jid) or {}
        is_cancelled = (
            check_cancelled()
            or jobs.get(jid, {}).get("cancelled", False)
            or bool(db_state.get("cancelled"))
            or "cancelled" in str(e).lower()
        )
        outcome = "cancelled" if is_cancelled else "failed"
        print(f"[WORKER] Job {jid} {outcome}: {e}")
        if jid in jobs:
            if is_cancelled:
                jobs[jid]["cancelled"] = True
                jobs[jid]["status"] = "Cancelled by user"
                jobs[jid]["stage"] = "Cancelled by user"
                jobs[jid]["done"] = True
            else:
                jobs[jid].update(error=str(e), status="Failed")
            save_job(jid)
        lib_entry["status"] = "Cancelled by user" if is_cancelled else f"Failed: {e}"
        lib_entry["done"] = True if is_cancelled else False
        save_library_entry(lib_entry)

# ================= CORE API ENDPOINTS =================

@app.get("/api/health")
def health_check():
    """System health check & diagnostics endpoint."""
    out_mb = get_dir_size_mb(OUT_DIR)
    work_mb = get_dir_size_mb(WORK_DIR)
    return jsonify({
        "status": "healthy",
        "uptime_seconds": int(time.time() - _start_time),
        "storage": {
            "output_mb": round(out_mb, 1),
            "work_mb": round(work_mb, 1),
            "total_mb": round(out_mb + work_mb, 1),
            "budget_mb": 400.0
        },
        "memory": get_memory_status(),
        "version": "2.0.0-phase1",
        "capabilities": {
            "database": "sqlite3-wal",
            "ssrf_protection": True,
            "bounded_queue": True,
            "adaptive_memory_guard": True,
            "independent_retry": True
        }
    })

@app.get("/api/preview")
def preview():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    try:
        validate_video_url(url)
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    meta = get_video_meta(url)
    return jsonify(meta)

@app.get("/api/storage")
def storage_info():
    out_mb = get_dir_size_mb(OUT_DIR)
    work_mb = get_dir_size_mb(WORK_DIR)
    provider = get_storage_provider()
    return jsonify({
        "output_mb": round(out_mb, 1),
        "work_mb": round(work_mb, 1),
        "total_mb": round(out_mb + work_mb, 1),
        "limit_mb": 400.0,
        "cleanup_count": 6,
        "memory": get_memory_status(),
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "cloud_storage": {
            "provider": provider.name,
            "configured": provider.is_configured(),
            "bucket": getattr(provider, "bucket", "clips")
        }
    })

@app.post("/api/cleanup")
def force_cleanup():
    cleanup_storage()
    return jsonify({"ok": True})


@app.post("/api/upload")
def upload_video():
    """
    Accepts direct MP4/MOV/MKV/WEBM file upload,
    saves cleanly into WORK_DIR/uploads/, extracts thumbnail, and returns preview metadata.
    """
    from security import sanitize_filename
    if "file" not in request.files and "video" not in request.files:
        return jsonify({"error": "No file uploaded. Please select a video file."}), 400

    uploaded_file = request.files.get("file") or request.files.get("video")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Empty filename."}), 400

    orig_name = sanitize_filename(uploaded_file.filename)
    ext = os.path.splitext(orig_name)[1].lower()
    allowed_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
    if ext not in allowed_exts:
        return jsonify({"error": f"Unsupported format '{ext}'. Please upload an MP4, MOV, WEBM, or MKV video."}), 400

    upload_id = str(uuid.uuid4())[:8]
    uploads_dir = safe_join_path(WORK_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    save_path = safe_join_path(uploads_dir, f"{upload_id}.mp4")
    uploaded_file.save(save_path)

    # Generate thumbnail frame with ffmpeg
    thumb_path = safe_join_path(uploads_dir, f"{upload_id}_thumb.jpg")
    try:
        import subprocess
        subprocess.run([
            "ffmpeg", "-y", "-ss", "00:00:01", "-i", save_path,
            "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "3", thumb_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception as e:
        print(f"[UPLOAD] Notice generating thumbnail: {e}")

    has_thumb = os.path.exists(thumb_path)
    thumb_url = f"/api/uploads/{upload_id}/thumbnail" if has_thumb else ""

    display_title = os.path.splitext(orig_name)[0].replace("_", " ").replace("-", " ").strip()
    if len(display_title) > 60:
        display_title = display_title[:57] + "..."

    return jsonify({
        "ok": True,
        "id": upload_id,
        "title": display_title or "Uploaded Video",
        "author": "Local Upload",
        "thumbnail": thumb_url,
        "platform": "File Upload",
        "url": f"upload://{upload_id}"
    })

@app.get("/api/uploads/<upload_id>/thumbnail")
def get_upload_thumbnail(upload_id):
    from security import sanitize_filename
    safe_id = sanitize_filename(upload_id)
    thumb_p = safe_join_path(WORK_DIR, "uploads", f"{safe_id}_thumb.jpg")
    if os.path.exists(thumb_p):
        return send_file(thumb_p, mimetype="image/jpeg")
    return jsonify({"error": "Thumbnail not found"}), 404

@app.post("/api/jobs/<jid>/email")
def set_job_email(jid):
    j = load_job(jid)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "Valid email address required"}), 400

    if jid in jobs:
        if "options" not in jobs[jid]:
            jobs[jid]["options"] = {}
        jobs[jid]["options"]["email"] = email
        save_job(jid)

    lib = get_library()
    for entry in lib:
        if str(entry.get("id")) == str(jid):
            if "options" not in entry:
                entry["options"] = {}
            entry["options"]["email"] = email
            save_library_entry(entry)
            break

    return jsonify({"ok": True, "email": email})

@app.post("/api/jobs")
def create():
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400

    # Validate video URL against SSRF and illegal protocols
    try:
        validate_video_url(url)
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

    jid = str(uuid.uuid4())[:8]
    min_len, max_len = parse_duration_options(data)

    options = {
        "count": min(20, max(1, int(data.get("count", 5)))),
        "duration": data.get("duration", "30to60"),
        "min_len": min_len,
        "max_len": max_len,
        "focus": data.get("focus", "viral"),
        "aspect_ratio": data.get("aspect_ratio", "9:16"),
        "caption_style": data.get("caption_style", "beasty"),
        "framing": data.get("framing", "crop"),
        "export_profile": data.get("export_profile") or data.get("resolution", DEFAULT_EXPORT_PROFILE),
        "review_clips": bool(data.get("review_clips", False)),
        "email": (data.get("email") or "").strip()
    }

    meta = get_video_meta(url)
    jobs[jid] = {
        "id": jid,
        "url": url,
        "preview": meta,
        "status": "Queued in processing pool...",
        "stage": "Queued in line...",
        "progress": 0,
        "done": False,
        "cancelled": False,
        "clips": [],
        "options": options,
        "created_at": time.time()
    }

    # Persist in SQLite
    db_create_job(jid, job_type="clipper", url=url, title=meta.get("title", "Video Batch"), thumbnail=meta.get("thumbnail", ""), options=options)
    save_job(jid)

    app_url = request.host_url.rstrip("/")
    # Submit to bounded worker queue (prevents server memory crashes)
    submit_job(work, jid, url, options, app_url)
    return jsonify({"id": jid})

@app.get("/api/jobs/<jid>")
def status(jid):
    j = load_job(jid)
    if not j: return jsonify({"error": "Job not found"}), 404
    return jsonify(j)

@app.get("/api/jobs/<jid>/review")
def get_job_review(jid):
    j = load_job(jid)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "id": jid,
        "status": j.get("status"),
        "stage": j.get("stage"),
        "candidates": j.get("candidates", []),
        "options": j.get("options", {}),
        "preview": j.get("preview", {})
    })

@app.post("/api/jobs/<jid>/confirm-review")
def confirm_job_review(jid):
    j = load_job(jid)
    if not j:
        return jsonify({"error": "Job not found"}), 404

    data = request.get_json(silent=True) or {}
    chosen_clips = data.get("clips")
    if not chosen_clips or not isinstance(chosen_clips, list):
        return jsonify({"error": "No clips provided for rendering"}), 400

    validated = []
    for idx, c in enumerate(chosen_clips, 1):
        try:
            start = float(c.get("start", 0))
            end = float(c.get("end", 0))
            if end <= start:
                continue
            validated.append({
                "start": start,
                "end": end,
                "title": str(c.get("title", f"Clip #{idx}")).strip(),
                "score": int(c.get("score", 90)),
                "category": str(c.get("category", "Viral Hook")),
                "hook_reason": str(c.get("hook_reason", "")),
                "caption": str(c.get("caption", "")),
                "hashtags": c.get("hashtags", []),
                "index": idx
            })
        except Exception:
            pass

    if not validated:
        return jsonify({"error": "At least one valid clip must be selected"}), 400

    set_confirmed_clips(jid, validated)
    if jid in jobs:
        jobs[jid]["status"] = "processing"
        jobs[jid]["stage"] = f"Confirmed {len(validated)} clip(s); preparing to render..."
        jobs[jid]["progress"] = 58
        save_job(jid)
    else:
        db_update_job(jid, status="processing", stage=f"Confirmed {len(validated)} clip(s); preparing to render...", progress=58)

    return jsonify({"ok": True, "count": len(validated)})

@app.get("/api/jobs/<jid>/source-video")
def get_source_video(jid):
    try:
        work_dir = get_job_work_dir(jid)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400
    if not os.path.exists(work_dir):
        return jsonify({"error": "Work directory not found"}), 404
    for fname in os.listdir(work_dir):
        if fname.endswith(".mp4") and not fname.startswith("tmp_"):
            return send_from_directory(work_dir, fname)
    return jsonify({"error": "Source video file not found"}), 404

@app.post("/api/jobs/<jid>/cancel")
def cancel_job(jid):
    j = load_job(jid)
    if not j: return jsonify({"error": "Job not found"}), 404

    jobs[jid]["cancelled"] = True
    jobs[jid]["status"] = "Cancelled by user"
    jobs[jid]["stage"] = "Cancelled by user"
    save_job(jid)

    # Cancel any active in-browser review wait
    cancel_review(jid)

    # Immediately terminate active subprocesses (ffmpeg / yt-dlp)
    terminate_job_processes(jid)
    cleanup_job_work_dir(jid)

    # Update library
    lib = get_library()
    for entry in lib:
        if str(entry.get("id")) == str(jid):
            entry["status"] = "Cancelled by user"
            entry["done"] = True
            save_library_entry(entry)
            break
    return jsonify({"ok": True, "status": "Cancelled"})

@app.post("/api/jobs/<jid>/retry-failed")
def retry_failed(jid):
    """
    Intelligent Retry:
    Instead of redownloading and re-transcribing a 1-hour video from scratch,
    re-renders only the clips that failed or are missing.
    """
    j = load_job(jid)
    if not j: return jsonify({"error": "Job not found"}), 404

    url = j.get("url")
    options = j.get("options", {})
    existing_clips = j.get("clips", [])
    failed_clips = [c for c in existing_clips if not c.get("ok") or not c.get("file")]

    jobs[jid]["cancelled"] = False
    jobs[jid]["done"] = False
    jobs[jid]["status"] = "Retrying failed clips..."
    save_job(jid)

    app_url = request.host_url.rstrip("/")
    submit_job(work, jid, url, options, app_url)
    return jsonify({"ok": True, "status": "Retrying", "failed_count": len(failed_clips)})

@app.post("/api/jobs/<jid>/clips/<int:clip_index>/retry")
def retry_single_clip(jid, clip_index):
    """Retries a single specific clip without restarting the entire video batch."""
    j = load_job(jid)
    if not j:
        return jsonify({"error": "Job not found"}), 404

    out_dir = get_job_out_dir(jid)
    transcript_cache = os.path.join(out_dir, "transcript.json")
    discovered_cache = os.path.join(out_dir, "discovered_clips.json")

    if not os.path.exists(discovered_cache):
        # Fall back to general retry
        return retry_failed(jid)

    try:
        with open(discovered_cache, "r", encoding="utf-8") as f:
            candidates = json.load(f)
        if clip_index < 1 or clip_index > len(candidates):
            return jsonify({"error": "Invalid clip index"}), 400
        
        target_candidate = candidates[clip_index - 1]
        options = j.get("options", {})

        # Run independent clip cut in background
        def do_single_retry():
            work_dir = os.path.join(WORK_DIR, "jobs", f"{jid}_retry_{clip_index}")
            os.makedirs(work_dir, exist_ok=True)
            try:
                from download import download
                video, vid = download(j["url"], dest_dir=work_dir)
                with open(transcript_cache, "r", encoding="utf-8") as tf:
                    segs = json.load(tf)
                f = cut(
                    video, segs, target_candidate, clip_index, vid,
                    aspect_ratio=options.get("aspect_ratio", "9:16"),
                    caption_style=options.get("caption_style", "beasty"),
                    framing=options.get("framing", "crop"),
                    export_profile=options.get("export_profile") or options.get("resolution", DEFAULT_EXPORT_PROFILE),
                    out_dir=out_dir,
                    jid=jid
                )
                filename = os.path.basename(f)
                # Update clip record
                clips = j.get("clips", [])
                for idx, c in enumerate(clips):
                    if c.get("index") == clip_index:
                        clips[idx] = {
                            **c,
                            "file": filename,
                            "ok": True,
                            "error": None,
                            "download_url": f"/clips/{jid}/{filename}?dl=1",
                            "stream_url": f"/clips/{jid}/{filename}"
                        }
                        break
                j["clips"] = clips
                save_job(jid)
            except Exception as e:
                print(f"[RETRY] Error retrying clip {clip_index}: {e}")
            finally:
                import shutil
                shutil.rmtree(work_dir, ignore_errors=True)

        submit_job(do_single_retry)
        return jsonify({"ok": True, "status": f"Retrying clip {clip_index}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= PERSISTENT LIBRARY API =================
@app.get("/api/library")
def library_api():
    lib = get_library()
    return jsonify({"library": lib})

@app.delete("/api/library/<entry_id>")
def delete_library_item(entry_id):
    ok = delete_library_entry(entry_id)
    return jsonify({"ok": ok})

@app.delete("/api/library/<entry_id>/clips/<path:filename>")
def delete_clip_item(entry_id, filename):
    ok = delete_library_clip(entry_id, filename)
    return jsonify({"ok": ok})

@app.post("/api/library/clear-completed")
def clear_completed_library():
    count = clear_completed_entries()
    return jsonify({"ok": True, "cleared_count": count})

@app.get("/api/history")
def history_compat():
    lib = get_library()
    return jsonify({"jobs": lib})

# ================= UNIVERSAL DOWNLOAD / STREAM WITH PATH TRAVERSAL GUARDS =================
@app.get("/clips/<path:filepath>")
def get_clip(filepath):
    try:
        full_path = safe_join_path(OUT_DIR, filepath)
    except PermissionError:
        return jsonify({"error": "Access denied"}), 403

    dl = request.args.get("dl") == "1"

    # If local file is missing (e.g. Render restart/sleep or pruned disk), redirect to cloud storage
    if not os.path.exists(full_path):
        provider = get_storage_provider()
        if provider and provider.is_configured():
            cloud_url = provider.get_public_url(filepath)
            return redirect(cloud_url, code=302)
        return jsonify({"error": "File not found"}), 404

    directory = os.path.dirname(full_path)
    filename = os.path.basename(full_path)
    response = send_from_directory(directory, filename, as_attachment=dl)
    if dl:
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

# ================= OPT-IN VOICE PROFILES API =================
@app.get("/api/voice-profiles")
def get_voice_profiles():
    profiles = list_voice_profiles()
    return jsonify({"profiles": profiles})

@app.post("/api/voice-profiles")
def create_profile():
    if request.content_type and "multipart/form-data" in request.content_type:
        name = request.form.get("name", "Custom Voice")
        consent = request.form.get("consent") in ("true", "1", "True")
        base_voice = request.form.get("base_voice", "en-US-ChristopherNeural")
        pitch = request.form.get("pitch", "+0Hz")
        rate = request.form.get("rate", "+0%")
        audio_file = request.files.get("audio")
        audio_bytes = audio_file.read() if audio_file else None
        filename = audio_file.filename if audio_file else ""
    else:
        data = request.json or {}
        name = data.get("name", "Custom Voice")
        consent = bool(data.get("consent"))
        base_voice = data.get("base_voice", "en-US-ChristopherNeural")
        pitch = data.get("pitch", "+0Hz")
        rate = data.get("rate", "+0%")
        audio_bytes = None
        filename = ""

    try:
        profile = create_voice_profile(
            name=name,
            consent=consent,
            base_voice=base_voice,
            pitch=pitch,
            rate=rate,
            audio_bytes=audio_bytes,
            filename=filename
        )
        return jsonify({"ok": True, "profile": profile})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to create profile: {e}"}), 500

@app.get("/api/voice-profiles/<vpid>/preview")
def preview_voice(vpid):
    p = get_voice_preview_file(vpid)
    if not p or not os.path.exists(p):
        return jsonify({"error": "Preview not available"}), 404
    return send_file(p, mimetype="audio/mpeg")

@app.put("/api/voice-profiles/<vpid>")
def rename_profile(vpid):
    data = request.json or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "Name required"}), 400
    ok = rename_voice_profile(vpid, new_name)
    return jsonify({"ok": ok})

@app.delete("/api/voice-profiles/<vpid>")
def delete_profile(vpid):
    ok = delete_voice_profile(vpid)
    return jsonify({"ok": ok})

# ================= GTA 6 FACELESS STUDIO ENDPOINTS =================
def faceless_job_path(fid):
    return os.path.join(WORK_DIR, "faceless_jobs", f"{fid}.json")

def save_faceless_job(fid):
    try:
        os.makedirs(os.path.join(WORK_DIR, "faceless_jobs"), exist_ok=True)
        with open(faceless_job_path(fid), "w", encoding="utf-8") as f:
            json.dump(faceless_jobs.get(fid, {}), f)
        
        j = faceless_jobs.get(fid, {})
        db_update_job(
            fid,
            status=j.get("status"),
            stage=j.get("status"),
            error=j.get("error"),
            done=j.get("done", False),
            cancelled=j.get("cancelled", False)
        )
    except Exception as e:
        print(f"[SERVER] Notice saving faceless job {fid}: {e}")

def load_faceless_job(fid):
    if fid in faceless_jobs:
        return faceless_jobs[fid]
    db_j = db_get_job(fid)
    if db_j:
        faceless_jobs[fid] = db_j
        return db_j
    p = faceless_job_path(fid)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                faceless_jobs[fid] = data
                return data
        except Exception:
            pass
    return None

def faceless_work(fid, url, script, voice, style, framing, email="", app_url=""):
    lib_entry = {
        "id": fid,
        "type": "faceless",
        "title": f"GTA 6: {script[:32]}...",
        "thumbnail": "",
        "status": "Synthesizing voiceover...",
        "done": False,
        "total_target": 1,
        "clips_count": 0,
        "clips": [],
        "script": script
    }
    save_library_entry(lib_entry)

    try:
        def on_status(m):
            if fid in faceless_jobs:
                faceless_jobs[fid]["status"] = m
                save_faceless_job(fid)
            lib_entry["status"] = m
            save_library_entry(lib_entry)

        def check_cancelled():
            return faceless_jobs.get(fid, {}).get("cancelled", False)

        res = create_faceless_video(url, script, voice_key=voice, caption_style=style, framing=framing, fid=fid, cb=on_status, check_cancelled=check_cancelled)

        if check_cancelled():
            if fid in faceless_jobs:
                faceless_jobs[fid]["status"] = "Cancelled by user"
                faceless_jobs[fid]["done"] = True
                save_faceless_job(fid)
            lib_entry["status"] = "Cancelled by user"
            lib_entry["done"] = True
            save_library_entry(lib_entry)
            return

        if not res:
            raise RuntimeError("Faceless video creation produced no output")

        clip_record = {
            "file": res["filename"],
            "vid": f"faceless/{fid}",
            "title": "GTA 6 Faceless Video",
            "score": 98,
            "category": "GTA 6 Faceless",
            "hook_reason": "Neural narration with word-synced captions.",
            "caption": script,
            "download_url": f"/clips/faceless/{fid}/{res['filename']}?dl=1",
            "stream_url": f"/clips/faceless/{fid}/{res['filename']}"
        }

        if fid in faceless_jobs:
            faceless_jobs[fid]["result"] = res
            faceless_jobs[fid]["done"] = True
            faceless_jobs[fid]["status"] = "Done"
            save_faceless_job(fid)

        lib_entry["clips"] = [clip_record]
        lib_entry["clips_count"] = 1
        lib_entry["done"] = True
        lib_entry["status"] = "Done"
        save_library_entry(lib_entry)

        recipient = email or os.getenv("NOTIFICATION_EMAIL")
        if recipient:
            html, text = build_faceless_email(lib_entry["title"], res["duration"], res["file_url"], script, app_url=app_url)
            send_completion_email(to_email=recipient, subject="🎮 GTA 6 Faceless Video Ready!", html_content=html, text_content=text)

    except Exception as e:
        print(f"[WORKER] Faceless job {fid} failed: {e}")
        if fid in faceless_jobs:
            faceless_jobs[fid].update(error=str(e), status="Failed")
            save_faceless_job(fid)
        lib_entry["status"] = f"Failed: {e}"
        save_library_entry(lib_entry)

@app.post("/api/faceless")
def create_faceless():
    data = request.json or {}
    script = data.get("script", "").strip()
    if not script:
        return jsonify({"error": "Script text is required"}), 400

    url = data.get("url", "").strip()
    if url:
        try:
            validate_video_url(url)
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400

    voice = data.get("voice", "deep_narrator")
    style = data.get("style", "beasty")
    framing = data.get("framing", "crop")
    email = (data.get("email") or "").strip()

    fid = str(uuid.uuid4())[:8]
    faceless_jobs[fid] = {
        "id": fid,
        "status": "Queued in processing pool...",
        "done": False,
        "cancelled": False,
        "url": url,
        "script": script,
        "voice": voice,
        "style": style,
        "framing": framing,
        "email": email,
        "created_at": time.time()
    }

    db_create_job(fid, job_type="faceless", url=url, title=f"GTA 6: {script[:32]}...", options={"voice": voice, "style": style})
    save_faceless_job(fid)

    app_url = request.host_url.rstrip("/")
    submit_job(faceless_work, fid, url, script, voice, style, framing, email, app_url)
    return jsonify({"id": fid})

@app.get("/api/faceless/<fid>")
def faceless_status(fid):
    j = load_faceless_job(fid)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(j)

@app.post("/api/faceless/<fid>/cancel")
def cancel_faceless(fid):
    f = load_faceless_job(fid)
    if not f: return jsonify({"error": "Job not found"}), 404

    faceless_jobs[fid]["cancelled"] = True
    faceless_jobs[fid]["status"] = "Cancelled by user"
    save_faceless_job(fid)

    terminate_job_processes(fid)
    cleanup_job_work_dir(f"faceless_{fid}")

    lib = get_library()
    for entry in lib:
        if str(entry.get("id")) == str(fid):
            entry["status"] = "Cancelled by user"
            entry["done"] = True
            save_library_entry(entry)
            break
    return jsonify({"ok": True, "status": "Cancelled"})

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
