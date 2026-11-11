import unittest
from unittest.mock import MagicMock, patch
import os
import tempfile
import yt_dlp
import download

class TestSubtitleResilience(unittest.TestCase):
    def test_opts_has_ignoreerrors_true(self):
        """Verifies that yt-dlp options include ignoreerrors: True so subtitle 429 errors do not crash downloads."""
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_instance = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.return_value = {"id": "test12345"}
            mock_instance.prepare_filename.return_value = "source_test12345.mp4"

            with tempfile.TemporaryDirectory() as tmp_dir:
                test_file = os.path.join(tmp_dir, "source_test12345.mp4")
                with open(test_file, "wb") as f:
                    f.write(b"dummy mp4 data")

                download.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", dest_dir=tmp_dir)

                # Check the options passed into YoutubeDL
                self.assertTrue(mock_ydl_cls.called)
                opts = mock_ydl_cls.call_args[0][0]
                self.assertIn("ignoreerrors", opts)
                self.assertEqual(opts["ignoreerrors"], True)
                self.assertEqual(opts["writesubtitles"], True)

    def test_subtitle_429_warning_does_not_abort_when_video_downloaded(self):
        """Simulates yt-dlp raising a subtitle download warning/error but completing video download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = os.path.join(tmp_dir, "source_vid999.mp4")
            with open(test_file, "wb") as f:
                f.write(b"video bytes")

            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_instance = MagicMock()
                mock_ydl_cls.return_value.__enter__.return_value = mock_instance
                mock_instance.extract_info.return_value = {"id": "vid999"}
                mock_instance.prepare_filename.return_value = test_file

                res_path, vid = download.download("https://www.youtube.com/watch?v=vid999", dest_dir=tmp_dir)
                self.assertEqual(res_path, test_file)
                self.assertEqual(vid, "vid999")

if __name__ == "__main__":
    unittest.main()
