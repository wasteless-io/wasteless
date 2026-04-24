"""
Pagination utility for Streamlit dataframes
"""
import streamlit as st
import pandas as pd
from typing import Tuple


def paginate_dataframe(df: pd.DataFrame, page_size: int = 50, key_prefix: str = "pagination") -> Tuple[pd.DataFrame, dict]:
    """
    Add pagination controls to a dataframe and return the current page.

    Args:
        df: The dataframe to paginate
        page_size: Number of rows per page
        key_prefix: Unique prefix for session state keys

    Returns:
        Tuple of (paginated_df, pagination_info)
    """
    if df.empty:
        return df, {"total_rows": 0, "total_pages": 0, "current_page": 0}

    # Calculate pagination
    total_rows = len(df)
    total_pages = (total_rows + page_size - 1) // page_size  # Ceiling division

    # Initialize session state for this paginator
    page_key = f"{key_prefix}_current_page"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1

    current_page = st.session_state[page_key]

    # Ensure current page is valid
    if current_page > total_pages:
        current_page = total_pages
        st.session_state[page_key] = current_page
    elif current_page < 1:
        current_page = 1
        st.session_state[page_key] = current_page

    # Calculate slice indices
    start_idx = (current_page - 1) * page_size
    end_idx = min(start_idx + page_size, total_rows)

    # Create pagination controls
    col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])

    with col1:
        if st.button("⏮️ First", key=f"{key_prefix}_first", disabled=current_page == 1):
            st.session_state[page_key] = 1
            st.rerun()

    with col2:
        if st.button("◀️ Prev", key=f"{key_prefix}_prev", disabled=current_page == 1):
            st.session_state[page_key] = current_page - 1
            st.rerun()

    with col3:
        st.markdown(f"<div style='text-align: center; padding-top: 5px;'>Page {current_page} of {total_pages} ({start_idx + 1}-{end_idx} of {total_rows} rows)</div>", unsafe_allow_html=True)

    with col4:
        if st.button("Next ▶️", key=f"{key_prefix}_next", disabled=current_page == total_pages):
            st.session_state[page_key] = current_page + 1
            st.rerun()

    with col5:
        if st.button("Last ⏭️", key=f"{key_prefix}_last", disabled=current_page == total_pages):
            st.session_state[page_key] = total_pages
            st.rerun()

    # Return paginated dataframe and info
    paginated_df = df.iloc[start_idx:end_idx]

    pagination_info = {
        "total_rows": total_rows,
        "total_pages": total_pages,
        "current_page": current_page,
        "page_size": page_size,
        "start_idx": start_idx,
        "end_idx": end_idx
    }

    return paginated_df, pagination_info


def reset_pagination(key_prefix: str):
    """Reset pagination to first page."""
    page_key = f"{key_prefix}_current_page"
    if page_key in st.session_state:
        st.session_state[page_key] = 1
