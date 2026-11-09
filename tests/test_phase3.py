import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import subprocess
import shutil
import tempfile
import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

from reframe import detect_smart_crop_anchor
from cut import get_filter_complex, cut, STYLES

class TestPhase3SmartReframing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="clipforge_phase3_test_")
        cls.no_face_video = os.path.join(cls.temp_dir, "no_face.mp4")
        cls.face_video = os.path.join(cls.temp_dir, "face_left.mp4")

        # 1. Create a 2s video without any faces
        cmd1 = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            cls.no_face_video
        ]
        subprocess.run(cmd1, check=True, capture_output=True)

        # 2. Create a 2s video with a face placed on the left third (x ~ 200/1280 = 0.156)
        if CV2_AVAILABLE:
            try:
                # Create a simple portrait face pattern or use lena if available
                bg = np.zeros((720, 1280, 3), dtype=np.uint8)
                # Draw head / face oval on left third: center (240, 300)
                cv2.ellipse(bg, (240, 300), (90, 120), 0, 0, 360, (210, 180, 140), -1)
                # Eyes
                cv2.circle(bg, (210, 270), 12, (255, 255, 255), -1)
                cv2.circle(bg, (210, 270), 5, (0, 0, 0), -1)
                cv2.circle(bg, (270, 270), 12, (255, 255, 255), -1)
                cv2.circle(bg, (270, 270), 5, (0, 0, 0), -1)
                # Nose
                cv2.line(bg, (240, 285), (240, 315), (100, 70, 50), 3)
                # Mouth
                cv2.ellipse(bg, (240, 345), (35, 15), 0, 0, 180, (50, 50, 200), 3)

                frame_path = os.path.join(cls.temp_dir, "face_frame.png")
                cv2.imwrite(frame_path, bg)

                cmd2 = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", frame_path,
                    "-t", "2", "-r", "25",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    cls.face_video
                ]
                subprocess.run(cmd2, check=True, capture_output=True)
            except Exception:
                pass

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_reframe_missing_file_fallback(self):
        """Missing or non-existent file should gracefully return center fallback."""
        x, y, detected = detect_smart_crop_anchor("/nonexistent_path/video.mp4", 0.0, 10.0)
        self.assertEqual(x, 0.5)
        self.assertEqual(y, 0.5)
        self.assertFalse(detected)

    def test_reframe_invalid_timestamps_fallback(self):
        """Invalid time range should gracefully return center fallback."""
        x, y, detected = detect_smart_crop_anchor(self.no_face_video, 5.0, 2.0)
        self.assertEqual(x, 0.5)
        self.assertEqual(y, 0.5)
        self.assertFalse(detected)

    def test_reframe_no_faces_detected_fallback(self):
        """Video with no faces should safely fall back to center crop."""
        x, y, detected = detect_smart_crop_anchor(self.no_face_video, 0.0, 2.0)
        self.assertEqual(x, 0.5)
        self.assertEqual(y, 0.5)
        self.assertFalse(detected)

    def test_filter_complex_exact_preservation_for_crop(self):
        """framing='crop' must output the exact standard center crop filter."""
        vf = get_filter_complex("9:16", "beasty", framing="crop")
        self.assertIn("scale=540:960:force_original_aspect_ratio=increase,crop=540:960", vf)
        self.assertNotIn("x=", vf)
        self.assertNotIn("y=", vf)

    def test_filter_complex_exact_preservation_for_blur_and_fit(self):
        """framing='blur' and 'fit' must preserve their distinct filters."""
        vf_blur = get_filter_complex("9:16", "beasty", framing="blur")
        self.assertIn("boxblur=4:1", vf_blur)

        vf_fit = get_filter_complex("9:16", "beasty", framing="fit")
        self.assertIn("scale=540:960:force_original_aspect_ratio=decrease,pad=540:960", vf_fit)

    def test_filter_complex_smart_fallback_matches_crop(self):
        """When framing='smart' and no face is found (x=0.5, y=0.5), it must cleanly match crop."""
        vf_smart_fallback = get_filter_complex("9:16", "beasty", framing="smart", x_norm=0.5, y_norm=0.5)
        self.assertIn("scale=540:960:force_original_aspect_ratio=increase,crop=540:960", vf_smart_fallback)

    def test_filter_complex_smart_anchored_crop(self):
        """When framing='smart' and subject is off-center, filter should center crop on subject."""
        vf_smart = get_filter_complex("9:16", "beasty", framing="smart", x_norm=0.22, y_norm=0.35)
        self.assertIn("crop=540:960:x=", vf_smart)
        self.assertIn("0.2200*iw", vf_smart)

    def test_phase3_caption_styles_available(self):
        """Verify new Phase 3 caption styles are properly registered."""
        self.assertIn("clean_minimal", STYLES)
        self.assertIn("podcast", STYLES)
        self.assertIn("cinematic", STYLES)
        self.assertIn("bold_creator", STYLES)

    def test_cut_end_to_end_with_smart_reframing(self):
        """cut() must succeed when framing='smart', producing valid output."""
        out_test_dir = os.path.join(self.temp_dir, "cut_out")
        os.makedirs(out_test_dir, exist_ok=True)

        clip_candidate = {
            "start": 0.0,
            "end": 1.5,
            "title": "Smart Reframing Test Clip",
            "score": 95,
            "category": "Viral Moment",
            "hook_reason": "Visual subject focus"
        }
        segs = [{"start": 0.0, "end": 1.5, "text": "Testing smart reframing functionality"}]

        out_file = cut(
            self.no_face_video,
            segs,
            clip_candidate,
            i=1,
            vid="test_vid",
            aspect_ratio="9:16",
            caption_style="beasty",
            framing="smart",
            out_dir=out_test_dir
        )

        self.assertTrue(os.path.exists(out_file))
        self.assertTrue(os.path.getsize(out_file) > 1000)

        # Verify output resolution is 540x960
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_file
        ]
        dims = subprocess.check_output(probe_cmd).decode().strip()
        self.assertEqual(dims, "540x960")

if __name__ == "__main__":
    unittest.main()
