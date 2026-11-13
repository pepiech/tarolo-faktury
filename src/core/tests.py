from unittest.mock import patch

from django.test import SimpleTestCase


class HomePageTests(SimpleTestCase):
    @patch("core.views.database_status", return_value="17")
    def test_shows_valid_cloudflare_access_email(self, _database_status):
        response = self.client.get(
            "/",
            HTTP_CF_ACCESS_AUTHENTICATED_USER_EMAIL="User@F13CyberTech.cz",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "user@f13cybertech.cz")

    @patch("core.views.database_status", return_value="17")
    def test_rejects_malformed_access_email(self, _database_status):
        response = self.client.get(
            "/",
            HTTP_CF_ACCESS_AUTHENTICATED_USER_EMAIL="not-an-email",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "not-an-email")
        self.assertContains(response, "Lokální kontrola serveru")


class HealthTests(SimpleTestCase):
    @patch("core.views.database_status", return_value="17")
    def test_reports_ok_with_environment_and_revision(self, _database_status):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["application"], "faktury")
        self.assertIn("environment", payload)
        self.assertRegex(payload["revision"], r"^(dev|[0-9a-f]{40})$")

    @patch("core.views.database_status", side_effect=RuntimeError("db down"))
    def test_returns_503_when_database_unavailable(self, _database_status):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["database"], "unavailable")
