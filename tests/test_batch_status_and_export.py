import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import server
import pick
import pipeline
import library

class TestBatchStatusAndExport(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.test_dir = tempfile.mkdtemp()
        self.old_work_dir = server.WORK_DIR
        self.old_out_dir = server.OUT_DIR
        server.WORK_DIR = self.test_dir
        server.OUT_DIR = self.test_dir

    def tearDown(self):
        server.WORK_DIR = self.old_work_dir
        server.OUT_DIR = self.old_out_dir
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clip_run_report_structure(self):
        """Report contains requested, discovered, completed, failed, and stopped_reason."""
        report = pick.clip_run_report(
            requested=20,
            produced=6,
            min_len=20,
            max_len=60,
            total_duration=120,
            relaxed=False,
            discovered=7,
            failed=1,
            stopped_reason="Rendered 6 clip(s) successfully; 1 clip render(s) failed."
        )
        self.assertEqual(report["requested"], 20)
        self.assertEqual(report["discovered"], 7)
        self.assertEqual(report["completed"], 6)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["shortfall"], True)
        self.assertEqual(report["stopped_reason"], "Rendered 6 clip(s) successfully; 1 clip render(s) failed.")
        self.assertIn("6", report["stopped_reason"])

    def test_clip_run_report_completion_without_shortfall(self):
        """When requested equals produced, shortfall is False."""
        report = pick.clip_run_report(
            requested=5,
            produced=5,
            min_len=20,
            max_len=60,
            total_duration=600,
            relaxed=False,
            discovered=5,
            failed=0
        )
        self.assertEqual(report["requested"], 5)
        self.assertEqual(report["discovered"], 5)
        self.assertEqual(report["completed"], 5)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["shortfall"], False)
        self.assertIsNone(report["reason"]) # backward compatibility
        self.assertEqual(report["stopped_reason"], "All 5 requested clips were successfully discovered and rendered.")

    def test_load_job_finds_job_in_library_json_after_restart(self):
        """When job is missing from DB and memory, load_job recovers it from library.json."""
        jid = "restored_library_job_999"
        lib_entry = {
            "id": jid,
            "type": "clipper",
            "title": "Restored Video",
            "clips": [
                {
                    "file": "clip1_720p.mp4",
                    "export_profile": "720p",
                    "cloud_url": "https://storage.example.com/clips/restored_clip.mp4",
                    "download_url": "/clips/restored_library_job_999/clip1_720p.mp4?dl=1"
                }
            ],
            "info": {
                "requested": 10,
                "discovered": 3,
                "completed": 1,
                "failed": 0,
                "stopped_reason": "Only 3 distinct moments passed length and no-overlap checks in this video."
            }
        }
        with patch("library.get_library", return_value=[lib_entry]):
            j = server.load_job(jid)
            self.assertIsNotNone(j)
            self.assertEqual(j["id"], jid)
            self.assertEqual(len(j["clips"]), 1)
            self.assertEqual(j["info"]["requested"], 10)
            self.assertEqual(j["info"]["discovered"], 3)

    def test_load_job_rejects_undefined_or_null_jid(self):
        """load_job cleanly returns None for 'undefined', 'null', or empty string."""
        self.assertIsNone(server.load_job("undefined"))
        self.assertIsNone(server.load_job("null"))
        self.assertIsNone(server.load_job(""))
        self.assertIsNone(server.load_job(None))

    def test_transcode_job_clip_fast_path_serves_matching_profile_without_error(self):
        """Requesting the same resolution as the rendered clip returns immediately."""
        jid = "job_fast_path_test"
        clip_dir = os.path.join(self.test_dir, jid)
        os.makedirs(clip_dir, exist_ok=True)
        clip_path = os.path.join(clip_dir, "clip1_720p.mp4")
        with open(clip_path, "wb") as f:
            f.write(b"0" * 200000)

        job_data = {
            "id": jid,
            "status": "Done",
            "clips": [
                {
                    "file": "clip1_720p.mp4",
                    "export_profile": "720p",
                    "download_url": f"/clips/{jid}/clip1_720p.mp4?dl=1"
                }
            ]
        }
        server.jobs[jid] = job_data

        res = self.client.post(
            f"/api/jobs/{jid}/clips/0/transcode",
            json={"resolution": "720p"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["resolution"], "720p")
        self.assertIn("clip1_720p.mp4", data["file"])

    def test_transcode_job_clip_falls_back_to_cloud_url_if_local_disk_missing(self):
        """When local file is missing, transcode fast-path returns cloud URL if available."""
        jid = "job_cloud_url_test"
        job_data = {
            "id": jid,
            "status": "Done",
            "clips": [
                {
                    "file": "clip1_720p.mp4",
                    "export_profile": "720p",
                    "cloud_url": "https://storage.supabase.co/clips/clip1.mp4",
                    "download_url": "/clips/job_cloud_url_test/clip1_720p.mp4?dl=1"
                }
            ]
        }
        server.jobs[jid] = job_data

        res = self.client.post(
            f"/api/jobs/{jid}/clips/0/transcode",
            json={"resolution": "720p"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["download_url"], "https://storage.supabase.co/clips/clip1.mp4")

    def test_frontend_has_batch_status_panel_and_stat_cards(self):
        """Check static/index.html includes batch status panel implementation and selectors."""
        with open("static/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("batch-status-panel", html)
        self.assertIn("stat-val-requested", html)
        self.assertIn("stat-val-discovered", html)
        self.assertIn("stat-val-completed", html)
        self.assertIn("stat-val-failed", html)
        self.assertIn("Why generation stopped", html)
        self.assertIn("renderBatchStatusPanel", html)
