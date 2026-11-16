"""Offline checks that E2E failure diagnostics never expose remote payloads."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
import urllib.error

import e2e_sync_cleanup401 as e2e401
import e2e_sync_cleanup406 as e2e406


class E2EDiagnostics414Tests(unittest.TestCase):
    def test_http_error_reports_only_status_and_safe_code(self):
        secret_fields = {
            "code": "NATIVE_CREDENTIALS_INVALID",
            "message": "username=private@example.invalid password=private-pass token=private-token",
        }
        failure = urllib.error.HTTPError(
            e2e401.API + e2e401.LOGIN, 401, "Unauthorized", {},
            io.BytesIO(json.dumps(secret_fields).encode()),
        )
        with patch.object(e2e401.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaises(e2e401.SafeFailure) as caught:
                e2e401._request("POST", e2e401.LOGIN, body={"username": "private@example.invalid",
                                                              "password": "private-pass"})
        message = str(caught.exception)
        self.assertIn("HTTP 401", message)
        self.assertIn("code=NATIVE_CREDENTIALS_INVALID", message)
        for private_value in ("private@example.invalid", "private-pass", "private-token", "Unauthorized"):
            self.assertNotIn(private_value, message)

    def test_untrusted_code_text_and_body_are_suppressed(self):
        failure = urllib.error.HTTPError(
            e2e401.API + e2e401.LOGIN, 403, "Forbidden", {},
            io.BytesIO(b'{"code":"PRIVATE_TOKEN_VALUE","message":"do not print"}'),
        )
        with patch.object(e2e401.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaises(e2e401.SafeFailure) as caught:
                e2e401._request("GET", e2e401.WORKSPACES)
        self.assertEqual(str(caught.exception), "HTTP request failed; HTTP 403")

    def test_count_formatter_contains_only_numeric_evidence(self):
        text = e2e401._count_text((12, 7, 3))
        self.assertEqual(text, "entities=12,requests=7,accounts=3")
        self.assertIs(e2e406.SafeFailure, e2e401.SafeFailure)


if __name__ == "__main__":
    unittest.main()
