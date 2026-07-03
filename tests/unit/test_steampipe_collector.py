"""
Unit tests for the Steampipe collector wrapper and the Steampipe-backed
EBS orphan detector (row mapping). Steampipe itself is mocked — no binary
or AWS access required.
"""

import json
import subprocess
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from collectors.steampipe import (
    run_query,
    run_query_file,
    is_available,
    SteampipeError,
    SteampipeNotInstalledError,
    QUERIES_DIR,
)
from detectors.ebs_orphan_steampipe import rows_to_volumes


def _completed(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess(
        args=['steampipe'], returncode=returncode, stdout=stdout, stderr=stderr
    )


SAMPLE_ROW = {
    'volume_id': 'vol-0abc123',
    'name': 'old-data',
    'size_gb': 100,
    'vol_type': 'gp3',
    'az': 'eu-west-1a',
    'region': 'eu-west-1',
    'encrypted': True,
    'age_days': 45,
}


class TestRunQuery:
    """Tests for the run_query wrapper."""

    @patch('collectors.steampipe.is_available', return_value=False)
    def test_not_installed_raises(self, _):
        with pytest.raises(SteampipeNotInstalledError):
            run_query('select 1')

    @patch('collectors.steampipe.is_available', return_value=True)
    @patch('collectors.steampipe.subprocess.run')
    def test_parses_wrapped_rows_format(self, mock_run, _):
        """Steampipe >= 0.21 wraps rows in {"rows": [...]}."""
        mock_run.return_value = _completed(json.dumps({'rows': [SAMPLE_ROW]}))
        rows = run_query('select 1')
        assert rows == [SAMPLE_ROW]

    @patch('collectors.steampipe.is_available', return_value=True)
    @patch('collectors.steampipe.subprocess.run')
    def test_parses_bare_array_format(self, mock_run, _):
        """Older Steampipe versions return a bare JSON array."""
        mock_run.return_value = _completed(json.dumps([SAMPLE_ROW]))
        rows = run_query('select 1')
        assert rows == [SAMPLE_ROW]

    @patch('collectors.steampipe.is_available', return_value=True)
    @patch('collectors.steampipe.subprocess.run')
    def test_empty_rows(self, mock_run, _):
        mock_run.return_value = _completed(json.dumps({'rows': []}))
        assert run_query('select 1') == []

    @patch('collectors.steampipe.is_available', return_value=True)
    @patch('collectors.steampipe.subprocess.run')
    def test_nonzero_exit_raises(self, mock_run, _):
        mock_run.return_value = _completed(returncode=1, stderr='boom')
        with pytest.raises(SteampipeError) as exc_info:
            run_query('select 1')
        assert 'boom' in str(exc_info.value)

    @patch('collectors.steampipe.is_available', return_value=True)
    @patch('collectors.steampipe.subprocess.run')
    def test_invalid_json_raises(self, mock_run, _):
        mock_run.return_value = _completed('not json at all')
        with pytest.raises(SteampipeError) as exc_info:
            run_query('select 1')
        assert 'JSON' in str(exc_info.value)

    @patch('collectors.steampipe.is_available', return_value=True)
    @patch('collectors.steampipe.subprocess.run')
    def test_timeout_raises(self, mock_run, _):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='steampipe', timeout=5)
        with pytest.raises(SteampipeError) as exc_info:
            run_query('select 1', timeout=5)
        assert 'timed out' in str(exc_info.value)

    @patch('collectors.steampipe.is_available', return_value=True)
    @patch('collectors.steampipe.subprocess.run')
    def test_unexpected_shape_raises(self, mock_run, _):
        mock_run.return_value = _completed(json.dumps({'rows': 'oops'}))
        with pytest.raises(SteampipeError):
            run_query('select 1')


class TestRunQueryFile:
    """Tests for named query files."""

    def test_missing_file_raises(self):
        with pytest.raises(SteampipeError) as exc_info:
            run_query_file('does_not_exist')
        assert 'not found' in str(exc_info.value)

    def test_ebs_orphan_query_file_exists(self):
        assert (QUERIES_DIR / 'ebs_orphan.sql').is_file()

    @patch('collectors.steampipe.run_query')
    def test_passes_sql_content(self, mock_run_query):
        mock_run_query.return_value = []
        run_query_file('ebs_orphan')
        sql = mock_run_query.call_args[0][0]
        assert 'aws_ebs_volume' in sql
        assert "state = 'available'" in sql


class TestRowsToVolumes:
    """Tests for mapping Steampipe rows to detector volume dicts."""

    def test_full_row(self):
        volumes = rows_to_volumes([SAMPLE_ROW])
        assert len(volumes) == 1
        vol = volumes[0]
        assert vol['volume_id'] == 'vol-0abc123'
        assert vol['name'] == 'old-data'
        assert vol['size_gb'] == 100
        assert vol['vol_type'] == 'gp3'
        assert vol['region'] == 'eu-west-1'
        assert vol['encrypted'] is True
        assert vol['age_days'] == 45
        # 100 GiB gp3 at 0.0736 EUR/GiB/month
        assert vol['monthly_cost'] == pytest.approx(7.36)

    def test_missing_optional_fields(self):
        volumes = rows_to_volumes([{'volume_id': 'vol-1', 'size_gb': None,
                                    'vol_type': None, 'name': None}])
        vol = volumes[0]
        assert vol['size_gb'] == 0
        assert vol['vol_type'] == 'gp2'
        assert vol['name'] == ''
        assert vol['monthly_cost'] == 0.0
        assert vol['age_days'] is None

    def test_empty_input(self):
        assert rows_to_volumes([]) == []
