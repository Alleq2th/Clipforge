import os, threading, uuid, json, time, re, urllib.request
from flask import Flask, jsonify, request, send_from_directory
from config import OUT_DIR, WORK_DIR
from llm import providers
from pipeline import run
from storage import get_dir_size_mb, cleanup_storage

app = Flask(__name__, static_folder="static", static_url_path="")
jobs = {}

def get_yt_meta(url):
    m = re.search(r"(?:v=|\/|youtu\.be\/|embed\/)([a-zA-Z0-9_-]{11})", url)
    if not m:
        return {"error": "Invalid YouTube URL"}
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
    return {"ok": True, "id": vid, "title": title, "author": author, "thumbnail": thumb}

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
        def on_status(m):
            if jid in jobs:
                jobs[jid]["status"] = m
                save_job(jid)

        clips = run(url, on_status, options=options)
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
    meta = get_yt_meta(url)
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
    if not url: return jsonify(error="Paste a YouTube link"), 400
    if not providers(): return jsonify(error="No AI key set (Set GEMINI_API_KEY or GROQ_API_KEY)"), 500

    count = int(data.get("count", 5))
    dur = data.get("duration", "30to60")
    focus = data.get("focus", "viral")
    aspect_ratio = data.get("aspect_ratio", "9:16")
    caption_style = data.get("caption_style", "beasty")

    if dur == "under30":
        min_len, max_len = 15, 30
    elif dur == "30to60":
        min_len, max_len = 30, 60
    elif dur == "1to3m":
        min_len, max_len = 60, 180
    else:
        min_len, max_len = 20, 60

    options = {
        "count": max(1, min(count, 20)),
        "min_len": min_len,
        "max_len": max_len,
        "focus": focus,
        "aspect_ratio": aspect_ratio,
        "caption_style": caption_style,
    }

    meta = get_yt_meta(url)
    jid = uuid.uuid4().hex[:8]
    jobs[jid] = {
        "id": jid,
        "url": url,
        "preview": meta,
        "options": options,
        "status": "Starting...",
        "done": False,
        "clips": [],
        "error": None,
        "created_at": time.time(),
    }
    save_job(jid)
    threading.Thread(target=work, args=(jid, url, options), daemon=True).start()
    return jsonify(id=jid, preview=meta)

@app.get("/api/jobs/<jid>")
def status(jid):
    j = load_job(jid)
    if not j:
        return jsonify({"error": "Job not found. The server may have restarted or the link expired."}), 404
    return jsonify(j)

@app.get("/api/history")
def history():
    jobs_dir = os.path.join(WORK_DIR, "jobs")
    if not os.path.exists(jobs_dir):
        return jsonify({"jobs": []})
    out = []
    try:
        files = sorted(
            [os.path.join(jobs_dir, f) for f in os.listdir(jobs_dir) if f.endswith(".json")],
            key=os.path.getmtime,
            reverse=True
        )
        for f in files[:25]:
            try:
                with open(f, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                    if data.get("clips") or data.get("done"):
                        out.append(data)
            except Exception:
                continue
    except Exception:
        pass
    return jsonify({"jobs": out})

@app.get("/clips/<vid>/<name>")
def clip(vid, name):
    return send_from_directory(os.path.abspath(os.path.join(OUT_DIR, vid)), name,
                               as_attachment=request.args.get("dl") == "1")

@app.get("/")
def index():
    return app.send_static_file("index.html") if os.path.exists("static/index.html") else "ClipForge API running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
