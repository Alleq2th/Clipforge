import os
import sys
import unittest
import tempfile
import shutil
import subprocess

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cut import resolve_export_profile, get_filter_complex, cut
from captions import get_ass_dimensions, make_ass

class TestMultiResolutionExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="clipforge_multires_test_")
        cls.test_video = os.path.join(cls.temp_dir, "input.mp4")

        # Create a 1.5s 1280x720 video with audio
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "1.5",
            "-c:v", "libx264", "-c:a", "aac",
            cls.test_video
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_profile_resolution_matching(self):
        """Verify all supported profiles resolve with correct dimensions."""
        profiles_to_test = {
            "360p": {"9:16": (360, 640), "1:1": (360, 360), "16:9": (640, 360)},
            "480p": {"9:16": (480, 854), "1:1": (480, 480), "16:9": (854, 480)},
            "540p": {"9:16": (540, 960), "1:1": (540, 540), "16:9": (960, 540)},
            "720p": {"9:16": (720, 1280), "1:1": (720, 720), "16:9": (1280, 720)},
            "1080p": {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)},
        }

        for prof_key, expected_dims in profiles_to_test.items():
            p = resolve_export_profile(prof_key)
            self.assertEqual(p["name"], prof_key)
            for ratio, expected_wh in expected_dims.items():
                self.assertEqual(p["resolutions"][ratio], expected_wh)

                # Filter complex should target exact resolution
                vf = get_filter_complex(ratio, "beasty", export_profile=prof_key)
                w, h = expected_wh
                if ratio == "16:9":
                    self.assertIn(f"scale={w}:{h}", vf)
                else:
                    self.assertIn(f"crop={w}:{h}", vf)

    def test_ass_caption_scaling_across_resolutions(self):
        """Verify ASS dimensions scale correctly for all profiles."""
        w360, h360, m360 = get_ass_dimensions("9:16", export_profile="360p")
        self.assertEqual((w360, h360), (360, 640))

        w720, h720, m720 = get_ass_dimensions("9:16", export_profile="720p")
        self.assertEqual((w720, h720), (720, 1280))

        w1080, h1080, m1080 = get_ass_dimensions("9:16", export_profile="1080p")
        self.assertEqual((w1080, h1080), (1080, 1920))

    def test_cut_end_to_end_720p(self):
        """End-to-end render test producing 720x1280 video."""
        out_dir = os.path.join(self.temp_dir, "out_720p")
        os.makedirs(out_dir, exist_ok=True)
        candidate = {"start": 0.0, "end": 1.0, "title": "720p Clip", "score": 95}
        segs = [{"start": 0.0, "end": 1.0, "text": "Testing 720p rendering"}]

        out_file = cut(
            self.test_video,
            segs,
            candidate,
            i=1,
            vid="test_720",
            aspect_ratio="9:16",
            caption_style="beasty",
            out_dir=out_dir,
            export_profile="720p"
        )
        self.assertTrue(os.path.exists(out_file))
        probe = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", out_file
        ]).decode().strip()
        self.assertEqual(probe, "720x1280")

    def test_cut_end_to_end_480p(self):
        """End-to-end render test producing 480x854 video."""
        out_dir = os.path.join(self.temp_dir, "out_480p")
        os.makedirs(out_dir, exist_ok=True)
        candidate = {"start": 0.0, "end": 1.0, "title": "480p Clip", "score": 90}
        segs = []

        out_file = cut(
            self.test_video,
            segs,
            candidate,
            i=1,
            vid="test_480",
            aspect_ratio="9:16",
            caption_style="none",
            out_dir=out_dir,
            export_profile="480p"
        )
        self.assertTrue(os.path.exists(out_file))
        probe = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", out_file
        ]).decode().strip()
        self.assertEqual(probe, "480x854")

if __name__ == "__main__":
    unittest.main()
