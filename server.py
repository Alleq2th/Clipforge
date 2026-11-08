import os, threading, uuid, json, time, re, urllib.request, urllib.parse
from flask import Flask, jsonify, request, send_from_directory
from config import OUT_DIR, WORK_DIR
from llm import providers
from pipeline import run
from storage import get_dir_size_mb, cleanup_storage
from faceless import create_faceless_video, VOICES, STYLES

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

    # 3. Instagram, Twitch, Facebook, Rumble, X/Twitter, or generic video link
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

def work(jid, url, options):
    try:
        if jid in jobs:
            jobs[jid]["clips"] = []

        def on_status(m):
            if jid in jobs:
                jobs[jid]["status"] = m
                save_job(jid)

        def on_clip(clip_item, idx, total):
            if jid in jobs:
                if "clips" not in jobs[jid]:
                    jobs[jid]["clips"] = []
                jobs[jid]["clips"].append(clip_item)
                jobs[jid]["status"] = f"Generated clip {idx} of {total} • Ready to watch and download!"
                save_job(jid)

        clips = run(url, cb=on_status, on_clip=on_clip, options=options)
        if jid in jobs:
            jobs[jid]["clips"] = clips
            jobs[jid]["done"] = True
            jobs[jid]["status"] = "Done"
            save_job(jid)
    except Exception as e:
        if jid in jobs:
            jobs[jid].update(error=str(e), status="Failed")
            save_job(jid)

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
    threading.Thread(target=work, args=(jid, url, options), daemon=True).start()
    return jsonify({"id": jid})

@app.get("/api/jobs/<jid>")
def status(jid):
    j = load_job(jid)
    if not j: return jsonify({"error": "Job not found"}), 404
    return jsonify(j)

@app.get("/api/history")
def history():
    saved = []
    p = os.path.join(WORK_DIR, "jobs")
    if os.path.exists(p):
        for f in sorted(os.listdir(p), reverse=True):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(p, f), "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                        # Save if job has any clips generated, even if still running!
                        if data.get("clips") and len(data.get("clips", [])) > 0:
                            saved.append(data)
                except Exception:
                    pass
    return jsonify({"jobs": saved[:30]})

@app.get("/clips/<vid>/<filename>")
def get_clip(vid, filename):
    d = os.path.join(OUT_DIR, vid)
    dl = request.args.get("dl") == "1"
    return send_from_directory(d, filename, as_attachment=dl)

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

def faceless_work(fid, url, script, voice, style):
    try:
        def on_status(m):
            if fid in faceless_jobs:
                faceless_jobs[fid]["status"] = m
                save_faceless_job(fid)

        res = create_faceless_video(url, script, voice_key=voice, caption_style=style, fid=fid, cb=on_status)
        if fid in faceless_jobs:
            faceless_jobs[fid]["result"] = res
            faceless_jobs[fid]["done"] = True
            faceless_jobs[fid]["status"] = "Done"
            save_faceless_job(fid)
    except Exception as e:
        if fid in faceless_jobs:
            faceless_jobs[fid].update(error=str(e), status="Failed")
            save_faceless_job(fid)

@app.post("/api/faceless")
def create_faceless():
    data = request.json or {}
    script = data.get("script", "").strip()
    if not script:
        return jsonify({"error": "Script text is required"}), 400
    url = data.get("url", "").strip()
    voice = data.get("voice", "deep_narrator")
    style = data.get("style", "beasty")
    fid = str(uuid.uuid4())[:8]
    faceless_jobs[fid] = {
        "id": fid,
        "status": "Starting faceless video engine...",
        "done": False,
        "script": script,
        "voice": voice,
        "style": style,
        "created_at": time.time()
    }
    save_faceless_job(fid)
    threading.Thread(target=faceless_work, args=(fid, url, script, voice, style), daemon=True).start()
    return jsonify({"id": fid})

@app.get("/api/faceless/<fid>")
def faceless_status(fid):
    j = load_faceless_job(fid)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(j)

@app.get("/faceless/<fid>/<filename>")
def get_faceless_file(fid, filename):
    d = os.path.join(OUT_DIR, "faceless", fid)
    dl = request.args.get("dl") == "1"
    return send_from_directory(d, filename, as_attachment=dl)

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)), debug=True)
