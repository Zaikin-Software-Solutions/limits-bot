"""Checks for actionable auth failures and deduplicated health alerts."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("CHAT_ID", "123456")
os.environ.setdefault("LIMITS_TOKEN", "test")

from app.health import (  # noqa: E402
    AuthExpiry,
    Check,
    _expiry_alerts,
    _update_check_state,
    codex_auth_expiries,
    format_report,
    probe_model,
)


class _Client:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        return self.response


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_unavailable_503_is_actionable(self):
        response = httpx.Response(
            503,
            json={"error": {"message": "auth_unavailable: no auth available"}},
            request=httpx.Request("POST", "https://proxy.example/v1/chat/completions"),
        )
        with patch("app.health.httpx.AsyncClient", return_value=_Client(response)):
            check = await probe_model("Claude", "https://proxy.example/v1/claude/limits", "key", "model")
        self.assertFalse(check.ok)
        self.assertTrue(check.auth_failure)
        self.assertNotIn("no auth available", format_report([check], [], None))

    async def test_real_completion_required(self):
        response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
            request=httpx.Request("POST", "https://proxy.example/v1/chat/completions"),
        )
        with patch("app.health.httpx.AsyncClient", return_value=_Client(response)):
            check = await probe_model("Claude", "https://proxy.example/v1/claude/limits", "key", "model")
        self.assertTrue(check.ok)


class StateTests(unittest.TestCase):
    def test_transient_failure_and_recovery_are_debounced(self):
        history = {}
        failed = Check("Claude", False, "HTTP 503")
        ok = Check("Claude", True, "ответ")
        self.assertIsNone(_update_check_state(history, "claude", failed))
        self.assertIn("Проблема", _update_check_state(history, "claude", failed))
        self.assertIsNone(_update_check_state(history, "claude", failed))
        self.assertIsNone(_update_check_state(history, "claude", ok))
        self.assertIn("Восстановлено", _update_check_state(history, "claude", ok))

    def test_auth_failure_alerts_on_first_probe(self):
        self.assertIn(
            "Проблема",
            _update_check_state({}, "claude", Check("Claude", False, "авторизация", auth_failure=True)),
        )

    def test_expiry_warning_deduplicates_and_renews(self):
        history = {}
        expiry = AuthExpiry("account", datetime.now(timezone.utc) + timedelta(hours=36))
        self.assertEqual(len(_expiry_alerts(history, [expiry])), 1)
        self.assertEqual(_expiry_alerts(history, [expiry]), [])
        renewed = AuthExpiry("account", datetime.now(timezone.utc) + timedelta(hours=30))
        self.assertEqual(len(_expiry_alerts(history, [renewed])), 1)

    def test_auth_file_uses_expiry_metadata_without_exposing_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            Path(tmp, "codex.json").write_text(json.dumps({
                "type": "codex", "email": "test@example.com", "expired": expires,
                "access_token": "secret", "refresh_token": "secret",
            }))
            expiries, error = codex_auth_expiries(tmp)
        self.assertIsNone(error)
        self.assertEqual(expiries[0].name, "test@example.com")
        self.assertNotIn("secret", format_report([], expiries, None))


if __name__ == "__main__":
    unittest.main()
