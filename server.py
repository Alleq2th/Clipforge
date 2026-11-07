import os, threading, uuid
from flask import Flask, jsonify, request, send_from_directory
from config import OUT_DIR
from llm import providers
from pipeline import run

app = Flask(__name__, static_folder="static", static_url_path="")
jobs = {}

def work(jid, url):
    try:
        jobs[jid]["clips"] = run(url, lambda m: jobs[jid].update(status=m))
        jobs[jid]["done"] = True
    except Exception as e:
        jobs[jid].update(error=str(e), status="Failed")

@app.post("/api/jobs")
def create():
    url = (request.json or {}).get("url", "").strip()
    if not url: return jsonify(error="Paste a YouTube link"), 400
    if not providers(): return jsonify(error="No AI key set"), 500
    jid = uuid.uuid4().hex[:8]
    jobs[jid] = {"status": "Starting...", "done": False, "clips": [], "error": None}
    threading.Thread(target=work, args=(jid, url), daemon=True).start()
    return jsonify(id=jid)

@app.get("/api/jobs/<jid>")
def status(jid):
    return jsonify(jobs.get(jid) or {"error": "Not found"})

@app.get("/clips/<vid>/<name>")
def clip(vid, name):
    return send_from_directory(os.path.abspath(os.path.join(OUT_DIR, vid)), name,
                               as_attachment=request.args.get("dl") == "1")

@app.get("/")
def index():
    return app.send_static_file("index.html") if os.path.exists("static/index.html") else "ClipForge API running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
