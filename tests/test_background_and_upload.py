import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import config
import security
import download
import storage
import library
import server

class TestBackgroundAndUpload(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_bg_upload_")
        self.orig_work = config.WORK_DIR
        self.orig_out = config.OUT_DIR

        self.work_dir = os.path.join(self.test_dir, "work")
        self.out_dir = os.path.join(self.test_dir, "output")
        os.makedirs(self.work_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)

        config.WORK_DIR = self.work_dir
        config.OUT_DIR = self.out_dir
        download.WORK_DIR = self.work_dir
        storage.WORK_DIR = self.work_dir
        storage.OUT_DIR = self.out_dir
        library.WORK_DIR = self.work_dir
        library.OUT_DIR = self.out_dir
        server.WORK_DIR = self.work_dir
        server.OUT_DIR = self.out_dir

        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def tearDown(self):
        config.WORK_DIR = self.orig_work
        config.OUT_DIR = self.orig_out
        download.WORK_DIR = self.orig_work
        storage.WORK_DIR = self.orig_work
        storage.OUT_DIR = self.orig_out
        library.WORK_DIR = self.orig_work
        library.OUT_DIR = self.orig_out
        server.WORK_DIR = self.orig_work
        server.OUT_DIR = self.orig_out
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_upload_url_security_validation(self):
        upload_dir = os.path.join(self.work_dir, "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        valid_path = os.path.join(upload_dir, "test1234.mp4")
        with open(valid_path, "wb") as f:
            f.write(b"fake mp4 content")

        # Valid upload URL
        self.assertEqual(security.validate_video_url("upload://test1234"), "upload://test1234")

        # Invalid upload tokens must raise ValueError
        with self.assertRaises(ValueError):
            security.validate_video_url("upload://../secret")
        with self.assertRaises(ValueError):
            security.validate_video_url("upload:///etc/passwd")
        with self.assertRaises(ValueError):
            security.validate_video_url("upload://test.mp4")
        with self.assertRaises(ValueError):
            security.validate_video_url("upload://nonexistent_file")

    def test_direct_video_upload_endpoint(self):
        data = {
            "file": (io.BytesIO(b"dummy video data for direct upload"), "sample_video.mp4")
        }
        res = self.client.post("/api/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body.get("ok"))
        self.assertIn("upload://", body.get("url"))
        self.assertEqual(body.get("title"), "sample video")

        upload_id = body.get("url").replace("upload://", "")
        saved_file = os.path.join(self.work_dir, "uploads", f"{upload_id}.mp4")
        self.assertTrue(os.path.exists(saved_file))

    def test_download_copies_uploaded_video(self):
        upload_dir = os.path.join(self.work_dir, "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        up_file = os.path.join(upload_dir, "localvid123.mp4")
        with open(up_file, "wb") as f:
            f.write(b"video bytes header data")

        job_dir = os.path.join(self.work_dir, "job_test")
        os.makedirs(job_dir, exist_ok=True)

        target, vid = download.download("upload://localvid123", job_dir)
        self.assertEqual(vid, "localvid123")
        self.assertTrue(os.path.exists(target))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"video bytes header data")

    def test_update_job_email_endpoint(self):
        server.jobs["job_email_test"] = {
            "jid": "job_email_test",
            "status": "processing",
            "options": {"count": 5}
        }

        # Valid email update
        res = self.client.post("/api/jobs/job_email_test/email", json={"email": "client@example.com"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json().get("ok"))
        self.assertEqual(server.jobs["job_email_test"]["options"]["email"], "client@example.com")

        # Invalid email
        res_bad = self.client.post("/api/jobs/job_email_test/email", json={"email": "invalid_email"})
        self.assertEqual(res_bad.status_code, 400)

    def test_cloud_library_backup_and_restore(self):
        mock_provider = MagicMock()
        mock_provider.load_json.return_value = [
            {"id": "cloud_job_1", "title": "Cloud Restored Clip", "done": True, "clips": []}
        ]

        with patch("storage.get_storage_provider", return_value=mock_provider):
            # Test restoration from cloud when local is empty
            items = library.get_library()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], "cloud_job_1")

            # Test save_library_entry triggers cloud save
            library.save_library_entry({"id": "local_job_2", "title": "New Video", "clips": []})
            mock_provider.save_json.assert_called()

if __name__ == "__main__":
    unittest.main()
