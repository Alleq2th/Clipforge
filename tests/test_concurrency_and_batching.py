import os
import sys
import unittest
import tempfile
import shutil
import subprocess
import numpy as np
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import transcribe
import pipeline
from config import MAX_CLIP_WORKERS

class TestConcurrencyAndBatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="clipforge_batch_test_")
        cls.dummy_audio = os.path.join(cls.test_dir, "test_audio.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-ar", "16000", "-ac", "1", cls.dummy_audio
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_load_audio_buffer(self):
        """Verifies _load_audio_buffer decodes to 16kHz float32 numpy array."""
        buf = transcribe._load_audio_buffer(self.dummy_audio)
        self.assertIsInstance(buf, np.ndarray)
        self.assertEqual(buf.dtype, np.float32)
        # 2 seconds at 16000Hz = 32000 samples
        self.assertEqual(len(buf), 32000)

    def test_batched_transcription_fallback_to_standard(self):
        """When BatchedInferencePipeline throws an error, it safely falls back to standard transcribe."""
        class MockWord:
            def __init__(self, word, start, end):
                self.word = word
                self.start = start
                self.end = end

        class MockSegment:
            def __init__(self, text, start, end, words):
                self.text = text
                self.start = start
                self.end = end
                self.words = words

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            [MockSegment("Fallback success", 0.0, 2.0, [MockWord("Fallback", 0.0, 1.0), MockWord("success", 1.0, 2.0)])],
            MagicMock()
        )

        with patch("transcribe._get_local_whisper", return_value=mock_model):
            with patch("faster_whisper.BatchedInferencePipeline", side_effect=RuntimeError("Simulated batch failure")):
                result = transcribe._transcribe_local(self.dummy_audio)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["text"], "Fallback success")
                self.assertEqual(len(result[0]["words"]), 2)

    def test_concurrent_rendering_worker_clamping(self):
        """Worker count is clamped within [1, 4] to protect container memory."""
        self.assertGreaterEqual(MAX_CLIP_WORKERS, 1)
        self.assertLessEqual(MAX_CLIP_WORKERS, 4)

    def test_concurrent_rendering_execution_and_ordering(self):
        """Verifies concurrent rendering produces correctly ordered output clips."""
        clips = [
            {"start": 0.0, "end": 1.0, "title": "Clip 1"},
            {"start": 1.0, "end": 2.0, "title": "Clip 2"},
            {"start": 2.0, "end": 3.0, "title": "Clip 3"},
        ]
        segs = [{"start": 0.0, "end": 3.0, "text": "Testing concurrent clip render pipeline"}]

        # Mock download, transcribe, pick, and cut
        with patch("pipeline.download", return_value=("/tmp/fake_video.mp4", "fake_vid")), \
             patch("pipeline.transcribe", return_value=segs), \
             patch("pipeline.pick", return_value=clips), \
             patch("pipeline.cut", side_effect=lambda video, s, c, i, *args, **kwargs: f"/tmp/output/clip{i}.mp4"):

            rendered = pipeline.run("https://www.youtube.com/watch?v=dQw4w9WgXcQ", jid="test_concurrent", options={"max_workers": 2})

            self.assertEqual(len(rendered), 3)
            # Ensure output is strictly sorted by index 1, 2, 3
            indices = [r["index"] for r in rendered]
            self.assertEqual(indices, [1, 2, 3])
            for r in rendered:
                self.assertTrue(r["ok"])
                self.assertIn(f"clip{r['index']}.mp4", r["file"])

    def test_concurrent_rendering_fallback_to_serial_when_workers_is_one(self):
        """When max_workers=1, execution falls back cleanly to the serial loop."""
        clips = [
            {"start": 0.0, "end": 1.0, "title": "Serial Clip 1"},
            {"start": 1.0, "end": 2.0, "title": "Serial Clip 2"},
        ]
        with patch("pipeline.download", return_value=("/tmp/fake_video.mp4", "fake_vid")), \
             patch("pipeline.transcribe", return_value=[]), \
             patch("pipeline.pick", return_value=clips), \
             patch("pipeline.cut", side_effect=lambda video, s, c, i, *args, **kwargs: f"/tmp/output/clip{i}.mp4"):

            rendered = pipeline.run("https://www.youtube.com/watch?v=dQw4w9WgXcQ", jid="test_serial", options={"max_workers": 1})

            self.assertEqual(len(rendered), 2)
            self.assertEqual([r["index"] for r in rendered], [1, 2])

    def test_concurrent_rendering_resilient_to_individual_clip_failure(self):
        """If one clip fails in the concurrent pool, other clips still succeed and error is recorded."""
        clips = [
            {"start": 0.0, "end": 1.0, "title": "Good Clip 1"},
            {"start": 1.0, "end": 2.0, "title": "Failing Clip 2"},
            {"start": 2.0, "end": 3.0, "title": "Good Clip 3"},
        ]

        def mock_cut(video, s, c, i, *args, **kwargs):
            if i == 2:
                raise RuntimeError("FFmpeg seek error on clip 2")
            return f"/tmp/output/clip{i}.mp4"

        with patch("pipeline.download", return_value=("/tmp/fake_video.mp4", "fake_vid")), \
             patch("pipeline.transcribe", return_value=[]), \
             patch("pipeline.pick", return_value=clips), \
             patch("pipeline.cut", side_effect=mock_cut):

            rendered = pipeline.run("https://www.youtube.com/watch?v=dQw4w9WgXcQ", jid="test_resilience", options={"max_workers": 2})

            self.assertEqual(len(rendered), 3)
            self.assertTrue(rendered[0]["ok"])
            self.assertFalse(rendered[1]["ok"])
            self.assertIn("FFmpeg seek error", rendered[1]["error"])
            self.assertTrue(rendered[2]["ok"])

if __name__ == "__main__":
    unittest.main()
