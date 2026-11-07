import os, threading, uuid, json
from flask import Flask, jsonify, request, send_from_directory
from config import OUT_DIR, WORK_DIR
from llm import providers
from pipeline import run

app = Flask(__name__, static_folder="static", static_url_path="")
jobs = {}

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

def work(jid, url):
    try:
        def on_status(m):
            if jid in jobs:
                jobs[jid]["status"] = m
                save_job(jid)

        clips = run(url, on_status)
        if jid in jobs:
            jobs[jid]["clips"] = clips
            jobs[jid]["done"] = True
            jobs[jid]["status"] = "Done"
            save_job(jid)
    except Exception as e:
        if jid in jobs:
            jobs[jid].update(error=str(e), status="Failed")
            save_job(jid)

@app.post("/api/jobs")
def create():
    url = (request.json or {}).get("url", "").strip()
    if not url: return jsonify(error="Paste a YouTube link"), 400
    if not providers(): return jsonify(error="No AI key set"), 500
    jid = uuid.uuid4().hex[:8]
    jobs[jid] = {"status": "Starting...", "done": False, "clips": [], "error": None}
    save_job(jid)
    threading.Thread(target=work, args=(jid, url), daemon=True).start()
    return jsonify(id=jid)

@app.get("/api/jobs/<jid>")
def status(jid):
    j = load_job(jid)
    if not j:
        return jsonify({"error": "Job not found. The server may have restarted or the link expired."}), 404
    return jsonify(j)

@app.get("/clips/<vid>/<name>")
def clip(vid, name):
    return send_from_directory(os.path.abspath(os.path.join(OUT_DIR, vid)), name,
                               as_attachment=request.args.get("dl") == "1")

@app.get("/")
def index():
    return app.send_static_file("index.html") if os.path.exists("static/index.html") else "ClipForge API running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
