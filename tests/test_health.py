"""Checks for actionable auth failures, deduplicated alerts and the per-model matrix."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("CHAT_ID", "123456")
os.environ.setdefault("LIMITS_TOKEN", "test")

from app.health import (  # noqa: E402
    Check,
    _is_chat_model,
    _update_check_state,
    format_models_report,
    format_report,
    probe_model,
    probe_source_models,
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


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status, json=body,
        request=httpx.Request("POST", "https://proxy.example/v1/chat/completions"),
    )


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_unavailable_503_is_actionable(self):
        response = _resp(503, {"error": {"message": "auth_unavailable: no auth available"}})
        with patch("app.health.httpx.AsyncClient", return_value=_Client(response)):
            check = await probe_model("Claude", "https://proxy.example/v1/claude/limits", "key", "model")
        self.assertFalse(check.ok)
        self.assertTrue(check.auth_failure)
        self.assertNotIn("no auth available", format_report([check]))

    async def test_real_completion_required(self):
        response = _resp(200, {"choices": [{"message": {"content": "OK"}}]})
        with patch("app.health.httpx.AsyncClient", return_value=_Client(response)):
            check = await probe_model("Claude", "https://proxy.example/v1/claude/limits", "key", "model")
        self.assertTrue(check.ok)

    async def test_relaxed_accepts_empty_text_from_reasoning_model(self):
        response = _resp(200, {"choices": [{"message": {"content": ""}}]})
        with patch("app.health.httpx.AsyncClient", return_value=_Client(response)):
            strict = await probe_model("m", "https://proxy.example/v1/x", "key", "m")
            relaxed = await probe_model("m", "https://proxy.example/v1/x", "key", "m", relaxed=True)
        self.assertFalse(strict.ok)
        self.assertTrue(relaxed.ok)

    async def test_matrix_skips_image_models_and_reports_each_model(self):
        models = [("openai", "gpt-a"), ("openai", "gpt-image-2"), ("anthropic", "claude-b")]

        async def fake_probe(name, url, token, model, *, relaxed=False):
            return Check(name, model != "claude-b", "ok" if model != "claude-b" else "HTTP 503")

        with patch("app.health.fetch_models", return_value=models), \
             patch("app.health.probe_model", side_effect=fake_probe):
            checks, error = await probe_source_models("https://p.example/v1/x", "key")
        self.assertIsNone(error)
        self.assertEqual([c.name for c in checks], ["claude-b", "gpt-a"])
        text = format_models_report([("proxy", checks, None)])
        self.assertIn("1/2 отвечают", text)
        self.assertIn("❌ <code>claude-b</code>", text)

    def test_image_models_are_not_chat_probed(self):
        self.assertFalse(_is_chat_model("gpt-image-2.5"))
        self.assertTrue(_is_chat_model("claude-sonnet-5-5"))

    def test_unconfigured_source_is_reported_not_raised(self):
        text = format_models_report([("proxy", [], "не настроен")])
        self.assertIn("не настроен", text)


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

    def test_zsp_public_tracked_independently(self):
        history = {}
        _update_check_state(history, "zsp_public", Check("zsp", False, "HTTP 502"))
        _update_check_state(history, "codex", Check("codex", True, "ok"))
        self.assertEqual(history["zsp_public"]["fails"], 1)
        self.assertEqual(history["codex"]["fails"], 0)


if __name__ == "__main__":
    unittest.main()
