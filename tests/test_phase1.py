import os
import sys
import unittest
import json
import tempfile
import time

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import security
import jobs_db
import process_manager
import server
from storage import get_job_work_dir, get_job_out_dir, cleanup_job_work_dir

class TestPhase1Reliability(unittest.TestCase):

    def setUp(self):
        jobs_db.init_db()
        self.app = server.app.test_client()

    def test_security_valid_urls(self):
        valid = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.tiktok.com/@scout2015/video/6718335390845095173",
            "https://www.twitch.tv/videos/12345678",
            "https://instagram.com/reel/C12345678"
        ]
        for url in valid:
            self.assertTrue(security.validate_video_url(url))

    def test_security_ssrf_blocking(self):
        malicious = [
            "http://localhost:5000/admin",
            "http://127.0.0.1/etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/internal",
            "http://192.168.1.1/router",
            "file:///etc/passwd",
            "ftp://files.internal/secret.txt"
        ]
        for url in malicious:
            with self.assertRaises(ValueError):
                security.validate_video_url(url)

    def test_security_path_traversal(self):
        base = "/tmp/test_storage"
        with self.assertRaises(PermissionError):
            security.safe_join_path(base, "../../../etc/passwd")

        safe = security.safe_join_path(base, "job_123/clip.mp4")
        self.assertTrue(safe.startswith(base))

    def test_jobs_db_crud(self):
        jid = f"test_{int(time.time()*1000)}"
        job = jobs_db.create_job(
            jid,
            job_type="clipper",
            url="https://youtube.com/watch?v=123",
            title="Test Batch",
            options={"count": 3, "duration": "30to60"}
        )
        self.assertEqual(job["id"], jid)
        self.assertEqual(job["status"], "queued")

        jobs_db.update_job(jid, status="processing", stage="Transcribing...", progress=45)
        fetched = jobs_db.get_job(jid)
        self.assertEqual(fetched["status"], "processing")
        self.assertEqual(fetched["stage"], "Transcribing...")
        self.assertEqual(fetched["progress"], 45)

        # Save clip record
        clip_data = {"index": 1, "title": "Highlight 1", "score": 95, "file": "clip1.mp4"}
        jobs_db.save_clip_record(jid, 1, clip_data)
        updated = jobs_db.get_job(jid)
        self.assertEqual(len(updated["clips"]), 1)
        self.assertEqual(updated["clips"][0]["file"], "clip1.mp4")

    def test_duration_parsing(self):
        min_l, max_l = server.parse_duration_options({"duration": "under30"})
        self.assertEqual((min_l, max_l), (15, 30))

        min_l, max_l = server.parse_duration_options({"duration": "30to60"})
        self.assertEqual((min_l, max_l), (30, 60))

        min_l, max_l = server.parse_duration_options({"duration": "1to3m"})
        self.assertEqual((min_l, max_l), (60, 180))

    def test_health_check_endpoint(self):
        resp = self.app.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("storage", data)
        self.assertTrue(data["capabilities"]["ssrf_protection"])

    def test_job_creation_and_cancellation(self):
        resp = self.app.post("/api/jobs", json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "duration": "30to60",
            "count": 3
        })
        self.assertEqual(resp.status_code, 200)
        jid = resp.get_json()["id"]

        # Check job status
        status_resp = self.app.get(f"/api/jobs/{jid}")
        self.assertEqual(status_resp.status_code, 200)
        jdata = status_resp.get_json()
        self.assertEqual(jdata["id"], jid)

        # Cancel job
        cancel_resp = self.app.post(f"/api/jobs/{jid}/cancel")
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(cancel_resp.get_json()["status"], "Cancelled")

        # Confirm cancellation in DB
        db_job = jobs_db.get_job(jid)
        self.assertTrue(db_job["cancelled"])


    def test_download_format_fallback(self):
        from unittest.mock import patch
        import download
        import shutil

        attempt_formats = []

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts
                attempt_formats.append(opts.get("format"))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def extract_info(self, url, download=True):
                if len(attempt_formats) == 1:
                    raise RuntimeError("Requested format is not available")
                return {"id": "test_fallback_id", "ext": "mp4"}
            def prepare_filename(self, info):
                return self.opts["outtmpl"] % info

        target_dir = tempfile.mkdtemp()
        dummy_file = os.path.join(target_dir, "source_test_fallback_id.mp4")
        with open(dummy_file, "w") as f:
            f.write("dummy")

        with patch("yt_dlp.YoutubeDL", FakeYDL):
            cand, vid = download.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", dest_dir=target_dir)
            self.assertEqual(vid, "test_fallback_id")
            self.assertEqual(len(attempt_formats), 2)

        shutil.rmtree(target_dir, ignore_errors=True)
if __name__ == "__main__":
    unittest.main()
