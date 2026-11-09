import os, threading, uuid, json, time, re, urllib.request, urllib.parse
from flask import Flask, jsonify, request, send_from_directory
from config import OUT_DIR, WORK_DIR
from llm import providers
from pipeline import run
from storage import get_dir_size_mb, cleanup_storage
from faceless import create_faceless_video, VOICES, STYLES
from library import get_library, save_library_entry, delete_library_entry
from notifications import send_completion_email, build_clipper_email, build_faceless_email
from voice_profiles import list_voice_profiles, create_voice_profile, delete_voice_profile

app = Flask(__name__, static_folder="static", static_url_path="")
jobs = {}
faceless_jobs = {}

def get_video_meta(url):
    domain = urllib.parse.urlparse(url).netloc.lower()

    # 1. YouTube
    m = re.search(r"(?:v=|\/|youtu\.be\/|embed\/)([a-zA-Z0-9_-]{11})", url)
    if m:
        vid = m.group(1)
        thumb = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
        title = f"YouTube Video ({vid})"
        author = "YouTube"
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as res:
                data = json.loads(res.read().decode())
                title = data.get("title", title)
                author = data.get("author_name", author)
        except Exception:
            pass
        return {"ok": True, "id": vid, "title": title, "author": author, "thumbnail": thumb, "platform": "YouTube"}

    # 2. TikTok
    if "tiktok.com" in domain:
        try:
            oembed_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as res:
                data = json.loads(res.read().decode())
                return {
                    "ok": True,
                    "id": str(uuid.uuid4())[:8],
                    "title": data.get("title") or "TikTok Video",
                    "author": data.get("author_name") or "TikTok",
                    "thumbnail": data.get("thumbnail_url") or "",
                    "platform": "TikTok"
                }
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
        "title": f"{platform_name} Stream / Clip",
        "author": domain,
        "thumbnail": "",
        "platform": platform_name
    }

def job_path(jid):
    return os.path.join(WORK_DIR, "jobs", f"{jid}.json")

def save_job(jid):
    try:
        os.makedirs(os.path.join(WORK_DIR, "jobs"), exist_ok=True)
        with open(job_path(jid), "w", encoding="utf-8") as f:
            json.dump(jobs.get(jid, {}), f)
    except Exception:
        pass

def load_job(jid):
    if jid in jobs:
        return jobs[jid]
    p = job_path(jid)
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
            c_vid = clip_item.get("vid", "")
            clip_record = {
                **clip_item,
                "download_url": f"/clips/{c_vid}/{c_file}?dl=1",
                "stream_url": f"/clips/{c_vid}/{c_file}"
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

        clips = run(url, cb=on_status, on_clip=on_clip, options=options)
        formatted_clips = []
        for c in clips:
            formatted_clips.append({
                **c,
                "download_url": f"/clips/{c.get('vid')}/{c.get('file')}?dl=1",
                "stream_url": f"/clips/{c.get('vid')}/{c.get('file')}"
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

        # Trigger completion email
        recipient = options.get("email") or os.getenv("NOTIFICATION_EMAIL")
        if recipient:
            html, text = build_clipper_email(lib_entry["title"], formatted_clips, len(formatted_clips), app_url=app_url)
            send_completion_email(to_email=recipient, subject=f"🎉 ClipForge: {len(formatted_clips)} viral clips ready!", html_content=html, text_content=text)

    except Exception as e:
        if jid in jobs:
            jobs[jid].update(error=str(e), status="Failed")
            save_job(jid)
        lib_entry["status"] = f"Failed: {e}"
        save_library_entry(lib_entry)

@app.get("/api/preview")
def preview():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    meta = get_video_meta(url)
    return jsonify(meta)

@app.get("/api/storage")
def storage_info():
    out_mb = get_dir_size_mb(OUT_DIR)
    work_mb = get_dir_size_mb(WORK_DIR)
    return jsonify({
        "output_mb": round(out_mb, 1),
        "work_mb": round(work_mb, 1),
        "total_mb": round(out_mb + work_mb, 1),
        "limit_mb": 400
    })

@app.post("/api/jobs")
def create():
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url: return jsonify({"error": "URL required"}), 400

    count = int(data.get("count", 5))
    dur = data.get("duration", "30to60")
    if dur == "under30": min_len, max_len = 10, 29
    elif dur == "1to3m": min_len, max_len = 60, 180
    elif dur == "auto": min_len, max_len = 15, 90
    else: min_len, max_len = 30, 60

    options = {
        "count": count,
        "min_len": min_len,
        "max_len": max_len,
        "focus": data.get("focus", "viral"),
        "aspect_ratio": data.get("aspect_ratio", "9:16"),
        "caption_style": data.get("caption_style", "beasty"),
        "framing": data.get("framing", "crop"),
        "email": (data.get("email") or "").strip()
    }

    meta = get_video_meta(url)
    jid = str(uuid.uuid4())
    jobs[jid] = {
        "id": jid,
        "status": "Queued...",
        "done": False,
        "clips": [],
        "preview": meta,
        "options": options,
        "created_at": time.time()
    }
    save_job(jid)

    app_url = request.host_url.rstrip("/")
    threading.Thread(target=work, args=(jid, url, options, app_url), daemon=True).start()
    return jsonify({"id": jid})

@app.get("/api/jobs/<jid>")
def status(jid):
    j = load_job(jid)
    if not j: return jsonify({"error": "Job not found"}), 404
    return jsonify(j)

# ================= PERSISTENT LIBRARY API =================
@app.get("/api/library")
def library_api():
    lib = get_library()
    return jsonify({"library": lib})

@app.delete("/api/library/<entry_id>")
def delete_library_item(entry_id):
    ok = delete_library_entry(entry_id)
    return jsonify({"ok": ok})

@app.get("/api/history")
def history_compat():
    lib = get_library()
    return jsonify({"jobs": lib})

# ================= BULLETPROOF UNIVERSAL DOWNLOAD / STREAM =================
@app.get("/clips/<path:filepath>")
def get_clip(filepath):
    full_path = os.path.abspath(os.path.join(OUT_DIR, filepath))
    if not full_path.startswith(os.path.abspath(OUT_DIR)):
        return jsonify({"error": "Access denied"}), 403
    if not os.path.exists(full_path):
        return jsonify({"error": "File not found"}), 404

    directory = os.path.dirname(full_path)
    filename = os.path.basename(full_path)
    dl = request.args.get("dl") == "1"
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
    # Handle both multipart and json
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
    except Exception:
        pass

def load_faceless_job(fid):
    if fid in faceless_jobs:
        return faceless_jobs[fid]
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

        res = create_faceless_video(url, script, voice_key=voice, caption_style=style, framing=framing, fid=fid, cb=on_status)
        clip_record = {
            "file": res["filename"],
            "vid": f"faceless/{fid}",
            "title": "GTA 6 Faceless Video",
            "score": 98,
            "category": "GTA 6 Faceless",
            "hook_reason": "Neural narration with zero original audio and word-synced captions.",
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

        # Trigger completion email
        recipient = email or os.getenv("NOTIFICATION_EMAIL")
        if recipient:
            html, text = build_faceless_email(lib_entry["title"], res["duration"], res["file_url"], script, app_url=app_url)
            send_completion_email(to_email=recipient, subject="🎮 GTA 6 Faceless Video Ready!", html_content=html, text_content=text)

    except Exception as e:
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
    voice = data.get("voice", "deep_narrator")
    style = data.get("style", "beasty")
    framing = data.get("framing", "crop")
    email = (data.get("email") or "").strip()

    fid = str(uuid.uuid4())[:8]
    faceless_jobs[fid] = {
        "id": fid,
        "status": "Starting faceless video engine...",
        "done": False,
        "script": script,
        "voice": voice,
        "style": style,
        "framing": framing,
        "email": email,
        "created_at": time.time()
    }
    save_faceless_job(fid)

    app_url = request.host_url.rstrip("/")
    threading.Thread(target=faceless_work, args=(fid, url, script, voice, style, framing, email, app_url), daemon=True).start()
    return jsonify({"id": fid})

@app.get("/api/faceless/<fid>")
def faceless_status(fid):
    j = load_faceless_job(fid)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(j)

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)), debug=True)
