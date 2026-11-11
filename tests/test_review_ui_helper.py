import re
import unittest
from pathlib import Path


class ReviewUIHelperTests(unittest.TestCase):
    def test_escape_html_is_defined_before_review_renderer(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn('function escapeHtml(value)', source)
        self.assertLess(source.index('function escapeHtml(value)'), source.index('function showReviewScreen('))

    def test_escape_html_escapes_markup_and_quotes(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        match = re.search(r'function escapeHtml\(value\)\s*\{([\s\S]*?)\n    \}', source)
        self.assertIsNotNone(match)
        script = match.group(0) + "\nconsole.log(escapeHtml(`<img src=x onerror=\"x\"> & 'hi'`));"
        result = __import__('subprocess').run(['node', '-e', script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '&lt;img src=x onerror=&quot;x&quot;&gt; &amp; &#39;hi&#39;')

    def test_every_review_escape_call_has_helper(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertGreaterEqual(source.count('escapeHtml('), 4)

    def test_polling_uses_reported_stage_and_progress(self):
        source = Path('static/index.html').read_text(encoding='utf-8')
        self.assertIn("const stageLabel = data.stage || data.status || 'Processing...';", source)
        self.assertIn("updateStepIndicator(stageLabel, data.clips ? data.clips.length : 0, totalOpt, data.progress);", source)
        self.assertIn('function updateStepIndicator(status, currentCount, totalCount, reportedProgress)', source)


if __name__ == '__main__':
    unittest.main()
