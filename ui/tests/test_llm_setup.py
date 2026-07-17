#!/usr/bin/env python3
"""
Tests for the Settings AI insights endpoints (routes/settings.py):
/api/llm/test and /api/llm/save.

Same contract as the /setup AWS endpoints: the test endpoint never writes
anything, the save endpoint persists to BOTH env files only after a
successful connection test, and provider failures surface as a 400 with a
user-safe message, never a 500. core.llm.test_connection is mocked
throughout — no test here talks to an LLM provider.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR))

from schemas import LlmSetupRequest
from routes.settings import api_llm_save, api_llm_test

import core.llm as core_llm
import utils.env_files as env_files


def _payload(model="anthropic/claude-haiku-4-5-20251001", api_key="sk-test"):
    return LlmSetupRequest(model=model, api_key=api_key)


class TestLlmTestEndpoint(unittest.TestCase):

    def test_success(self):
        with patch.object(core_llm, "check_connection", return_value=None) as tc:
            result = api_llm_test(_payload())
        self.assertEqual(result, {"success": True, "model": "anthropic/claude-haiku-4-5-20251001"})
        tc.assert_called_once_with("anthropic/claude-haiku-4-5-20251001", "sk-test")

    def test_empty_key_is_passed_as_none(self):
        """Empty key means 'use the key already in the environment'."""
        with patch.object(core_llm, "check_connection", return_value=None) as tc:
            api_llm_test(_payload(api_key=""))
        self.assertIsNone(tc.call_args[0][1])

    def test_failure_is_400_with_user_safe_message(self):
        with patch.object(core_llm, "check_connection", return_value="authentication failed"):
            response = api_llm_test(_payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"authentication failed", response.body)

    def test_never_writes_anything(self):
        with (
            patch.object(core_llm, "check_connection", return_value=None),
            patch.object(env_files, "write_env_files") as write,
        ):
            api_llm_test(_payload())
        write.assert_not_called()


class TestLlmSaveEndpoint(unittest.TestCase):

    def _save(self, payload, test_result=None):
        """Run api_llm_save against a temp pair of env files; returns
        (response, root_env_text, ui_env_text)."""
        with tempfile.TemporaryDirectory() as tmp:
            root_env = Path(tmp) / ".env"
            ui_env = Path(tmp) / "ui.env"
            with (
                patch.object(core_llm, "check_connection", return_value=test_result),
                patch.object(env_files, "ENV_FILES", [root_env, ui_env]),
            ):
                response = api_llm_save(payload)
            texts = [p.read_text() if p.exists() else "" for p in (root_env, ui_env)]
        return response, texts[0], texts[1]

    def test_saves_model_and_key_to_both_files(self):
        result, root_text, ui_text = self._save(_payload())
        self.assertEqual(result["success"], True)
        self.assertTrue(result["key_saved"])
        for text in (root_text, ui_text):
            self.assertIn("WASTELESS_LLM_MODEL=anthropic/claude-haiku-4-5-20251001", text)
            self.assertIn("ANTHROPIC_API_KEY=sk-test", text)

    def test_failed_test_persists_nothing(self):
        response, root_text, ui_text = self._save(_payload(), test_result="invalid key")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(root_text, "")
        self.assertEqual(ui_text, "")

    def test_keyless_model_saves_model_only(self):
        result, root_text, _ = self._save(_payload(model="ollama/llama3.1", api_key=""))
        self.assertEqual(result["success"], True)
        self.assertFalse(result["key_saved"])
        self.assertIn("WASTELESS_LLM_MODEL=ollama/llama3.1", root_text)
        self.assertNotIn("API_KEY", root_text)

    def test_key_for_unknown_provider_is_rejected(self):
        """We wouldn't know which env var to write the key into — refuse
        instead of guessing, without calling the provider at all."""
        with patch.object(core_llm, "check_connection") as tc:
            response = api_llm_save(_payload(model="somefuture/model"))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"env var", response.body)
        tc.assert_not_called()

    def test_applies_to_process_env(self):
        with patch.object(env_files, "os") as fake_os:
            fake_os.environ = {}
            result, _, _ = self._save(_payload())
        self.assertEqual(result["success"], True)
        self.assertEqual(
            fake_os.environ.get("WASTELESS_LLM_MODEL"), "anthropic/claude-haiku-4-5-20251001"
        )
        self.assertEqual(fake_os.environ.get("ANTHROPIC_API_KEY"), "sk-test")


if __name__ == "__main__":
    unittest.main()
