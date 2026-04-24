#!/usr/bin/env python3
"""
Run all unit tests for Wasteless UI

Usage:
    python run_tests.py           # Run all tests
    python run_tests.py -v        # Verbose output

Note: For full test coverage, run within the virtual environment:
    source venv/bin/activate
    python run_tests.py
"""
import unittest
import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))


def check_dependencies():
    """Check if required test dependencies are available."""
    missing = []

    try:
        import yaml
    except ImportError:
        missing.append('pyyaml')

    try:
        import pandas
    except ImportError:
        missing.append('pandas')

    try:
        import streamlit
    except ImportError:
        missing.append('streamlit')

    try:
        import psycopg2
    except ImportError:
        missing.append('psycopg2-binary')

    if missing:
        print("=" * 60)
        print("WARNING: Some test dependencies are missing")
        print("=" * 60)
        print(f"Missing packages: {', '.join(missing)}")
        print()
        print("Some tests will be skipped. For full coverage, run:")
        print("  source venv/bin/activate")
        print("  pip install -r requirements.txt")
        print("  python run_tests.py")
        print("=" * 60)
        print()


# Discover and run tests
if __name__ == '__main__':
    check_dependencies()

    # Create test loader
    loader = unittest.TestLoader()

    # Discover all tests in the tests directory
    start_dir = 'tests'
    suite = loader.discover(start_dir, pattern='test_*.py')

    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print()
    print("=" * 60)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print("=" * 60)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)
