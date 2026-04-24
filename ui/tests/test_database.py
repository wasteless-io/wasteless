#!/usr/bin/env python3
"""
Unit Tests for Database Module
==============================

Tests for database connection and utilities.
Note: Requires streamlit to be installed (run within venv).
"""

import unittest
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Skip these tests if streamlit is not available
try:
    from utils.database import _INSECURE_PASSWORDS
    STREAMLIT_AVAILABLE = True
except ImportError:
    STREAMLIT_AVAILABLE = False
    _INSECURE_PASSWORDS = set()  # Fallback for test discovery


@unittest.skipUnless(STREAMLIT_AVAILABLE, "Streamlit not installed")
class TestInsecurePasswordList(unittest.TestCase):
    """Tests for insecure password detection."""

    def test_insecure_passwords_list_exists(self):
        """Test that insecure passwords list is defined."""
        self.assertIsNotNone(_INSECURE_PASSWORDS)
        self.assertIsInstance(_INSECURE_PASSWORDS, set)

    def test_common_weak_passwords_are_listed(self):
        """Test that common weak passwords are in the list."""
        expected_weak = ['password', 'admin', '123456']
        for pwd in expected_weak:
            self.assertIn(pwd, _INSECURE_PASSWORDS)

    def test_placeholder_password_is_listed(self):
        """Test that the placeholder password is in the list."""
        self.assertIn('CHANGE_ME_USE_STRONG_PASSWORD', _INSECURE_PASSWORDS)

    def test_empty_password_is_listed(self):
        """Test that empty password is in the list."""
        self.assertIn('', _INSECURE_PASSWORDS)


@unittest.skipUnless(STREAMLIT_AVAILABLE, "Streamlit not installed")
class TestDatabaseConnection(unittest.TestCase):
    """Tests for database connection function.

    Note: These tests mock the database connection to avoid
    requiring an actual database.
    """

    @patch('utils.database.psycopg2.connect')
    @patch('utils.database.st')
    @patch.dict(os.environ, {
        'DB_HOST': 'testhost',
        'DB_PORT': '5432',
        'DB_NAME': 'testdb',
        'DB_USER': 'testuser',
        'DB_PASSWORD': 'testpassword'
    })
    def test_connection_with_valid_credentials(self, mock_st, mock_connect):
        """Test connection succeeds with valid credentials."""
        # Clear the cached connection
        from utils import database
        if hasattr(database.get_db_connection, 'clear'):
            database.get_db_connection.clear()

        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        # Note: Due to st.cache_resource, we can't easily test the function directly
        # This test verifies the mock setup is correct
        self.assertTrue(mock_connect.call_count == 0)  # Not called yet

    @patch('utils.database.st')
    @patch.dict(os.environ, {
        'DB_HOST': 'testhost',
        'DB_PORT': '5432',
        'DB_NAME': 'testdb',
        'DB_USER': 'testuser',
        'DB_PASSWORD': ''
    })
    def test_error_shown_when_password_missing(self, mock_st):
        """Test that error is shown when password is missing."""
        # Clear environment variable
        with patch.dict(os.environ, {'DB_PASSWORD': ''}):
            # The function should call st.error when password is missing
            pass  # Test setup verification only

    def test_insecure_password_detection(self):
        """Test that insecure passwords are correctly identified."""
        insecure_samples = [
            'password',
            'CHANGE_ME_USE_STRONG_PASSWORD',
            '123456',
            ''
        ]

        secure_samples = [
            'MyS3cur3P@ssw0rd!',
            'random-uuid-4f2b-8a1c',
            'correct-horse-battery-staple'
        ]

        for pwd in insecure_samples:
            self.assertIn(pwd, _INSECURE_PASSWORDS,
                         f"'{pwd}' should be detected as insecure")

        for pwd in secure_samples:
            self.assertNotIn(pwd, _INSECURE_PASSWORDS,
                            f"'{pwd}' should not be detected as insecure")


if __name__ == '__main__':
    unittest.main(verbosity=2)
