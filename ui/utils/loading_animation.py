"""
Loading Animation for Wasteless UI
===================================

Elegant loading screen with animated text similar to Claude's thinking animation.
"""

import streamlit as st
import time


def show_loading_animation():
    """
    Display an elegant loading animation while the app initializes.
    Features animated words appearing progressively on a gradient background.
    """

    # CSS for the loading animation
    st.markdown("""
    <style>
    .loading-container {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 9999;
    }

    .loading-text {
        font-size: 2rem;
        color: white;
        font-weight: 300;
        text-align: center;
        margin: 20px;
        min-height: 60px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-wrap: wrap;
    }

    .loading-word {
        display: inline-block;
        opacity: 0;
        animation: wordFadeIn 0.6s ease-in forwards;
        margin: 0 8px;
    }

    @keyframes wordFadeIn {
        from {
            opacity: 0;
            transform: translateY(10px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }

    .logo-container {
        margin-bottom: 40px;
    }

    @keyframes pulse {
        0%, 100% {
            transform: scale(1);
            opacity: 1;
        }
        50% {
            transform: scale(1.05);
            opacity: 0.9;
        }
    }

    .spinner {
        width: 50px;
        height: 50px;
        border: 4px solid rgba(255, 255, 255, 0.3);
        border-top: 4px solid white;
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin-top: 40px;
    }

    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }

    .loading-subtitle {
        color: rgba(255, 255, 255, 0.7);
        font-size: 1rem;
        margin-top: 20px;
        font-weight: 300;
    }
    </style>
    """, unsafe_allow_html=True)

    # Sequence of phrases that appear during loading
    loading_phrases = [
        ["Initializing", "Wasteless", "platform"],
        ["Connecting", "to", "database"],
        ["Loading", "cost", "optimization", "engine"],
        ["Analyzing", "cloud", "infrastructure"],
        ["Preparing", "recommendations"],
        ["Almost", "ready"]
    ]

    # Create placeholder for animation
    placeholder = st.empty()

    # Display each phrase with animated words
    for phrase in loading_phrases:
        html_words = ""
        for i, word in enumerate(phrase):
            delay = i * 0.15  # Stagger animation for each word
            html_words += f'<span class="loading-word" style="animation-delay: {delay}s">{word}</span>'

        placeholder.markdown(f"""
        <div class="loading-container">
            <div class="loading-text">
                {html_words}
            </div>
            <div class="spinner"></div>
            <div class="loading-subtitle">
                Autonomous cloud cost optimization
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Wait before showing next phrase
        time.sleep(1.2)

    # Clear the loading screen
    placeholder.empty()


def show_page_loading(message="Loading"):
    """
    Show a quick loading indicator for page transitions.

    Args:
        message: Loading message to display
    """
    st.markdown(f"""
    <div style="text-align: center; padding: 100px 20px;">
        <div class="spinner" style="margin: 0 auto;"></div>
        <p style="color: #667eea; margin-top: 20px; font-size: 1.2rem;">{message}...</p>
    </div>
    """, unsafe_allow_html=True)
