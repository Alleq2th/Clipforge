import os
import sys
import unittest
import tempfile
import shutil
import subprocess

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import EXPORT_PROFILES, DEFAULT_EXPORT_PROFILE
from cut import resolve_export_profile, get_filter_complex, scale_style_for_1080p, check_has_audio, cut

class TestExportProfileAndLoudnorm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="clipforge_export_test_")
        cls.video_with_audio = os.path.join(cls.temp_dir, "input_audio.mp4")
        cls.video_no_audio = os.path.join(cls.temp_dir, "input_no_audio.mp4")

        # 1. Create a 2s video with audio (quiet tone)
        cmd_audio = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "2",
            "-af", "volume=-20dB",
            "-c:v", "libx264", "-c:a", "aac",
            cls.video_with_audio
        ]
        subprocess.run(cmd_audio, check=True, capture_output=True)

        # 2. Create a 2s video without audio
        cmd_no_audio = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-t", "2",
            "-c:v", "libx264",
            cls.video_no_audio
        ]
        subprocess.run(cmd_no_audio, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_resolve_export_profile(self):
        """Test export profile resolution aliases and default fallback."""
        p_1080 = resolve_export_profile("1080p")
        self.assertEqual(p_1080["name"], "1080p")
        self.assertEqual(p_1080["crf"], 21)
        self.assertEqual(p_1080["preset"], "veryfast")
        self.assertEqual(p_1080["audio_bitrate"], "192k")
        self.assertTrue(p_1080["normalize_audio"])

        # Check aliases
        self.assertEqual(resolve_export_profile("full_hd")["name"], "1080p")
        self.assertEqual(resolve_export_profile("1080")["name"], "1080p")
        self.assertEqual(resolve_export_profile("premium")["name"], "1080p")

        # Check 540p fallback
        p_540 = resolve_export_profile("540p")
        self.assertEqual(p_540["name"], "540p")
        self.assertEqual(p_540["crf"], 26)
        self.assertEqual(p_540["preset"], "ultrafast")
        self.assertFalse(p_540["normalize_audio"])

        p_default = resolve_export_profile(None)
        self.assertEqual(p_default["name"], DEFAULT_EXPORT_PROFILE)

    def test_filter_complex_1080p_resolutions(self):
        """Verify 1080p filter complex targets full 1080p dimensions across aspect ratios."""
        # 9:16 Vertical
        vf_916 = get_filter_complex("9:16", "beasty", export_profile="1080p")
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", vf_916)

        # 1:1 Square
        vf_11 = get_filter_complex("1:1", "beasty", export_profile="1080p")
        self.assertIn("scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080", vf_11)

        # 16:9 Landscape
        vf_169 = get_filter_complex("16:9", "beasty", export_profile="1080p")
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080", vf_169)

    def test_filter_complex_1080p_blur_background(self):
        """Verify 1080p blur canvas matches 1080p dimensions."""
        vf_blur_916 = get_filter_complex("9:16", "beasty", framing="blur", export_profile="1080p")
        self.assertIn("scale=1080:1920", vf_blur_916)
        self.assertIn("scale=270:480", vf_blur_916)
        self.assertIn("boxblur=4:1", vf_blur_916)

        vf_blur_11 = get_filter_complex("1:1", "beasty", framing="blur", export_profile="1080p")
        self.assertIn("scale=1080:1080", vf_blur_11)
        self.assertIn("scale=270:270", vf_blur_11)

    def test_subtitle_style_scaling_for_1080p(self):
        """Verify font size is properly scaled up for 1080p readability."""
        raw_style = "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FFFF,Outline=3"
        scaled = scale_style_for_1080p(raw_style, font_scale=1.85, outline_scale=1.8)
        self.assertIn("FontSize=24", scaled)
        self.assertIn("Outline=5.4", scaled)

    def test_check_has_audio_detection(self):
        """Test audio stream probe detection."""
        self.assertTrue(check_has_audio(self.video_with_audio))
        self.assertFalse(check_has_audio(self.video_no_audio))

    def test_cut_renders_1080p_video_end_to_end(self):
        """cut() with export_profile='1080p' must generate a valid 1080x1920 video with normalized audio."""
        out_dir = os.path.join(self.temp_dir, "out_1080p")
        os.makedirs(out_dir, exist_ok=True)

        candidate = {
            "start": 0.0,
            "end": 1.5,
            "title": "1080p Export Test",
            "score": 98,
            "category": "Tech Demo",
            "hook_reason": "High resolution test"
        }
        segs = [{"start": 0.0, "end": 1.5, "text": "Testing 1080p video export"}]

        out_file = cut(
            self.video_with_audio,
            segs,
            candidate,
            i=1,
            vid="test_1080p",
            aspect_ratio="9:16",
            caption_style="beasty",
            framing="crop",
            out_dir=out_dir,
            export_profile="1080p"
        )

        self.assertTrue(os.path.exists(out_file))
        self.assertTrue(os.path.getsize(out_file) > 1000)

        # Probe resolution using ffprobe
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_file
        ]
        dims = subprocess.check_output(probe_cmd).decode().strip()
        self.assertEqual(dims, "1080x1920")

        # Verify audio stream is present
        self.assertTrue(check_has_audio(out_file))

    def test_cut_1080p_without_audio_stream_does_not_crash(self):
        """cut() with export_profile='1080p' on audio-less input must gracefully succeed."""
        out_dir = os.path.join(self.temp_dir, "out_no_audio")
        os.makedirs(out_dir, exist_ok=True)

        candidate = {
            "start": 0.0,
            "end": 1.5,
            "title": "No Audio 1080p Test",
            "score": 90
        }
        segs = []

        out_file = cut(
            self.video_no_audio,
            segs,
            candidate,
            i=1,
            vid="test_no_audio",
            aspect_ratio="9:16",
            framing="crop",
            out_dir=out_dir,
            export_profile="1080p"
        )

        self.assertTrue(os.path.exists(out_file))
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_file
        ]
        dims = subprocess.check_output(probe_cmd).decode().strip()
        self.assertEqual(dims, "1080x1920")

    def test_cut_custom_crf_and_loudnorm_override(self):
        """Verify custom CRF override is accepted without errors."""
        out_dir = os.path.join(self.temp_dir, "out_crf_override")
        os.makedirs(out_dir, exist_ok=True)

        candidate = {
            "start": 0.0,
            "end": 1.0,
            "title": "CRF Override Test",
            "score": 94
        }
        segs = []

        out_file = cut(
            self.video_with_audio,
            segs,
            candidate,
            i=1,
            vid="test_crf",
            aspect_ratio="1:1",
            out_dir=out_dir,
            export_profile="1080p",
            crf=18,
            normalize_audio=True
        )

        self.assertTrue(os.path.exists(out_file))
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_file
        ]
        dims = subprocess.check_output(probe_cmd).decode().strip()
        self.assertEqual(dims, "1080x1080")

if __name__ == "__main__":
    unittest.main()
