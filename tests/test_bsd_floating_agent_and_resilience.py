import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import EXPORT_PROFILES

class TestBsdFloatingAgentAndResilience(unittest.TestCase):
    def setUp(self):
        self.html = Path('static/index.html').read_text(encoding='utf-8')

    def test_live_clips_wrap_positioned_above_batch_container(self):
        """Verify #live-clips-wrap is positioned before #live-batch-status-container so clips render up top."""
        clips_wrap_idx = self.html.find('id="live-clips-wrap"')
        batch_cont_idx = self.html.find('id="live-batch-status-container"')
        self.assertNotEqual(clips_wrap_idx, -1)
        self.assertNotEqual(batch_cont_idx, -1)
        self.assertLess(clips_wrap_idx, batch_cont_idx, "#live-clips-wrap must be placed above #live-batch-status-container")

    def test_bsd_floating_agent_exists(self):
        """Verify the BSD floating agent pill and badge exist."""
        self.assertIn('id="bsd-floating-agent"', self.html)
        self.assertIn('id="bsd-badge"', self.html)
        self.assertIn('toggleBsdModal()', self.html)
        self.assertIn('id="bsd-modal"', self.html)
        self.assertIn('id="bsd-modal-content"', self.html)

    def test_discovery_notice_modal_exists(self):
        """Verify the Live Pipeline Status discovery popup modal exists."""
        self.assertIn('id="discovery-notice-modal"', self.html)
        self.assertIn('id="discovery-notice-text"', self.html)
        self.assertIn('dismissDiscoveryNotice()', self.html)
        self.assertIn('showDiscoveryNotice', self.html)

    def test_grid_dot_skeleton_css_and_markup(self):
        """Verify cyberpunk grid dot matrix styles and laser sweep animation exist."""
        self.assertIn('.clip-skel', self.html)
        self.assertIn('skel-laser-beam', self.html)
        self.assertIn('laserSweep', self.html)
        self.assertIn('skelGridWave', self.html)
        self.assertIn('skel-hud', self.html)
        self.assertIn('skel-wave-dots', self.html)

    def test_resilient_polling_error_threshold(self):
        """Verify polling error tolerance is increased for mobile background resilience."""
        self.assertIn('consecutiveErrors > 120', self.html)

    def test_fast_720p_rendering_profile(self):
        """Verify 720p export profile uses ultrafast preset and audio optimizations for high rendering speed."""
        profile_720 = EXPORT_PROFILES["720p"]
        self.assertEqual(profile_720["preset"], "ultrafast")
        self.assertFalse(profile_720["normalize_audio"])

if __name__ == "__main__":
    unittest.main()
