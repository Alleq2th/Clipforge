import os
import sys
import json
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import server
import jobs_db
import pipeline

class TestClipReview(unittest.TestCase):
    def setUp(self):
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        jobs_db.init_db()

    def test_review_events_coordination(self):
        jid = "test_jid_review_events"
        evt = pipeline.get_review_event(jid)
        self.assertFalse(evt.is_set())

        clips = [
            {"start": 10.0, "end": 35.0, "title": "Great Moment 1", "score": 95},
            {"start": 40.0, "end": 65.0, "title": "Great Moment 2", "score": 92}
        ]
        pipeline.set_confirmed_clips(jid, clips)
        self.assertTrue(evt.is_set())

        pulled = pipeline.get_confirmed_clips(jid)
        self.assertEqual(len(pulled), 2)
        self.assertEqual(pulled[0]["title"], "Great Moment 1")

        # Cleanup
        pipeline.cleanup_review(jid)

    def test_cancel_review_releases_event(self):
        jid = "test_jid_cancel_review"
        evt = pipeline.get_review_event(jid)
        self.assertFalse(evt.is_set())
        pipeline.cancel_review(jid)
        self.assertTrue(evt.is_set())
        self.assertIsNone(pipeline.get_confirmed_clips(jid))

    def test_review_api_endpoints(self):
        jid = "test_review_job_1"
        candidates = [
            {"start": 5.0, "end": 25.0, "title": "Discovered Clip 1", "score": 88, "hook_reason": "Curiosity hook"},
            {"start": 30.0, "end": 50.0, "title": "Discovered Clip 2", "score": 94, "hook_reason": "High action"}
        ]
        jobs_db.create_job(jid, title="Review Test Video", candidates=candidates, options={"review_clips": True})
        jobs_db.update_job(jid, status="awaiting_review", stage="Review & trim clips", candidates=candidates)

        # GET /api/jobs/<jid>/review
        res = self.client.get(f"/api/jobs/{jid}/review")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "awaiting_review")
        self.assertEqual(len(data["candidates"]), 2)
        self.assertEqual(data["candidates"][0]["title"], "Discovered Clip 1")

        # POST /api/jobs/<jid>/confirm-review (valid selection with trim)
        confirmed_selection = [
            {"start": 6.5, "end": 24.0, "title": "Trimmed Clip 1", "score": 90}
        ]
        res_post = self.client.post(
            f"/api/jobs/{jid}/confirm-review",
            data=json.dumps({"clips": confirmed_selection}),
            content_type="application/json"
        )
        self.assertEqual(res_post.status_code, 200)
        post_data = json.loads(res_post.data)
        self.assertTrue(post_data["ok"])
        self.assertEqual(post_data["count"], 1)

        # Confirmed clips should be available in pipeline
        ready_clips = pipeline.get_confirmed_clips(jid)
        self.assertIsNotNone(ready_clips)
        self.assertEqual(len(ready_clips), 1)
        self.assertEqual(ready_clips[0]["title"], "Trimmed Clip 1")
        self.assertEqual(ready_clips[0]["start"], 6.5)

    def test_confirm_review_validation(self):
        jid = "test_review_invalid"
        jobs_db.create_job(jid, title="Invalid Review Test")

        # Empty clips list
        res = self.client.post(
            f"/api/jobs/{jid}/confirm-review",
            data=json.dumps({"clips": []}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 400)

        # Inverted start and end times
        res_inv = self.client.post(
            f"/api/jobs/{jid}/confirm-review",
            data=json.dumps({"clips": [{"start": 50, "end": 10}]}),
            content_type="application/json"
        )
        self.assertEqual(res_inv.status_code, 400)

    def test_confirm_review_with_custom_export_profile(self):
        jid = "test_review_custom_profile"
        candidates = [
            {"start": 10.0, "end": 30.0, "title": "Test 720p Clip", "score": 91}
        ]
        jobs_db.create_job(jid, title="Profile Test Video", candidates=candidates, options={"review_clips": True, "export_profile": "1080p"})
        jobs_db.update_job(jid, status="awaiting_review", stage="Review & trim clips", candidates=candidates)

        # Confirm review specifying 720p
        res = self.client.post(
            f"/api/jobs/{jid}/confirm-review",
            data=json.dumps({"clips": candidates, "export_profile": "720p"}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(pipeline.get_confirmed_profile(jid), "720p")

        job = jobs_db.get_job(jid)
        self.assertEqual(job["options"].get("export_profile"), "720p")

if __name__ == "__main__":
    unittest.main()
