"""
Wasteless Design System
=======================

Centralized design tokens and reusable components for consistent UI.
"""
import streamlit as st


# =============================================================================
# DESIGN TOKENS - Brand Colors
# =============================================================================

class Colors:
    """Brand color palette."""
    # Primary brand colors
    PRIMARY = "#667eea"
    PRIMARY_DARK = "#5568d3"
    PRIMARY_LIGHT = "#7c8ff0"

    # Secondary colors
    SECONDARY = "#764ba2"
    ACCENT = "#f093fb"

    # Semantic colors
    SUCCESS = "#10b981"
    SUCCESS_BG = "#d1fae5"
    WARNING = "#f59e0b"
    WARNING_BG = "#fef3c7"
    ERROR = "#ef4444"
    ERROR_BG = "#fee2e2"
    INFO = "#3b82f6"
    INFO_BG = "#dbeafe"

    # Neutral colors
    GRAY_50 = "#f9fafb"
    GRAY_100 = "#f3f4f6"
    GRAY_200 = "#e5e7eb"
    GRAY_300 = "#d1d5db"
    GRAY_400 = "#9ca3af"
    GRAY_500 = "#6b7280"
    GRAY_600 = "#4b5563"
    GRAY_700 = "#374151"
    GRAY_800 = "#1f2937"
    GRAY_900 = "#111827"

    # Text colors
    TEXT_PRIMARY = "#111827"
    TEXT_SECONDARY = "#6b7280"
    TEXT_LIGHT = "#9ca3af"


class Spacing:
    """Spacing scale."""
    XS = "0.25rem"   # 4px
    SM = "0.5rem"    # 8px
    MD = "1rem"      # 16px
    LG = "1.5rem"    # 24px
    XL = "2rem"      # 32px
    XXL = "3rem"     # 48px


class Typography:
    """Typography settings."""
    FONT_FAMILY = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"

    # Font sizes
    TEXT_XS = "0.75rem"    # 12px
    TEXT_SM = "0.875rem"   # 14px
    TEXT_BASE = "1rem"     # 16px
    TEXT_LG = "1.125rem"   # 18px
    TEXT_XL = "1.25rem"    # 20px
    TEXT_2XL = "1.5rem"    # 24px
    TEXT_3XL = "1.875rem"  # 30px
    TEXT_4XL = "2.25rem"   # 36px


class BorderRadius:
    """Border radius scale."""
    SM = "0.25rem"   # 4px
    MD = "0.5rem"    # 8px
    LG = "0.75rem"   # 12px
    XL = "1rem"      # 16px
    FULL = "9999px"


# =============================================================================
# GLOBAL STYLES
# =============================================================================

def apply_global_styles():
    """Apply global CSS styles to the application."""
    st.markdown(f"""
    <style>
        /* Global styles */
        :root {{
            --primary-color: {Colors.PRIMARY};
            --secondary-color: {Colors.SECONDARY};
            --success-color: {Colors.SUCCESS};
            --warning-color: {Colors.WARNING};
            --error-color: {Colors.ERROR};
            --info-color: {Colors.INFO};
        }}

        /* Custom font */
        * {{
            font-family: {Typography.FONT_FAMILY};
        }}

        /* Page header */
        .main-header {{
            font-size: {Typography.TEXT_4XL};
            font-weight: 700;
            color: {Colors.PRIMARY};
            margin-bottom: {Spacing.MD};
            text-align: center;
        }}

        /* Section headers */
        h1, h2, h3 {{
            color: {Colors.TEXT_PRIMARY};
        }}

        /* Metric cards enhancement */
        [data-testid="stMetric"] {{
            background: linear-gradient(135deg, {Colors.PRIMARY} 0%, {Colors.SECONDARY} 100%);
            padding: {Spacing.LG};
            border-radius: {BorderRadius.LG};
            color: white;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}

        [data-testid="stMetric"] label {{
            color: rgba(255, 255, 255, 0.9) !important;
            font-weight: 600;
        }}

        [data-testid="stMetric"] [data-testid="stMetricValue"] {{
            color: white !important;
            font-size: {Typography.TEXT_2XL};
            font-weight: 700;
        }}

        [data-testid="stMetric"] [data-testid="stMetricDelta"] {{
            color: rgba(255, 255, 255, 0.8) !important;
        }}

        /* Cards */
        .card {{
            background: white;
            padding: {Spacing.LG};
            border-radius: {BorderRadius.LG};
            border: 1px solid {Colors.GRAY_200};
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
            margin-bottom: {Spacing.MD};
        }}

        .card-success {{
            background: {Colors.SUCCESS_BG};
            border-color: {Colors.SUCCESS};
            border-left: 4px solid {Colors.SUCCESS};
        }}

        .card-warning {{
            background: {Colors.WARNING_BG};
            border-color: {Colors.WARNING};
            border-left: 4px solid {Colors.WARNING};
        }}

        .card-error {{
            background: {Colors.ERROR_BG};
            border-color: {Colors.ERROR};
            border-left: 4px solid {Colors.ERROR};
        }}

        .card-info {{
            background: {Colors.INFO_BG};
            border-color: {Colors.INFO};
            border-left: 4px solid {Colors.INFO};
        }}

        /* Buttons enhancement */
        .stButton > button {{
            border-radius: {BorderRadius.MD};
            font-weight: 600;
            transition: all 0.2s;
            border: none;
        }}

        .stButton > button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}

        /* Primary button */
        .stButton > button[kind="primary"] {{
            background: linear-gradient(135deg, {Colors.PRIMARY} 0%, {Colors.SECONDARY} 100%);
        }}

        /* Dataframe styling */
        [data-testid="stDataFrame"] {{
            border-radius: {BorderRadius.MD};
            overflow: hidden;
        }}

        /* Expander styling */
        .streamlit-expanderHeader {{
            background-color: {Colors.GRAY_50};
            border-radius: {BorderRadius.MD};
            font-weight: 600;
        }}

        /* Sidebar styling */
        [data-testid="stSidebar"] {{
            background: linear-gradient(180deg, {Colors.GRAY_50} 0%, white 100%);
        }}

        /* Alert boxes */
        .stAlert {{
            border-radius: {BorderRadius.MD};
            border-left-width: 4px;
        }}

        /* Form styling */
        [data-testid="stForm"] {{
            border: 2px solid {Colors.GRAY_200};
            border-radius: {BorderRadius.LG};
            padding: {Spacing.LG};
            background: {Colors.GRAY_50};
        }}

        /* Progress bars */
        .stProgress > div > div > div {{
            background: linear-gradient(90deg, {Colors.PRIMARY} 0%, {Colors.SECONDARY} 100%);
        }}

        /* Divider */
        hr {{
            margin: {Spacing.LG} 0;
            border-color: {Colors.GRAY_200};
        }}
    </style>
    """, unsafe_allow_html=True)


# =============================================================================
# REUSABLE COMPONENTS
# =============================================================================

def render_status_badge(status: str, text: str = None) -> str:
    """
    Render a status badge with appropriate color.

    Args:
        status: One of 'success', 'warning', 'error', 'info', 'neutral'
        text: Optional text to display (defaults to status)

    Returns:
        HTML string for the badge
    """
    text = text or status.title()

    color_map = {
        'success': (Colors.SUCCESS, Colors.SUCCESS_BG),
        'warning': (Colors.WARNING, Colors.WARNING_BG),
        'error': (Colors.ERROR, Colors.ERROR_BG),
        'info': (Colors.INFO, Colors.INFO_BG),
        'neutral': (Colors.GRAY_600, Colors.GRAY_100)
    }

    fg_color, bg_color = color_map.get(status, color_map['neutral'])

    return f"""
    <span style="
        display: inline-block;
        padding: {Spacing.XS} {Spacing.SM};
        background-color: {bg_color};
        color: {fg_color};
        border-radius: {BorderRadius.FULL};
        font-size: {Typography.TEXT_SM};
        font-weight: 600;
    ">
        {text}
    </span>
    """


def render_card(content: str, card_type: str = "default", title: str = None):
    """
    Render a styled card component.

    Args:
        content: HTML content to display in the card
        card_type: One of 'default', 'success', 'warning', 'error', 'info'
        title: Optional card title
    """
    card_class = f"card card-{card_type}" if card_type != "default" else "card"

    title_html = f"<h4 style='margin-top: 0;'>{title}</h4>" if title else ""

    st.markdown(f"""
    <div class="{card_class}">
        {title_html}
        {content}
    </div>
    """, unsafe_allow_html=True)


def render_metric_card(label: str, value: str, delta: str = None, icon: str = "📊"):
    """
    Render a custom metric card with gradient background.

    Args:
        label: Metric label
        value: Metric value
        delta: Optional delta text
        icon: Optional emoji icon
    """
    delta_html = f"<div style='font-size: {Typography.TEXT_SM}; opacity: 0.9;'>{delta}</div>" if delta else ""

    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, {Colors.PRIMARY} 0%, {Colors.SECONDARY} 100%);
        padding: {Spacing.LG};
        border-radius: {BorderRadius.LG};
        color: white;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    ">
        <div style="font-size: {Typography.TEXT_SM}; opacity: 0.9; margin-bottom: {Spacing.XS};">
            {icon} {label}
        </div>
        <div style="font-size: {Typography.TEXT_3XL}; font-weight: 700; margin-bottom: {Spacing.XS};">
            {value}
        </div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)


def render_section_header(title: str, subtitle: str = None, icon: str = None):
    """
    Render a styled section header.

    Args:
        title: Section title
        subtitle: Optional subtitle
        icon: Optional emoji icon
    """
    icon_html = f"{icon} " if icon else ""
    subtitle_html = f"<p style='color: {Colors.TEXT_SECONDARY}; margin-top: {Spacing.SM};'>{subtitle}</p>" if subtitle else ""

    st.markdown(f"""
    <div style="margin-bottom: {Spacing.LG};">
        <h2 style="color: {Colors.TEXT_PRIMARY}; margin-bottom: {Spacing.XS};">
            {icon_html}{title}
        </h2>
        {subtitle_html}
    </div>
    """, unsafe_allow_html=True)


def render_stat_row(stats: list):
    """
    Render a row of statistics.

    Args:
        stats: List of dicts with 'label', 'value', 'icon' keys
    """
    cols = st.columns(len(stats))

    for col, stat in zip(cols, stats):
        with col:
            icon = stat.get('icon', '📊')
            label = stat['label']
            value = stat['value']

            st.markdown(f"""
            <div style="text-align: center; padding: {Spacing.MD};">
                <div style="font-size: {Typography.TEXT_4XL};">{icon}</div>
                <div style="font-size: {Typography.TEXT_2XL}; font-weight: 700; color: {Colors.PRIMARY}; margin: {Spacing.SM} 0;">
                    {value}
                </div>
                <div style="font-size: {Typography.TEXT_SM}; color: {Colors.TEXT_SECONDARY};">
                    {label}
                </div>
            </div>
            """, unsafe_allow_html=True)
