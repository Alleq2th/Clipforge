import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import download
import captions
import pick
import transcribe

class TestPhase2Intelligence(unittest.TestCase):

    def test_download_scoping(self):
        """Verifies yt_dlp is module-level and cannot raise UnboundLocalError."""
        self.assertTrue(hasattr(download, "yt_dlp"))
        self.assertTrue(hasattr(download.yt_dlp, "YoutubeDL"))

    def test_captions_word_level_timestamps(self):
        """Tests that genuine word-level timestamps produce precise SRT timing."""
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
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
            srt_path = tf.name

        try:
            captions.make_srt(segs, start=10.0, end=15.0, path=srt_path, words=3)
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("THIS IS A", content)
            self.assertIn("VIRAL HOOK STATEMENT", content)
            # Word 1 starts at 10.0 -> offset 0.0s -> 00:00:00,000
            self.assertIn("00:00:00,000 --> 00:00:01,000", content)
        finally:
            if os.path.exists(srt_path):
                os.remove(srt_path)

    def test_captions_segment_level_fallback(self):
        """Tests fallback when segments do not carry word-level timings."""
        segs = [{
            "start": 5.0,
            "end": 10.0,
            "text": "Hello world welcome back",
            "words": []
        }]
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
            srt_path = tf.name

        try:
            captions.make_srt(segs, start=5.0, end=10.0, path=srt_path, words=2)
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("HELLO WORLD", content)
            self.assertIn("WELCOME BACK", content)
        finally:
            if os.path.exists(srt_path):
                os.remove(srt_path)

    def test_pick_complete_transcript_analysis(self):
        """
        Tests that pick processes long video transcripts (>15 minutes)
        using overlapping windows and yields non-overlapping clips.
        """
        # Generate 15-minute mock transcript (900 seconds)
        segs = []
        for i in range(90):
            st = i * 10.0
            en = st + 9.5
            segs.append({
                "start": st,
                "end": en,
                "text": f"This is conversational block number {i} discussing important ideas."
            })

        # Request 10 clips between 30 and 60 seconds
        clips = pick.pick(segs, count=10, min_len=30, max_len=60, focus="viral")
        self.assertGreater(len(clips), 0)
        self.assertLessEqual(len(clips), 10)

        # Verify duration constraints on all returned clips
        for c in clips:
            dur = c["end"] - c["start"]
            self.assertGreaterEqual(dur, 25.0)
            self.assertLessEqual(dur, 65.0)
            self.assertIn("title", c)
            self.assertIn("hook_reason", c)
            self.assertIn("score", c)

        # Verify no severe overlaps between clips
        for i in range(len(clips) - 1):
            curr_end = clips[i]["end"]
            next_start = clips[i + 1]["start"]
            # Overlap should not exceed 8 seconds
            overlap = max(0.0, curr_end - next_start)
            self.assertLessEqual(overlap, 8.0)

    def test_pick_generate_up_to_20_clips(self):
        """Tests that system can generate up to 20 clips from a long-form video."""
        segs = []
        for i in range(180): # 30 minutes
            st = i * 10.0
            en = st + 9.5
            segs.append({
                "start": st,
                "end": en,
                "text": f"Minute {int(st//60)} topic segment {i} key talking point."
            })

        clips = pick.pick(segs, count=20, min_len=20, max_len=60, focus="viral")
        self.assertGreaterEqual(len(clips), 15)
        self.assertLessEqual(len(clips), 20)

if __name__ == "__main__":
    unittest.main()
