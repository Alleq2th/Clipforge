import os
import shutil
import tempfile
import unittest
import subprocess
import json
from unittest.mock import patch, MagicMock

from config import EXPORT_PROFILES
from cut import resolve_export_profile, cut, shrink_video
import server


class TestVideoCompression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="clipforge_test_compression_")
        cls.test_video = os.path.join(cls.temp_dir, "source.mp4")

        # Generate a small 3-second synthetic test video with audio
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k",
            cls.test_video
        ]
        subprocess.run(cmd, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_export_profiles_contain_bitrate_caps(self):
        """Ensure all profiles define VBV maxrate and bufsize to prevent bitrate ballooning."""
        for prof_key in ("360p", "480p", "540p", "720p", "1080p"):
            prof = resolve_export_profile(prof_key)
            self.assertIn("maxrate", prof, f"{prof_key} missing maxrate")
            self.assertIn("bufsize", prof, f"{prof_key} missing bufsize")
            self.assertTrue(prof["maxrate"].endswith("k"))
            self.assertTrue(prof["bufsize"].endswith("k"))

    def test_cut_uses_high_profile_faststart_and_no_fastdecode(self):
        """Ensure cut() eliminates -tune fastdecode and applies web-optimized high profile and faststart."""
        captured_cmds = []

        def mock_subprocess_run(cmd, *args, **kwargs):
            captured_cmds.append(cmd)
            # Create dummy clip output
            out_file = cmd[-1]
            if not os.path.exists(out_file):
                with open(out_file, "wb") as f:
                    f.write(b"\x00" * 1024)
            return MagicMock(returncode=0)

        with patch("cut.run_tracked_subprocess", side_effect=mock_subprocess_run):
            candidate = {"start": 0.0, "end": 2.0, "title": "Test Hook", "caption": "Caption text"}
            out_path = cut(
                video=self.test_video,
                segs=[],
                c=candidate,
                i=1,
                vid="test_comp_job",
                out_dir=self.temp_dir,
                export_profile="720p"
            )

        self.assertTrue(len(captured_cmds) > 0, "No FFmpeg command captured")
        cmd = captured_cmds[0]

        # Assert -tune fastdecode is gone
        self.assertNotIn("-tune", cmd)
        self.assertNotIn("fastdecode", cmd)

        # Assert maxrate and bufsize are present
        self.assertIn("-maxrate", cmd)
        self.assertIn("-bufsize", cmd)
        maxrate_val = cmd[cmd.index("-maxrate") + 1]
        self.assertEqual(maxrate_val, "1600k")

        # Assert high profile, level 4.1, yuv420p, and faststart
        self.assertIn("-profile:v", cmd)
        self.assertEqual(cmd[cmd.index("-profile:v") + 1], "high")
        self.assertIn("-level", cmd)
        self.assertEqual(cmd[cmd.index("-level") + 1], "4.1")
        self.assertIn("-pix_fmt", cmd)
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("-movflags", cmd)
        self.assertEqual(cmd[cmd.index("-movflags") + 1], "+faststart")

    def test_shrink_video_produces_playable_compact_file(self):
        """Verify shrink_video runs cleanly, preserves audio/video, and outputs valid MP4."""
        out_shrunk = os.path.join(self.temp_dir, "shrunk_test.mp4")
        result_path = shrink_video(self.test_video, out_shrunk, target_max_dim=720, maxrate="1000k", crf=28)

        self.assertTrue(os.path.exists(result_path))
        self.assertGreater(os.path.getsize(result_path), 0)

        # Verify with ffprobe that video stream has yuv420p and high/main profile
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt",
            "-of", "json",
            result_path
        ]
        probe_out = subprocess.check_output(probe_cmd)
        probe_data = json.loads(probe_out)
        streams = probe_data.get("streams", [])
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["codec_name"], "h264")
        self.assertEqual(streams[0]["pix_fmt"], "yuv420p")

    def test_resolution_export_endpoint_creates_cached_variant(self):
        app = server.app
        app.config["TESTING"] = True
        client = app.test_client()
        jid = "test_resolution_export_jid"
        job_dir = os.path.join(server.OUT_DIR, jid)
        os.makedirs(job_dir, exist_ok=True)
        shutil.copyfile(self.test_video, os.path.join(job_dir, "clip1.mp4"))
        server.jobs[jid] = {
            "id": jid,
            "status": "Completed",
            "clips": [{"file": "clip1.mp4", "vid": jid, "title": "Test clip"}],
        }
        server.save_job(jid)
        try:
            res = client.post(f"/api/jobs/{jid}/clips/0/transcode", json={"resolution": "480p"})
            self.assertEqual(res.status_code, 200, res.get_json())
            data = res.get_json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["resolution"], "480p")
            self.assertGreater(data["size_bytes"], 0)
            variant = os.path.join(job_dir, data["file"])
            self.assertTrue(os.path.isfile(variant))
            # The endpoint reuses a complete cached rendition on a second request.
            second = client.post(f"/api/jobs/{jid}/clips/0/transcode", json={"resolution": "480p"})
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.get_json()["file"], data["file"])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            server.jobs.pop(jid, None)

    def test_resolution_export_rejects_unsupported_profile(self):
        app = server.app
        app.config["TESTING"] = True
        client = app.test_client()
        jid = "test_resolution_invalid_jid"
        server.jobs[jid] = {"id": jid, "clips": [{"file": "clip1.mp4"}]}
        server.save_job(jid)
        try:
            res = client.post(f"/api/jobs/{jid}/clips/0/transcode", json={"resolution": "8k"})
            self.assertEqual(res.status_code, 400)
            self.assertIn("Choose", res.get_json()["error"])
        finally:
            server.jobs.pop(jid, None)

    def test_shrink_endpoint_api(self):
        """Verify /api/jobs/<jid>/clips/<clip_idx>/shrink updates job state and library."""
        app = server.app
        app.config["TESTING"] = True
        client = app.test_client()

        jid = "test_shrink_jid"
        job_dir = os.path.join(server.OUT_DIR, jid)
        os.makedirs(job_dir, exist_ok=True)
        clip_path = os.path.join(job_dir, "clip1.mp4")
        shutil.copyfile(self.test_video, clip_path)

        # Mock job in memory and db
        job_data = {
            "id": jid,
            "status": "Completed",
            "url": "https://example.com/test",
            "clips": [
                {
                    "file": "clip1.mp4",
                    "title": "Clip 1",
                    "vid": jid,
                    "download_url": f"/clips/{jid}/clip1.mp4?dl=1",
                    "stream_url": f"/clips/{jid}/clip1.mp4",
                    "size_formatted": "1.2 MB"
                }
            ]
        }
        server.jobs[jid] = job_data
        server.save_job(jid)

        try:
            res = client.post(f"/api/jobs/{jid}/clips/0/shrink")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("ok"))
            self.assertIn("shrunk_file", data)
            self.assertIn("download_url", data)
            self.assertIn("size_formatted", data)
            self.assertTrue(os.path.exists(os.path.join(job_dir, data["shrunk_file"])))

            # Verify saved job was updated
            updated_job = server.load_job(jid)
            self.assertIn("shrunk_url", updated_job["clips"][0])
            self.assertIn("shrunk_size_formatted", updated_job["clips"][0])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            if jid in server.jobs:
                del server.jobs[jid]


if __name__ == "__main__":
    unittest.main()
