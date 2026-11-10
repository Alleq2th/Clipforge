import unittest
import os
import tempfile
import shutil
import json
from unittest.mock import patch, MagicMock

import storage
from storage import SupabaseStorageProvider, LocalStorageProvider, get_storage_provider
import library
import server

class TestCloudStorageIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_cloud_storage_")
        self.orig_work = storage.WORK_DIR
        self.orig_out = storage.OUT_DIR
        storage.WORK_DIR = os.path.join(self.test_dir, "work")
        storage.OUT_DIR = os.path.join(self.test_dir, "output")
        library.WORK_DIR = storage.WORK_DIR
        library.OUT_DIR = storage.OUT_DIR
        server.WORK_DIR = storage.WORK_DIR
        server.OUT_DIR = storage.OUT_DIR
        os.makedirs(storage.WORK_DIR, exist_ok=True)
        os.makedirs(storage.OUT_DIR, exist_ok=True)

    def tearDown(self):
        storage.WORK_DIR = self.orig_work
        storage.OUT_DIR = self.orig_out
        library.WORK_DIR = self.orig_work
        library.OUT_DIR = self.orig_out
        server.WORK_DIR = self.orig_work
        server.OUT_DIR = self.orig_out
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_local_provider_fallback_when_unconfigured(self):
        """When SUPABASE_URL / key are unset, LocalStorageProvider is used cleanly."""
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}, clear=False):
            provider = get_storage_provider()
            self.assertIsInstance(provider, LocalStorageProvider)
            self.assertEqual(provider.name, "local")
            self.assertFalse(provider.is_configured())
            self.assertIsNone(provider.save_clip("dummy.mp4", "job1/dummy.mp4"))
            self.assertEqual(provider.get_public_url("job1/dummy.mp4"), "/clips/job1/dummy.mp4")

    def test_supabase_provider_configuration_detection(self):
        """When SUPABASE_URL and key are set, SupabaseStorageProvider is active."""
        env = {
            "SUPABASE_URL": "https://testproj.supabase.co",
            "SUPABASE_SERVICE_KEY": "test-service-key-12345",
            "SUPABASE_STORAGE_BUCKET": "custom_clips"
        }
        with patch.dict(os.environ, env):
            provider = get_storage_provider()
            self.assertIsInstance(provider, SupabaseStorageProvider)
            self.assertEqual(provider.name, "supabase")
            self.assertTrue(provider.is_configured())
            self.assertEqual(provider.bucket, "custom_clips")
            self.assertEqual(
                provider.get_public_url("job1/clip_1.mp4"),
                "https://testproj.supabase.co/storage/v1/object/public/custom_clips/job1/clip_1.mp4"
            )

    @patch("requests.post")
    def test_supabase_save_clip_and_bucket_creation(self, mock_post):
        """save_clip uploads file and returns public URL, ensuring bucket creation."""
        mock_bucket_resp = MagicMock()
        mock_bucket_resp.status_code = 200
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_post.side_effect = [mock_bucket_resp, mock_upload_resp]

        fake_clip = os.path.join(self.test_dir, "test_clip.mp4")
        with open(fake_clip, "wb") as f:
            f.write(b"fake mp4 video bytes")

        env = {
            "SUPABASE_URL": "https://testproj.supabase.co",
            "SUPABASE_SERVICE_KEY": "test-key",
            "SUPABASE_STORAGE_BUCKET": "clips"
        }
        with patch.dict(os.environ, env):
            provider = SupabaseStorageProvider()
            public_url = provider.save_clip(fake_clip, "job_abc/clip_1.mp4")
            self.assertEqual(
                public_url,
                "https://testproj.supabase.co/storage/v1/object/public/clips/job_abc/clip_1.mp4"
            )
            self.assertEqual(mock_post.call_count, 2)
            bucket_call = mock_post.call_args_list[0]
            self.assertIn("/storage/v1/bucket", bucket_call[0][0])
            upload_call = mock_post.call_args_list[1]
            self.assertIn("/storage/v1/object/clips/job_abc/clip_1.mp4", upload_call[0][0])

    @patch("requests.delete")
    def test_supabase_delete_clip(self, mock_delete):
        """delete_clip sends DELETE request to Supabase Storage."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_delete.return_value = mock_resp

        env = {
            "SUPABASE_URL": "https://testproj.supabase.co",
            "SUPABASE_SERVICE_KEY": "test-key"
        }
        with patch.dict(os.environ, env):
            provider = SupabaseStorageProvider()
            res = provider.delete_clip("job_abc/clip_1.mp4")
            self.assertTrue(res)
            mock_delete.assert_called_once()
            self.assertIn("/storage/v1/object/clips/job_abc/clip_1.mp4", mock_delete.call_args[0][0])

    def test_server_storage_api_reports_cloud_status(self):
        """GET /api/storage accurately reports whether cloud storage is connected."""
        client = server.app.test_client()

        # Unconfigured
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}, clear=False):
            res = client.get("/api/storage")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("cloud_storage", data)
            self.assertFalse(data["cloud_storage"]["configured"])
            self.assertEqual(data["cloud_storage"]["provider"], "local")

        # Configured with Supabase
        env = {
            "SUPABASE_URL": "https://active.supabase.co",
            "SUPABASE_SERVICE_KEY": "service_role_secret",
            "SUPABASE_STORAGE_BUCKET": "permanent_clips"
        }
        with patch.dict(os.environ, env):
            res = client.get("/api/storage")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["cloud_storage"]["configured"])
            self.assertEqual(data["cloud_storage"]["provider"], "supabase")
            self.assertEqual(data["cloud_storage"]["bucket"], "permanent_clips")

    def test_clips_route_redirects_to_cloud_when_local_file_missing(self):
        """When local file is missing on disk (Render restart/sleep), /clips/ redirects to cloud URL."""
        client = server.app.test_client()
        env = {
            "SUPABASE_URL": "https://active.supabase.co",
            "SUPABASE_SERVICE_KEY": "service_role_secret",
            "SUPABASE_STORAGE_BUCKET": "clips"
        }
        with patch.dict(os.environ, env):
            # Request non-existent local file
            res = client.get("/clips/job_missing/clip_1.mp4")
            self.assertEqual(res.status_code, 302)
            self.assertEqual(
                res.headers.get("Location"),
                "https://active.supabase.co/storage/v1/object/public/clips/job_missing/clip_1.mp4"
            )

    @patch("requests.delete")
    def test_library_delete_clip_syncs_with_cloud(self, mock_delete):
        """Deleting a clip from library triggers cloud object deletion when configured."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_delete.return_value = mock_resp

        env = {
            "SUPABASE_URL": "https://active.supabase.co",
            "SUPABASE_SERVICE_KEY": "service_role_secret"
        }
        with patch.dict(os.environ, env):
            library.save_library_entry({
                "id": "job_123",
                "type": "clipper",
                "clips": [{"file": "clip_1.mp4"}]
            })
            ok = library.delete_library_clip("job_123", "clip_1.mp4")
            self.assertTrue(ok)
            mock_delete.assert_called_once()
            self.assertIn("/storage/v1/object/clips/job_123/clip_1.mp4", mock_delete.call_args[0][0])

if __name__ == "__main__":
    unittest.main()
