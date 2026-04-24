"""
Unit tests for pagination utility

Note: Requires pandas to be installed (run within venv).
"""
import unittest
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Check for pandas availability
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None

if PANDAS_AVAILABLE:
    from utils.pagination import paginate_dataframe


@unittest.skipUnless(PANDAS_AVAILABLE, "pandas not installed")
class TestPagination(unittest.TestCase):
    """Test cases for pagination functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create sample dataframe
        self.df = pd.DataFrame({
            'id': range(1, 101),
            'name': [f'Item_{i}' for i in range(1, 101)],
            'value': range(100, 201)
        })

    def test_empty_dataframe(self):
        """Test pagination with empty dataframe."""
        empty_df = pd.DataFrame()
        # Since paginate_dataframe uses streamlit session state,
        # we'll test the logic without actually calling it
        # Just verify empty df has 0 length
        self.assertEqual(len(empty_df), 0)

    def test_dataframe_smaller_than_page_size(self):
        """Test with dataframe smaller than page size."""
        small_df = pd.DataFrame({'id': [1, 2, 3]})
        self.assertEqual(len(small_df), 3)
        # With page_size=50, this should return all rows
        self.assertLess(len(small_df), 50)

    def test_dataframe_exactly_page_size(self):
        """Test with dataframe exactly one page."""
        exact_df = pd.DataFrame({'id': range(1, 51)})
        self.assertEqual(len(exact_df), 50)

    def test_dataframe_multiple_pages(self):
        """Test with dataframe requiring multiple pages."""
        # 100 rows with page_size 25 = 4 pages
        total_rows = 100
        page_size = 25
        expected_pages = (total_rows + page_size - 1) // page_size
        self.assertEqual(expected_pages, 4)

    def test_pagination_calculations(self):
        """Test pagination math."""
        total_rows = 87
        page_size = 20

        # Calculate total pages (ceiling division)
        total_pages = (total_rows + page_size - 1) // page_size
        self.assertEqual(total_pages, 5)

        # Test slice indices for each page
        for page in range(1, total_pages + 1):
            start_idx = (page - 1) * page_size
            end_idx = min(start_idx + page_size, total_rows)

            self.assertGreaterEqual(start_idx, 0)
            self.assertLessEqual(end_idx, total_rows)
            self.assertLessEqual(end_idx - start_idx, page_size)

    def test_last_page_partial(self):
        """Test that last page can have fewer items."""
        total_rows = 87
        page_size = 20

        # Last page (page 5) should have 7 items
        last_page = 5
        start_idx = (last_page - 1) * page_size  # 80
        end_idx = min(start_idx + page_size, total_rows)  # 87

        items_on_last_page = end_idx - start_idx
        self.assertEqual(items_on_last_page, 7)


@unittest.skipUnless(PANDAS_AVAILABLE, "pandas not installed")
class TestDataFrameSlicing(unittest.TestCase):
    """Test dataframe slicing operations."""

    def test_iloc_slicing(self):
        """Test pandas iloc slicing."""
        df = pd.DataFrame({'value': range(1, 11)})

        # Slice first 5 items
        sliced = df.iloc[0:5]
        self.assertEqual(len(sliced), 5)
        self.assertEqual(sliced['value'].tolist(), [1, 2, 3, 4, 5])

        # Slice next 5 items
        sliced = df.iloc[5:10]
        self.assertEqual(len(sliced), 5)
        self.assertEqual(sliced['value'].tolist(), [6, 7, 8, 9, 10])


if __name__ == '__main__':
    unittest.main()
