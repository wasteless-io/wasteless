"""
Unit tests for src/core/llm.py — AI insights with silent degradation.
litellm and the LLM provider are always mocked.
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core import llm
from core.llm import (
    build_prompt,
    enrich_recommendations,
    generate_insight,
    is_enabled,
    MODEL_ENV_VAR,
)


class TestIsEnabled:

    def test_disabled_without_model_env(self, monkeypatch):
        monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
        assert is_enabled() is False

    def test_disabled_when_litellm_missing(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        with patch.dict(sys.modules, {'litellm': None}):
            assert is_enabled() is False

    def test_enabled_with_model_and_litellm(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        with patch.dict(sys.modules, {'litellm': MagicMock()}):
            assert is_enabled() is True


class TestBuildPrompt:

    def test_contains_all_context(self):
        prompt = build_prompt(
            'DELETE orphaned EBS volume vol-1', 'ebs_volume',
            0.59, 0.95, {'size_gb': 8, 'region': 'eu-west-3'})
        assert 'vol-1' in prompt
        assert 'ebs_volume' in prompt
        assert '0.59' in prompt
        assert '"size_gb": 8' in prompt
        assert 'Never invent numbers' in prompt


class TestGenerateInsight:

    def _mock_litellm(self, content='This volume is unattached.'):
        mock = MagicMock()
        mock.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=content))])
        return mock

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
        assert generate_insight('a', 'ebs_volume', 1, 0.9, {}) is None

    def test_returns_stripped_content(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        mock = self._mock_litellm('  insight text  ')
        with patch.dict(sys.modules, {'litellm': mock}):
            assert generate_insight('a', 'ebs_volume', 1, 0.9, {}) == 'insight text'

    def test_provider_error_returns_none(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        mock = MagicMock()
        mock.completion.side_effect = RuntimeError('rate limited')
        with patch.dict(sys.modules, {'litellm': mock}):
            assert generate_insight('a', 'ebs_volume', 1, 0.9, {}) is None

    def test_empty_content_returns_none(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        with patch.dict(sys.modules, {'litellm': self._mock_litellm('')}):
            assert generate_insight('a', 'ebs_volume', 1, 0.9, {}) is None


class TestEnrichRecommendations:

    def test_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
        conn = MagicMock()
        assert enrich_recommendations(conn) == 0
        conn.cursor.assert_not_called()

    def test_updates_rows_and_commits_each(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            (1, 'DELETE vol-1', 0.59, 'ebs_volume', 0.95, {'size_gb': 8}),
            (2, 'RELEASE eip-1', 3.36, 'elastic_ip', 0.99, '{"region": "eu-west-3"}'),
        ]
        with patch.dict(sys.modules, {'litellm': MagicMock()}), \
             patch.object(llm, 'generate_insight', return_value='why'):
            assert enrich_recommendations(conn) == 2
        updates = [c for c in cursor.execute.call_args_list
                   if 'UPDATE recommendations' in c[0][0]]
        assert len(updates) == 2
        assert conn.commit.call_count == 2

    def test_failed_generation_skips_update(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            (1, 'DELETE vol-1', 0.59, 'ebs_volume', 0.95, {}),
        ]
        with patch.dict(sys.modules, {'litellm': MagicMock()}), \
             patch.object(llm, 'generate_insight', return_value=None):
            assert enrich_recommendations(conn) == 0
        updates = [c for c in cursor.execute.call_args_list
                   if 'UPDATE recommendations' in c[0][0]]
        assert updates == []

    def test_db_error_returns_zero(self, monkeypatch):
        monkeypatch.setenv(MODEL_ENV_VAR, 'gpt-4o-mini')
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = RuntimeError('db gone')
        with patch.dict(sys.modules, {'litellm': MagicMock()}):
            assert enrich_recommendations(conn) == 0
        conn.rollback.assert_called_once()
