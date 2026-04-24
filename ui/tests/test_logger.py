"""
Unit tests for logging utility
"""
import unittest
import logging
import sys
from pathlib import Path
from io import StringIO

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.logger import (
    setup_logging,
    get_logger,
    log_user_action,
    log_db_query,
    log_config_change
)


class TestLoggingSetup(unittest.TestCase):
    """Test cases for logging setup."""

    def test_setup_logging_creates_logger(self):
        """Test that setup_logging creates a valid logger."""
        logger = setup_logging(log_level=logging.DEBUG)
        self.assertIsNotNone(logger)
        self.assertEqual(logger.name, 'wasteless_ui')
        self.assertEqual(logger.level, logging.DEBUG)

    def test_get_logger_with_name(self):
        """Test get_logger with custom name."""
        logger = get_logger('test_module')
        self.assertEqual(logger.name, 'wasteless_ui.test_module')

    def test_get_logger_default(self):
        """Test get_logger without name."""
        logger = get_logger()
        self.assertEqual(logger.name, 'wasteless_ui')

    def test_logger_has_handlers(self):
        """Test that logger has at least one handler."""
        logger = get_logger()
        self.assertGreater(len(logger.handlers), 0)


class TestLoggingFunctions(unittest.TestCase):
    """Test cases for logging convenience functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Capture log output
        self.log_capture = StringIO()
        handler = logging.StreamHandler(self.log_capture)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(levelname)s - %(message)s')
        handler.setFormatter(formatter)

        # Get logger and add handler
        self.logger = get_logger('test')
        self.logger.handlers = []
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

    def test_log_user_action(self):
        """Test log_user_action function."""
        # This function uses its own logger, so we just test it doesn't crash
        try:
            log_user_action('test_action', {'key': 'value'}, user='test_user')
            success = True
        except Exception:
            success = False
        self.assertTrue(success)

    def test_log_db_query_success(self):
        """Test log_db_query with success."""
        try:
            log_db_query('test_query', 123.45, success=True)
            success = True
        except Exception:
            success = False
        self.assertTrue(success)

    def test_log_db_query_failure(self):
        """Test log_db_query with failure."""
        try:
            log_db_query('test_query', 50.0, success=False)
            success = True
        except Exception:
            success = False
        self.assertTrue(success)

    def test_log_config_change(self):
        """Test log_config_change function."""
        try:
            log_config_change('test_setting', 'old_value', 'new_value', user='test_user')
            success = True
        except Exception:
            success = False
        self.assertTrue(success)


class TestLogLevels(unittest.TestCase):
    """Test logging levels."""

    def test_log_levels_hierarchy(self):
        """Test that log levels follow correct hierarchy."""
        self.assertLess(logging.DEBUG, logging.INFO)
        self.assertLess(logging.INFO, logging.WARNING)
        self.assertLess(logging.WARNING, logging.ERROR)
        self.assertLess(logging.ERROR, logging.CRITICAL)

    def test_logger_respects_level(self):
        """Test that logger respects set level."""
        logger = setup_logging(log_level=logging.WARNING)
        self.assertEqual(logger.level, logging.WARNING)


if __name__ == '__main__':
    unittest.main()
