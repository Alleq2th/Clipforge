import os
import sys
import unittest
import tempfile
import shutil
import subprocess

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from captions import (
    _t_ass,
    sanitize_ass_text,
    get_ass_dimensions,
    make_ass,
    make_srt,
    ASS_PRESETS
)
from cut import get_filter_complex, STYLES, cut

class TestAssKaraokeCaptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="clipforge_ass_test_")
        cls.test_video = os.path.join(cls.temp_dir, "test_input.mp4")

        # Create a small 2s test video
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            cls.test_video
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_t_ass_formatting(self):
        """Verifies ASS timestamp formatting to H:MM:SS.cc (centiseconds)."""
        self.assertEqual(_t_ass(0.0), "0:00:00.00")
        self.assertEqual(_t_ass(1.25), "0:00:01.25")
        self.assertEqual(_t_ass(59.99), "0:00:59.99")
        self.assertEqual(_t_ass(61.50), "0:01:01.50")
        self.assertEqual(_t_ass(3661.05), "1:01:01.05")

    def test_sanitize_ass_text(self):
        """Verifies control character escaping and uppercase formatting for ASS text."""
        raw = "Hello {world} with \\ backslash and \n newlines"
        clean = sanitize_ass_text(raw)
        self.assertNotIn("{", clean)
        self.assertNotIn("}", clean)
        self.assertNotIn("\\", clean)
        self.assertIn("HELLO (WORLD)", clean)

    def test_get_ass_dimensions(self):
        """Verifies ASS coordinate space dimensions matching export profiles."""
        w_1080_916, h_1080_916, mv_1080_916 = get_ass_dimensions("9:16", "1080p")
        self.assertEqual((w_1080_916, h_1080_916, mv_1080_916), (1080, 1920, 280))

        w_1080_11, h_1080_11, mv_1080_11 = get_ass_dimensions("1:1", "1080p")
        self.assertEqual((w_1080_11, h_1080_11, mv_1080_11), (1080, 1080, 120))

        w_540_916, h_540_916, mv_540_916 = get_ass_dimensions("9:16", "540p")
        self.assertEqual((w_540_916, h_540_916, mv_540_916), (540, 960, 140))

    def test_all_presets_registered_in_ass_and_styles(self):
        """Verifies all existing presets are available and fully configured in ASS_PRESETS."""
        expected_presets = [
            "beasty", "karaoke", "classic", "cyber",
            "clean_minimal", "minimal", "bold_creator",
            "highlighted", "keywords", "podcast", "cinematic", "social_bold"
        ]
        for p in expected_presets:
            self.assertIn(p, ASS_PRESETS, f"Preset {p} missing from ASS_PRESETS")
            self.assertIn(p, STYLES, f"Preset {p} missing from STYLES backwards compatibility")
            preset_conf = ASS_PRESETS[p]
            self.assertTrue(preset_conf["primary"].startswith("&H"))
            self.assertTrue(preset_conf["secondary"].startswith("&H"))
            self.assertTrue(preset_conf["outline"].startswith("&H"))

    def test_make_ass_with_word_level_timestamps(self):
        """Verifies ASS file generation with animated karaoke tags and word-level pacing."""
        segs = [{
            "start": 10.0,
            "end": 15.0,
            "text": "This is a viral hook statement",
            "words": [
                {"word": "This", "start": 10.0, "end": 10.4},
                {"word": "is", "start": 10.5, "end": 10.7},
                {"word": "a", "start": 10.8, "end": 11.0},
                {"word": "viral", "start": 11.2, "end": 11.8},
                {"word": "hook", "start": 11.9, "end": 12.3},
                {"word": "statement", "start": 12.4, "end": 13.0}
            ]
        }]
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tf:
            ass_path = tf.name

        try:
            make_ass(segs, start=10.0, end=15.0, path=ass_path, style_key="beasty", aspect_ratio="9:16", export_profile="1080p")
            with open(ass_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Script Info headers
            self.assertIn("[Script Info]", content)
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1920", content)
            self.assertIn("ScaledBorderAndShadow: yes", content)

            # V4+ Styles definition
            self.assertIn("[V4+ Styles]", content)
            self.assertIn("Style: beasty,Arial", content)
            self.assertIn("&H0000FFFF", content)  # Beast Yellow primary highlight

            # Events & Dialogue entries
            self.assertIn("[Events]", content)
            self.assertIn("Dialogue: 0,0:00:00.00", content)
            self.assertIn("{\\k", content)
            self.assertIn("THIS", content)
            self.assertIn("VIRAL", content)
            self.assertIn("STATEMENT", content)
        finally:
            if os.path.exists(ass_path):
                os.remove(ass_path)

    def test_make_ass_segment_level_fallback(self):
        """Verifies proportional rhythmic fallback when word-level timings are absent."""
        segs = [{
            "start": 5.0,
            "end": 10.0,
            "text": "Hello world welcome back",
            "words": []
        }]
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tf:
            ass_path = tf.name

        try:
            make_ass(segs, start=5.0, end=10.0, path=ass_path, style_key="karaoke", aspect_ratio="9:16", export_profile="540p", words=2)
            with open(ass_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("PlayResX: 540", content)
            self.assertIn("PlayResY: 960", content)
            self.assertIn("Style: karaoke,Arial", content)
            self.assertIn("&H0000FF00", content)  # Green highlight
            self.assertIn("{\\k", content)
            self.assertIn("HELLO", content)
            self.assertIn("WORLD", content)
            self.assertIn("WELCOME", content)
            self.assertIn("BACK", content)
        finally:
            if os.path.exists(ass_path):
                os.remove(ass_path)

    def test_filter_complex_supports_both_ass_and_srt(self):
        """Verifies get_filter_complex uses ass filter for .ass and subtitles filter for .srt."""
        vf_ass = get_filter_complex("9:16", "beasty", srt_filename="clip1.ass", export_profile="1080p")
        self.assertIn(",ass=clip1.ass", vf_ass)

        vf_srt = get_filter_complex("9:16", "beasty", srt_filename="clip1.srt", export_profile="1080p")
        self.assertIn(",subtitles=clip1.srt:force_style=", vf_srt)

    def test_cut_end_to_end_burns_styled_ass_subtitles(self):
        """End-to-end test verifying cut() renders a video with styled ASS karaoke subtitles."""
        out_dir = os.path.join(self.temp_dir, "cut_ass_out")
        os.makedirs(out_dir, exist_ok=True)

        candidate = {
            "start": 0.0,
            "end": 1.5,
            "title": "ASS Karaoke Subtitles Test",
            "score": 96,
            "category": "Viral Subtitles",
            "hook_reason": "Animated ASS karaoke captions"
        }
        segs = [{
            "start": 0.0,
            "end": 1.5,
            "text": "Viral karaoke subtitles test",
            "words": [
                {"word": "Viral", "start": 0.0, "end": 0.4},
                {"word": "karaoke", "start": 0.4, "end": 0.9},
                {"word": "subtitles", "start": 0.9, "end": 1.3},
                {"word": "test", "start": 1.3, "end": 1.5}
            ]
        }]

        out_file = cut(
            self.test_video,
            segs,
            candidate,
            i=1,
            vid="test_ass_vid",
            aspect_ratio="9:16",
            caption_style="beasty",
            framing="crop",
            out_dir=out_dir,
            export_profile="1080p"
        )

        self.assertTrue(os.path.exists(out_file))
        self.assertTrue(os.path.getsize(out_file) > 1000)

        # Verify output dimensions via ffprobe
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_file
        ]
        dims = subprocess.check_output(probe_cmd).decode().strip()
        self.assertEqual(dims, "1080x1920")

if __name__ == "__main__":
    unittest.main()
