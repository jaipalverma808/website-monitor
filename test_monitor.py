"""
Automated Test Suite for Website Uptime & Keep-Alive Monitoring System.
Verifies CRUD operations, timeout handling, connection failures, status transitions,
one-site-failing resilience, and Flask dashboard endpoints.
"""
import unittest
from unittest.mock import patch, MagicMock
import requests

from config import SQLITE_PATH
from database import init_db, execute_db, query_db
from models import (
    get_all_websites, get_website_by_id, add_website,
    update_website, toggle_website, delete_website,
    record_check_result, get_website_stats, get_dashboard_summary
)
from monitor import check_single_website, run_monitor
from app import app


class TestDatabaseAndModels(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_add_and_get_website(self):
        site_id = add_website(
            name="Test Site Alpha",
            website_url="https://alpha.example.com/",
            health_url="https://alpha.example.com/health",
            github_repo="https://github.com/example/alpha",
            failure_threshold=3
        )
        self.assertIsNotNone(site_id)
        site = get_website_by_id(site_id)
        self.assertIsNotNone(site)
        self.assertEqual(site["name"], "Test Site Alpha")
        self.assertEqual(site["website_url"], "https://alpha.example.com/")
        self.assertEqual(site["health_url"], "https://alpha.example.com/health")
        self.assertEqual(site["github_repo"], "https://github.com/example/alpha")
        self.assertTrue(site["enabled"])

        # Clean up
        delete_website(site_id)

    def test_update_and_toggle_website(self):
        site_id = add_website(
            name="Toggle Target",
            website_url="https://toggle.example.com/"
        )
        # Toggle disabled
        new_state = toggle_website(site_id)
        self.assertFalse(new_state)
        site = get_website_by_id(site_id)
        self.assertFalse(site["enabled"])

        # Toggle enabled
        new_state = toggle_website(site_id)
        self.assertTrue(new_state)

        # Update
        update_website(
            site_id=site_id,
            name="Updated Target Name",
            website_url="https://updated.example.com/",
            health_url="https://updated.example.com/health",
            failure_threshold=5,
            enabled=True
        )
        updated = get_website_by_id(site_id)
        self.assertEqual(updated["name"], "Updated Target Name")
        self.assertEqual(updated["failure_threshold"], 5)

        delete_website(site_id)

    def test_status_transition_rules(self):
        """Tests: 1 failure -> Warning, 3 failures -> Offline, success -> Online."""
        site_id = add_website(
            name="Threshold Test Site",
            website_url="https://threshold.example.com/",
            failure_threshold=3
        )

        # 1st failure -> WARNING
        record_check_result(
            website_id=site_id,
            is_success=False,
            http_status=500,
            response_time_ms=250,
            target_url="https://threshold.example.com/",
            error_message="HTTP 500 error",
            status_result="WARNING"
        )
        s1 = get_website_by_id(site_id)
        self.assertEqual(s1["consecutive_failures"], 1)
        self.assertEqual(s1["last_status"], "WARNING")

        # 2nd failure -> WARNING
        record_check_result(
            website_id=site_id,
            is_success=False,
            http_status=500,
            response_time_ms=210,
            target_url="https://threshold.example.com/",
            error_message="HTTP 500 error",
            status_result="WARNING"
        )
        s2 = get_website_by_id(site_id)
        self.assertEqual(s2["consecutive_failures"], 2)

        # 3rd failure -> OFFLINE
        record_check_result(
            website_id=site_id,
            is_success=False,
            http_status=500,
            response_time_ms=200,
            target_url="https://threshold.example.com/",
            error_message="HTTP 500 error",
            status_result="OFFLINE"
        )
        s3 = get_website_by_id(site_id)
        self.assertEqual(s3["consecutive_failures"], 3)
        self.assertEqual(s3["last_status"], "OFFLINE")

        # Recovery -> ONLINE (resets failures to 0)
        record_check_result(
            website_id=site_id,
            is_success=True,
            http_status=200,
            response_time_ms=180,
            target_url="https://threshold.example.com/",
            error_message=None,
            status_result="ONLINE"
        )
        s4 = get_website_by_id(site_id)
        self.assertEqual(s4["consecutive_failures"], 0)
        self.assertEqual(s4["last_status"], "ONLINE")

        stats = get_website_stats(site_id)
        self.assertEqual(stats["total_checks"], 4)
        self.assertEqual(stats["successful_checks"], 1)
        self.assertEqual(stats["failed_checks"], 3)
        self.assertEqual(stats["uptime_percentage"], 25.0)

        delete_website(site_id)


class TestMonitoringEngine(unittest.TestCase):
    def setUp(self):
        init_db()

    @patch("requests.get")
    def test_successful_request(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        site_id = add_website(name="Mock Success", website_url="https://mock.ok/health")
        site = get_website_by_id(site_id)

        res = check_single_website(site)
        self.assertTrue(res["is_success"])
        self.assertEqual(res["http_status"], 200)
        self.assertEqual(res["status_result"], "ONLINE")

        delete_website(site_id)

    @patch("requests.get")
    def test_timeout_handling(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

        site_id = add_website(name="Mock Timeout", website_url="https://mock.timeout/")
        site = get_website_by_id(site_id)

        res = check_single_website(site)
        self.assertFalse(res["is_success"])
        self.assertIn("timed out", res["error_message"])
        self.assertEqual(res["status_result"], "WARNING")

        delete_website(site_id)

    @patch("requests.get")
    def test_connection_error_handling(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("Failed to resolve host")

        site_id = add_website(name="Mock ConnErr", website_url="https://nonexistent.domain.xyz/")
        site = get_website_by_id(site_id)

        res = check_single_website(site)
        self.assertFalse(res["is_success"])
        self.assertIn("Connection failed", res["error_message"])

        delete_website(site_id)

    @patch("requests.get")
    def test_resilience_one_fails_others_succeed(self, mock_get):
        """Simulates 3 websites where website 2 throws an exception and the other two succeed."""
        def side_effect(url, **kwargs):
            if "fail" in url:
                raise requests.exceptions.ConnectionError("Network down")
            mock = MagicMock()
            mock.status_code = 200
            return mock

        mock_get.side_effect = side_effect

        s1_id = add_website(name="Resilient 1", website_url="https://site1.com/health")
        s2_id = add_website(name="Resilient 2 (Fails)", website_url="https://fail.com/health")
        s3_id = add_website(name="Resilient 3", website_url="https://site3.com/health")

        # Disable all others temporarily to isolate test
        execute_db("UPDATE websites SET enabled = false WHERE id NOT IN (%s, %s, %s)", (s1_id, s2_id, s3_id))

        results = run_monitor()
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0]["is_success"])
        self.assertFalse(results[1]["is_success"])
        self.assertTrue(results[2]["is_success"])

        # Re-enable all
        execute_db("UPDATE websites SET enabled = true")
        delete_website(s1_id)
        delete_website(s2_id)
        delete_website(s3_id)


class TestDashboardFlaskRoutes(unittest.TestCase):
    def setUp(self):
        init_db()
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_dashboard_home(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Website Monitor", response.data)
        self.assertIn(b"Total Websites", response.data)
        self.assertIn(b"ResumeForge", response.data)

    def test_website_details_page(self):
        sites = get_all_websites()
        if sites:
            site_id = sites[0]["id"]
            response = self.client.get(f"/website/{site_id}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Uptime", response.data)
            self.assertIn(b"Recent Monitoring History", response.data)

    def test_settings_page(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Architecture", response.data)
        self.assertIn(b"GitHub Actions", response.data)

    def test_api_status_json(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("summary", data)
        self.assertIn("websites", data)
        self.assertGreaterEqual(len(data["websites"]), 4)

    def test_add_and_delete_via_dashboard(self):
        post_data = {
            "name": "Dashboard Add Test",
            "website_url": "https://test-dashboard.onrender.com/",
            "health_url": "https://test-dashboard.onrender.com/health",
            "github_repo": "https://github.com/test/repo",
            "failure_threshold": "4"
        }
        res = self.client.post("/api/websites", data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Dashboard Add Test", res.data)

        # Retrieve created site
        created = query_db("SELECT id FROM websites WHERE name = %s", ("Dashboard Add Test",), one=True)
        self.assertIsNotNone(created)

        # Delete it
        del_res = self.client.post(f"/api/websites/{created['id']}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
