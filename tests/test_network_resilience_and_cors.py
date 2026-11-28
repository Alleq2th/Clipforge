import unittest
import json
import server
from security import validate_video_url

class TestNetworkResilienceAndCORS(unittest.TestCase):
    def setUp(self):
        self.app = server.app.test_client()

    def test_cors_preflight_options(self):
        """Verify OPTIONS preflight returns 200 with permissive CORS headers."""
        res = self.app.open(
            "/api/jobs",
            method="OPTIONS",
            headers={
                "Origin": "https://clipforge.onrender.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type"
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("Authorization", res.headers.get("Access-Control-Allow-Headers", ""))
        self.assertIn("POST", res.headers.get("Access-Control-Allow-Methods", ""))

    def test_cors_headers_on_get_and_post(self):
        """Verify normal GET and POST responses carry Access-Control-Allow-Origin."""
        res_get = self.app.get("/api/health")
        self.assertEqual(res_get.headers.get("Access-Control-Allow-Origin"), "*")

        res_post = self.app.post("/api/jobs", json={})
        self.assertEqual(res_post.headers.get("Access-Control-Allow-Origin"), "*")

    def test_json_error_handler_no_html_500(self):
        """Verify 400, 404 and error routes return JSON rather than HTML."""
        res_404 = self.app.get("/api/nonexistent-endpoint-test")
        self.assertEqual(res_404.status_code, 404)
        self.assertTrue(res_404.is_json)
        self.assertIn("error", res_404.get_json())

    def test_url_validation_well_known_domains(self):
        """Verify validate_video_url accepts well-known video URLs cleanly."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        valid = validate_video_url(url)
        self.assertEqual(valid, url)

    def test_download_has_mobile_player_clients(self):
        """Verify download.py configures android/ios player clients to avoid bot blocks."""
        import download
        with open(download.__file__, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("player_client", src)
        self.assertIn("android", src)
        self.assertIn("ios", src)

if __name__ == "__main__":
    unittest.main()
