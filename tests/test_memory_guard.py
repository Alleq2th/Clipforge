import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import memory_guard

class TestMemoryGuard(unittest.TestCase):
    def test_tiny_tier_forces_one_clip_worker(self):
        self.assertEqual(memory_guard.get_adaptive_clip_workers(4, "540p", limit_mb=512), 1)
        self.assertEqual(memory_guard.get_adaptive_clip_workers(4, "1080p", limit_mb=768), 1)

    def test_low_tier_caps_1080p_at_one_worker(self):
        self.assertEqual(memory_guard.get_adaptive_clip_workers(4, "1080p", limit_mb=1024), 1)

    def test_low_tier_caps_540p_at_two_workers(self):
        self.assertEqual(memory_guard.get_adaptive_clip_workers(4, "540p", limit_mb=1024), 2)

    def test_medium_tier_caps_at_two_workers(self):
        self.assertEqual(memory_guard.get_adaptive_clip_workers(4, "540p", limit_mb=2048), 2)

    def test_high_tier_caps_at_four_workers(self):
        self.assertEqual(memory_guard.get_adaptive_clip_workers(9, "1080p", limit_mb=4096), 4)

    def test_force_serial_option_wins(self):
        self.assertEqual(memory_guard.get_adaptive_clip_workers(4, "540p", options={"force_serial": True}, limit_mb=4096), 1)

    def test_memory_tier_boundaries(self):
        self.assertEqual(memory_guard.get_memory_tier(768), "TINY")
        self.assertEqual(memory_guard.get_memory_tier(1024), "LOW")
        self.assertEqual(memory_guard.get_memory_tier(2048), "MEDIUM")
        self.assertEqual(memory_guard.get_memory_tier(4096), "HIGH")

    def test_tiny_tier_uses_low_memory_ffmpeg_flags(self):
        params = memory_guard.get_adaptive_ffmpeg_params("540p", limit_mb=512)
        self.assertEqual(params["threads"], "1")
        self.assertEqual(params["preset"], "ultrafast")
        self.assertIn("-max_muxing_queue_size", params["cli_flags"])

    def test_1080p_low_tier_uses_single_thread(self):
        params = memory_guard.get_adaptive_ffmpeg_params("1080p", limit_mb=1024)
        self.assertEqual(params["threads"], "1")
        self.assertEqual(params["preset"], "ultrafast")

    def test_render_slot_releases_after_exception(self):
        limiter = memory_guard.GlobalRenderLimiter.get_instance()
        before = limiter.active_slots
        with self.assertRaises(RuntimeError):
            with limiter.acquire_slot(timeout=1):
                self.assertEqual(limiter.active_slots, before + 1)
                raise RuntimeError("simulated render error")
        self.assertEqual(limiter.active_slots, before)

    def test_memory_pressure_triggers_gc(self):
        with patch("memory_guard.get_available_memory_mb", return_value=50), patch("memory_guard.enforce_gc") as gc_mock:
            self.assertTrue(memory_guard.check_memory_pressure())
            gc_mock.assert_called_once()

if __name__ == "__main__":
    unittest.main()
