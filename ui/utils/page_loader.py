"""
Page Loading Indicator for Wasteless UI
========================================

Provides loading state detection and user feedback during page rendering.
"""
import streamlit as st
import time
from contextlib import contextmanager


@contextmanager
def page_loading_state(page_name="Page"):
    """
    Context manager to track page loading state.

    Usage:
        with page_loading_state("Dashboard"):
            # Your page code here
            load_data()
            render_charts()

    Args:
        page_name: Name of the page being loaded
    """
    # Create a placeholder for loading indicator
    loading_placeholder = st.empty()

    # Show loading message
    loading_placeholder.markdown(f"""
    <div style="text-align: center; padding: 100px 20px;">
        <div style="display: inline-block;">
            <div style="border: 4px solid #f3f3f3; border-top: 4px solid #667eea;
                        border-radius: 50%; width: 40px; height: 40px;
                        animation: spin 1s linear infinite; margin: 0 auto;"></div>
            <p style="color: #667eea; margin-top: 20px; font-size: 1.1rem;">
                Loading {page_name}...
            </p>
        </div>
    </div>
    <style>
    @keyframes spin {{
        0% {{ transform: rotate(0deg); }}
        100% {{ transform: rotate(360deg); }}
    }}
    </style>
    """, unsafe_allow_html=True)

    try:
        # Execute the page content
        yield
    finally:
        # Clear the loading indicator
        loading_placeholder.empty()


def show_loading_spinner(message="Loading..."):
    """
    Display a simple loading spinner.

    Args:
        message: Loading message to display

    Returns:
        Placeholder that can be cleared when loading is done
    """
    placeholder = st.empty()

    with placeholder.container():
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown(f"""
            <div style="text-align: center; padding: 40px 20px;">
                <div style="border: 3px solid #f3f3f3; border-top: 3px solid #667eea;
                            border-radius: 50%; width: 30px; height: 30px;
                            animation: spin 0.8s linear infinite; margin: 0 auto;"></div>
                <p style="color: #667eea; margin-top: 15px; font-size: 0.95rem;">
                    {message}
                </p>
            </div>
            <style>
            @keyframes spin {{
                0% {{ transform: rotate(0deg); }}
                100% {{ transform: rotate(360deg); }}
            }}
            </style>
            """, unsafe_allow_html=True)

    return placeholder


def track_loading_progress(steps):
    """
    Track and display loading progress through multiple steps.

    Args:
        steps: List of step names

    Returns:
        Function to update progress

    Example:
        update_progress = track_loading_progress([
            "Loading data",
            "Processing results",
            "Rendering charts"
        ])

        update_progress(0)  # "Loading data"
        # ... do work ...
        update_progress(1)  # "Processing results"
    """
    progress_bar = st.progress(0)
    status_text = st.empty()

    total_steps = len(steps)

    def update(step_index):
        if step_index < total_steps:
            progress = (step_index + 1) / total_steps
            progress_bar.progress(progress)
            status_text.text(f"⏳ {steps[step_index]}...")
        else:
            progress_bar.progress(1.0)
            status_text.text("✅ Complete!")
            time.sleep(0.5)
            progress_bar.empty()
            status_text.empty()

    return update


@contextmanager
def loading_section(section_name):
    """
    Show loading state for a specific section of the page.

    Usage:
        with loading_section("Charts"):
            st.plotly_chart(fig)

    Args:
        section_name: Name of the section being loaded
    """
    placeholder = st.empty()

    with placeholder.container():
        st.info(f"⏳ Loading {section_name}...")

    try:
        yield
        placeholder.empty()
    except Exception as e:
        placeholder.error(f"❌ Error loading {section_name}: {e}")
        raise


def check_page_ready(required_data):
    """
    Check if all required data is loaded and page is ready.

    Args:
        required_data: Dict of {name: data} to check

    Returns:
        bool: True if all data is loaded

    Example:
        if not check_page_ready({
            "database": conn,
            "recommendations": recs_df
        }):
            st.stop()
    """
    missing = []

    for name, data in required_data.items():
        if data is None:
            missing.append(name)
        elif hasattr(data, 'empty') and data.empty:
            missing.append(name)

    if missing:
        st.warning(f"⚠️ Page not fully loaded. Missing: {', '.join(missing)}")
        return False

    return True


def add_page_load_time():
    """
    Track and display page load time.
    Should be called at the start and end of page rendering.

    Usage:
        # At start of page
        if 'page_load_start' not in st.session_state:
            st.session_state.page_load_start = time.time()

        # At end of page
        add_page_load_time()
    """
    if 'page_load_start' in st.session_state:
        load_time = time.time() - st.session_state.page_load_start

        # Display in sidebar footer
        st.sidebar.markdown("---")
        st.sidebar.caption(f"⏱️ Page loaded in {load_time:.2f}s")

        # Clear the timer
        del st.session_state.page_load_start


# Example usage for a complete page
def example_page_with_loading():
    """
    Example of a page using loading indicators.
    """
    # Track load time
    if 'page_load_start' not in st.session_state:
        st.session_state.page_load_start = time.time()

    # Main page loading
    with page_loading_state("Example Page"):
        st.title("Example Page")

        # Check prerequisites
        if not check_page_ready({"database": True}):
            st.stop()

        # Load data with progress
        update_progress = track_loading_progress([
            "Fetching data",
            "Processing",
            "Rendering"
        ])

        update_progress(0)
        time.sleep(1)  # Simulate data fetch

        update_progress(1)
        time.sleep(1)  # Simulate processing

        update_progress(2)
        st.success("Data loaded!")

        update_progress(3)

    # Show load time
    add_page_load_time()
