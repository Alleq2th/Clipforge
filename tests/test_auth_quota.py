import os
import sys
import json
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import server
import jobs_db
import auth_quota

class TestAuthQuota(unittest.TestCase):
    def setUp(self):
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        jobs_db.init_db()
        auth_quota.init_user_tables()
        conn = auth_quota._get_local_db()
        with conn:
            conn.execute("DELETE FROM user_profiles;")
            conn.execute("DELETE FROM jobs;")
        conn.close()

    def test_default_user_profile_has_60_minutes(self):
        uid = "test-user-profile-1"
        profile = auth_quota.get_user_profile(uid, email="user1@example.com")
        self.assertEqual(profile["id"], uid)
        self.assertEqual(profile["free_minutes_total"], 60.0)
        self.assertEqual(profile["minutes_used"], 0.0)
        self.assertEqual(profile["minutes_remaining"], 60.0)
        self.assertEqual(profile["plan"], "free")
        self.assertFalse(profile["plan_selected"])

    def test_select_user_plan_activates_workspace(self):
        uid = "test-user-profile-2"
        updated = auth_quota.select_user_plan(uid, plan="free")
        self.assertTrue(updated["plan_selected"])
        self.assertEqual(updated["free_minutes_total"], 60.0)

    def test_quota_check_and_deduction(self):
        uid = "test-user-quota-1"
        auth_quota.select_user_plan(uid, plan="free")

        # 10 minute video check
        allowed, rem, req, reason = auth_quota.check_user_quota(uid, video_duration_seconds=600)
        self.assertTrue(allowed)
        self.assertEqual(rem, 60.0)
        self.assertEqual(req, 10.0)

        # Deduct 10 minutes
        after_deduct = auth_quota.deduct_user_quota(uid, video_duration_seconds=600)
        self.assertEqual(after_deduct["minutes_used"], 10.0)
        self.assertEqual(after_deduct["minutes_remaining"], 50.0)

        # 55 minute video check (exceeds remaining 50m)
        allowed, rem, req, reason = auth_quota.check_user_quota(uid, video_duration_seconds=3300)
        self.assertFalse(allowed)
        self.assertEqual(rem, 50.0)
        self.assertEqual(req, 55.0)

        # Deduct remaining 50 minutes
        exhausted = auth_quota.deduct_user_quota(uid, video_duration_seconds=3000)
        self.assertEqual(exhausted["minutes_remaining"], 0.0)
        self.assertEqual(exhausted["plan_status"], "exhausted")

        # Now any video is blocked
        allowed, rem, req, reason = auth_quota.check_user_quota(uid, video_duration_seconds=30)
        self.assertFalse(allowed)

    def test_api_quota_exhausted_enforcement(self):
        uid = "test-user-exhausted"
        auth_quota.select_user_plan(uid, plan="free")
        auth_quota.deduct_user_quota(uid, video_duration_seconds=3600) # consume all 60m

        headers = {"Authorization": f"Bearer {uid}"}
        res = self.client.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, headers=headers)
        self.assertEqual(res.status_code, 403)
        data = json.loads(res.data)
        self.assertEqual(data.get("code"), "QUOTA_EXHAUSTED")
        self.assertEqual(data.get("minutes_remaining"), 0.0)

    def test_user_private_library_isolation(self):
        user_a = "test-user-a"
        user_b = "test-user-b"

        # Create job for User A
        jobs_db.create_job("job_a_1", title="User A Video", user_id=user_a)
        # Create job for User B
        jobs_db.create_job("job_b_1", title="User B Video", user_id=user_b)

        # User A library
        lib_a = auth_quota.get_user_private_library(user_a)
        self.assertEqual(len(lib_a), 1)
        self.assertEqual(lib_a[0]["title"], "User A Video")

        # User B library
        lib_b = auth_quota.get_user_private_library(user_b)
        self.assertEqual(len(lib_b), 1)
        self.assertEqual(lib_b[0]["title"], "User B Video")

        # API endpoint library test with Authorization header
        res_a = self.client.get("/api/library", headers={"Authorization": f"Bearer {user_a}"})
        self.assertEqual(res_a.status_code, 200)
        data_a = json.loads(res_a.data)
        self.assertTrue(data_a.get("private"))
        self.assertEqual(data_a.get("user_id"), user_a)
        self.assertEqual(len(data_a.get("library", [])), 1)
        self.assertEqual(data_a["library"][0]["id"], "job_a_1")

if __name__ == "__main__":
    unittest.main()
