import os
import json
import time
import pytest
from server import app, resume_unfinished_jobs
import jobs_db
from jobs_db import create_job, update_job, get_job

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_resume_unfinished_jobs_handles_states(monkeypatch):
    resumed = []
    def fake_submit(fn, jid, url, opt, app_url=None):
        resumed.append(jid)
    
    import server
    monkeypatch.setattr(server, "submit_job", fake_submit)

    # 1. Old job > 24h should expire
    create_job("test_exp", job_type="clipper", url="https://youtube.com/watch?v=1", options={})
    with jobs_db._lock:
        conn = jobs_db.get_connection()
        with conn:
            conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (time.time() - 90000, "test_exp"))

    # 2. Awaiting review should be preserved without auto-submitting
    create_job("test_review", job_type="clipper", url="https://youtube.com/watch?v=2", options={})
    update_job("test_review", status="awaiting_review")

    # 3. Incomplete job should be resumed
    create_job("test_resume", job_type="clipper", url="https://youtube.com/watch?v=3", options={"count": 3})
    update_job("test_resume", status="Rendering clip 1 of 3")

    resume_unfinished_jobs()

    exp_job = get_job("test_exp")
    assert exp_job["done"] is True
    assert "Expired" in exp_job["status"]

    review_job = get_job("test_review")
    assert review_job["status"] == "awaiting_review"
    assert "test_review" not in resumed

    assert "test_resume" in resumed

def test_single_clip_retry_route_validation(client, monkeypatch):
    monkeypatch.setattr(server := __import__("server"), "submit_job", lambda *args, **kwargs: None)

    # Test non-existent job
    res = client.post("/api/jobs/non_existent_job_12345/clips/0/retry")
    assert res.status_code == 404
    assert "not found" in res.get_json()["error"].lower()

    # Create a job with clips
    create_job("test_retry_job", job_type="clipper", url="https://youtube.com/watch?v=abc", options={"count": 2})
    clips = [
        {"ok": True, "file": "clip1.mp4", "index": 1},
        {"ok": False, "file": None, "error": "FFmpeg exit code 1", "index": 2}
    ]
    update_job("test_retry_job", clips=clips, done=True)

    # Test invalid clip index
    res_inv = client.post("/api/jobs/test_retry_job/clips/99/retry")
    assert res_inv.status_code == 404

    # Test valid retry of clip index 1 (the 2nd clip)
    res_ok = client.post("/api/jobs/test_retry_job/clips/1/retry")
    assert res_ok.status_code == 200
    data = res_ok.get_json()
    assert data["ok"] is True
    assert data["index"] == 2
