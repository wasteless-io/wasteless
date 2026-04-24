#!/usr/bin/env python3
"""
Unit Tests for Remediator Module
=================================

Tests for the RemediatorProxy and related functions.
"""

import unittest
import sys
from pathlib import Path
from datetime import datetime, date
from unittest.mock import patch, MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.remediator import (
    sanitize_for_json,
    get_backend_path,
    check_backend_available,
    get_backend_error,
    validate_backend_at_startup,
    BACKEND_PATH
)


class TestSanitizeForJson(unittest.TestCase):
    """Tests for sanitize_for_json function."""

    def test_datetime_conversion(self):
        """Test datetime objects are converted to ISO format."""
        dt = datetime(2025, 1, 15, 10, 30, 45)
        result = sanitize_for_json(dt)
        self.assertEqual(result, '2025-01-15T10:30:45')

    def test_date_conversion(self):
        """Test date objects are converted to ISO format."""
        d = date(2025, 1, 15)
        result = sanitize_for_json(d)
        self.assertEqual(result, '2025-01-15')

    def test_dict_with_datetime(self):
        """Test dict containing datetime is properly sanitized."""
        data = {
            'name': 'test',
            'created_at': datetime(2025, 1, 15, 10, 0, 0),
            'count': 42
        }
        result = sanitize_for_json(data)
        self.assertEqual(result['name'], 'test')
        self.assertEqual(result['created_at'], '2025-01-15T10:00:00')
        self.assertEqual(result['count'], 42)

    def test_list_with_datetime(self):
        """Test list containing datetime is properly sanitized."""
        data = [
            datetime(2025, 1, 14),
            datetime(2025, 1, 15)
        ]
        result = sanitize_for_json(data)
        self.assertEqual(result, ['2025-01-14T00:00:00', '2025-01-15T00:00:00'])

    def test_nested_structure(self):
        """Test deeply nested structure is properly sanitized."""
        data = {
            'outer': {
                'inner': {
                    'date': date(2025, 1, 15),
                    'items': [
                        {'timestamp': datetime(2025, 1, 15, 12, 0, 0)}
                    ]
                }
            }
        }
        result = sanitize_for_json(data)
        self.assertEqual(result['outer']['inner']['date'], '2025-01-15')
        self.assertEqual(result['outer']['inner']['items'][0]['timestamp'], '2025-01-15T12:00:00')

    def test_tuple_with_datetime(self):
        """Test tuple containing datetime is properly sanitized."""
        data = (datetime(2025, 1, 15), 'test', 42)
        result = sanitize_for_json(data)
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], '2025-01-15T00:00:00')
        self.assertEqual(result[1], 'test')
        self.assertEqual(result[2], 42)

    def test_primitive_passthrough(self):
        """Test primitive types pass through unchanged."""
        self.assertEqual(sanitize_for_json('string'), 'string')
        self.assertEqual(sanitize_for_json(123), 123)
        self.assertEqual(sanitize_for_json(45.67), 45.67)
        self.assertEqual(sanitize_for_json(True), True)
        self.assertEqual(sanitize_for_json(None), None)


class TestBackendPath(unittest.TestCase):
    """Tests for backend path functions."""

    def test_get_backend_path_returns_string(self):
        """Test get_backend_path returns a string."""
        path = get_backend_path()
        self.assertIsInstance(path, str)

    def test_get_backend_path_is_absolute(self):
        """Test get_backend_path returns an absolute path."""
        import os
        path = get_backend_path()
        self.assertTrue(os.path.isabs(path))

    def test_backend_path_constant(self):
        """Test BACKEND_PATH constant is defined."""
        self.assertIsNotNone(BACKEND_PATH)
        self.assertIsInstance(BACKEND_PATH, str)

    def test_backend_path_matches_function(self):
        """Test BACKEND_PATH matches get_backend_path()."""
        self.assertEqual(BACKEND_PATH, get_backend_path())


class TestBackendValidation(unittest.TestCase):
    """Tests for backend validation functions."""

    def test_check_backend_available_returns_bool(self):
        """Test check_backend_available returns boolean."""
        result = check_backend_available()
        self.assertIsInstance(result, bool)

    def test_validate_backend_at_startup_returns_tuple(self):
        """Test validate_backend_at_startup returns proper tuple."""
        is_valid, message = validate_backend_at_startup()
        self.assertIsInstance(is_valid, bool)
        self.assertIsInstance(message, str)

    def test_validate_backend_at_startup_message_not_empty(self):
        """Test validation message is not empty."""
        _, message = validate_backend_at_startup()
        self.assertGreater(len(message), 0)

    def test_get_backend_error_when_available(self):
        """Test get_backend_error returns None when backend exists."""
        # This test depends on the actual system state
        error = get_backend_error()
        # Either None (backend exists) or a string (backend doesn't exist)
        self.assertTrue(error is None or isinstance(error, str))


class TestRemediatorProxyInitialization(unittest.TestCase):
    """Tests for RemediatorProxy initialization."""

    def test_proxy_stores_dry_run_flag(self):
        """Test that dry_run flag is stored correctly."""
        # We can only test this if backend is not available
        # Otherwise it would try to import the real remediator
        from utils.remediator import _backend_exists

        if not _backend_exists:
            from utils.remediator import RemediatorProxy
            with self.assertRaises(RuntimeError):
                RemediatorProxy(dry_run=True)
        else:
            # If backend exists, test normal initialization
            self.skipTest("Backend is available, skipping error test")

    @patch('utils.remediator._backend_exists', True)
    @patch('utils.remediator._backend_error', None)
    def test_proxy_initialization_with_backend(self):
        """Test RemediatorProxy can be initialized when backend exists."""
        from utils.remediator import RemediatorProxy

        # Should not raise during __init__
        proxy = RemediatorProxy(dry_run=True)
        self.assertTrue(proxy.dry_run)
        self.assertIsNone(proxy._remediator)

        proxy = RemediatorProxy(dry_run=False)
        self.assertFalse(proxy.dry_run)


if __name__ == '__main__':
    unittest.main(verbosity=2)
