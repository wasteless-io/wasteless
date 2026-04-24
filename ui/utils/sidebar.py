"""
Sidebar utilities for consistent navigation across pages
"""
import streamlit as st
import os


def apply_sidebar_styles():
    """
    Apply CSS to style the sidebar navigation, including the Wasteless button.
    """
    st.markdown("""
    <style>
    /* Replace "app" text with "WASTELESS" styled button */
    /* Hide all text inside the first nav item */
    [data-testid="stSidebarNav"] li:first-child * {
        font-size: 0 !important;
        color: transparent !important;
    }
    [data-testid="stSidebarNav"] li:first-child a {
        font-size: 0 !important;
        display: flex !important;
        align-items: center !important;
        background-color: #6B8E4E !important;
        padding: 0.5rem 1rem !important;
        border-radius: 0.5rem !important;
        justify-content: center !important;
    }
    [data-testid="stSidebarNav"] li:first-child a::before {
        content: "WASTELESS";
        font-size: 1.1rem !important;
        font-weight: 600;
        color: #2d2d2d !important;
        letter-spacing: -0.5px;
    }
    [data-testid="stSidebarNav"] li:first-child {
        margin-bottom: 0.5rem !important;
    }
    </style>
    """, unsafe_allow_html=True)


def setup_sidebar():
    """
    Setup consistent sidebar with database connection.

    Best practices:
    - Clean navigation without logo
    - Streamlit handles navigation automatically (no manual links needed)
    - Database status at bottom
    """
    # Apply Wasteless button styling
    apply_sidebar_styles()

    # Connection status
    from utils.database import get_db_connection
    conn = get_db_connection()
    if conn:
        st.sidebar.success("✅ Database Connected")
        return conn
    else:
        st.sidebar.error("❌ Database Disconnected")
        return None
