"""
Page Transition Animation for Wasteless UI
===========================================

Claude-style loading animation with progressive word appearance.
"""
import streamlit as st
import time


def show_page_transition(page_name="page", phrases=None):
    """
    Display an elegant page transition with Claude-style word animation.

    Args:
        page_name: Name of the page being loaded
        phrases: Custom list of phrases (list of lists of words)
                 If None, uses default phrases based on page_name

    Example:
        show_page_transition("Dashboard", [
            ["Loading", "your", "dashboard"],
            ["Fetching", "cost", "data"],
            ["Almost", "ready"]
        ])
    """

    # Default phrases based on page type
    if phrases is None:
        phrases = get_default_phrases(page_name)

    # CSS for the animation
    st.markdown("""
    <style>
    .page-transition {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
        background: white;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 9999;
    }

    .transition-text {
        font-size: 1.5rem;
        color: #1f1f1f;
        font-weight: 300;
        text-align: center;
        margin: 20px;
        min-height: 50px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-wrap: wrap;
        max-width: 600px;
        line-height: 1.8;
    }

    .transition-word {
        display: inline-block;
        opacity: 0;
        animation: wordAppear 0.4s ease-out forwards;
        margin: 0 6px;
        color: #2d2d2d;
    }

    @keyframes wordAppear {
        from {
            opacity: 0;
            transform: translateY(8px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }

    .transition-subtitle {
        color: #666;
        font-size: 0.9rem;
        margin-top: 40px;
        opacity: 0.7;
        font-weight: 300;
    }
    </style>
    """, unsafe_allow_html=True)

    # Create placeholder for animation
    placeholder = st.empty()

    # Display each phrase with animated words
    for phrase in phrases:
        html_words = ""
        for i, word in enumerate(phrase):
            delay = i * 0.12  # Stagger each word slightly
            html_words += f'<span class="transition-word" style="animation-delay: {delay}s">{word}</span>'

        placeholder.markdown(f"""
        <div class="page-transition">
            <div class="transition-text">
                {html_words}
            </div>
            <div class="transition-subtitle">
                Wasteless
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Wait before showing next phrase
        time.sleep(0.8 + len(phrase) * 0.12)

    # Clear the transition screen
    placeholder.empty()


def get_default_phrases(page_name):
    """
    Get default loading phrases based on page type.

    Args:
        page_name: Name of the page

    Returns:
        List of phrases (list of lists of words)
    """

    # Common phrases
    common_end = [
        ["Preparing", "your", "view"],
        ["Almost", "ready"]
    ]

    # Page-specific phrases
    page_phrases = {
        "Dashboard": [
            ["Loading", "executive", "dashboard"],
            ["Fetching", "cost", "metrics"],
            ["Analyzing", "savings", "data"]
        ],
        "Recommendations": [
            ["Loading", "recommendations"],
            ["Analyzing", "idle", "resources"],
            ["Calculating", "potential", "savings"]
        ],
        "History": [
            ["Loading", "action", "history"],
            ["Retrieving", "audit", "trail"],
            ["Processing", "events"]
        ],
        "Settings": [
            ["Loading", "configuration"],
            ["Checking", "safeguards"],
            ["Retrieving", "settings"]
        ],
        "Home": [
            ["Welcome", "to", "Wasteless"],
            ["Loading", "overview"]
        ]
    }

    # Get page-specific or default phrases
    specific = page_phrases.get(page_name, [
        ["Loading", page_name.lower()],
        ["Preparing", "data"]
    ])

    return specific + common_end


def show_quick_transition(message="Loading"):
    """
    Show a quick, minimalist transition.

    Args:
        message: Single message to display
    """
    st.markdown(f"""
    <style>
    .quick-transition {{
        display: flex;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
        background: white;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 9999;
    }}

    .quick-text {{
        font-size: 1.3rem;
        color: #2d2d2d;
        font-weight: 300;
        animation: pulse 1.5s ease-in-out infinite;
    }}

    @keyframes pulse {{
        0%, 100% {{ opacity: 0.6; }}
        50% {{ opacity: 1; }}
    }}
    </style>

    <div class="quick-transition">
        <div class="quick-text">{message}</div>
    </div>
    """, unsafe_allow_html=True)

    time.sleep(0.5)


def transition_on_first_load(page_name):
    """
    Show transition only on first page load.

    Usage:
        # At the top of your page, after st.set_page_config()
        transition_on_first_load("Dashboard")

    Args:
        page_name: Name of the page
    """
    # Create unique key for this page
    session_key = f'page_loaded_{page_name}'

    if session_key not in st.session_state:
        # Show transition
        show_page_transition(page_name)

        # Mark as loaded
        st.session_state[session_key] = True

        # Rerun to show actual page
        st.rerun()


def transition_with_progress(page_name, steps):
    """
    Show transition with progress through steps.

    Args:
        page_name: Name of the page
        steps: List of step descriptions

    Example:
        steps = [
            "Connecting to database",
            "Loading recommendations",
            "Rendering charts"
        ]
        transition_with_progress("Dashboard", steps)
    """

    placeholder = st.empty()

    st.markdown("""
    <style>
    .progress-transition {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
        background: white;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 9999;
    }

    .progress-text {
        font-size: 1.2rem;
        color: #2d2d2d;
        font-weight: 300;
        margin-bottom: 20px;
        animation: fadeIn 0.3s ease-in;
    }

    .progress-bar {
        width: 300px;
        height: 3px;
        background: #e0e0e0;
        border-radius: 2px;
        overflow: hidden;
    }

    .progress-fill {
        height: 100%;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        transition: width 0.5s ease;
    }

    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(5px); }
        to { opacity: 1; transform: translateY(0); }
    }
    </style>
    """, unsafe_allow_html=True)

    total_steps = len(steps)

    for i, step in enumerate(steps):
        progress = ((i + 1) / total_steps) * 100

        placeholder.markdown(f"""
        <div class="progress-transition">
            <div class="progress-text">{step}</div>
            <div class="progress-bar">
                <div class="progress-fill" style="width: {progress}%"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Yield to allow actual work to happen
        yield

    # Clear transition
    placeholder.empty()


# Example integration
def example_dashboard_with_transition():
    """
    Example of a dashboard page with transition.
    """
    import streamlit as st

    st.set_page_config(
        page_title="Dashboard - Wasteless",
        page_icon="📊",
        layout="wide"
    )

    # Show transition on first load
    transition_on_first_load("Dashboard")

    # Your actual page content
    st.title("📊 Dashboard")
    st.write("Page content here...")


def example_custom_phrases():
    """
    Example with custom phrases.
    """
    custom_phrases = [
        ["Analyzing", "your", "cloud", "infrastructure"],
        ["Identifying", "cost", "optimization", "opportunities"],
        ["Computing", "savings", "potential"],
        ["Ready"]
    ]

    show_page_transition("Custom", custom_phrases)
