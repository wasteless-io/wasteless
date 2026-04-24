#!/usr/bin/env python3
"""
Wasteless.io - Web UI
=====================

Main Streamlit application for Wasteless cloud cost optimization platform.

This provides a user-friendly interface for:
- Viewing cost optimization recommendations
- Approving/rejecting remediation actions
- Tracking verified savings
- Managing safeguard configurations

Author: Wasteless Team
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os
from pathlib import Path
from dotenv import load_dotenv
from utils.logger import get_logger, log_user_action, log_db_query, log_error
from utils.design_system import apply_global_styles, Colors
import time

# Load environment variables from the correct location
# Get the directory where this app.py file is located
APP_DIR = Path(__file__).parent
ENV_PATH = APP_DIR / '.env'
load_dotenv(dotenv_path=ENV_PATH)

# Early validation of backend availability (check once, cache result)
from utils.remediator import validate_backend_at_startup, get_backend_path

# Page configuration
st.set_page_config(
    page_title="Wasteless - Cloud Cost Optimizer",
    page_icon="static/images/favicon.svg",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': 'https://github.com/wastelessio/wasteless',
        'Report a bug': 'https://github.com/wastelessio/wasteless/issues',
        'About': '# Wasteless\nAutonomous cloud cost optimization platform'
    }
)

# Show loading animation on first app launch
if 'app_loaded' not in st.session_state:
    from utils.loading_animation import show_loading_animation
    show_loading_animation()
    st.session_state.app_loaded = True
    st.rerun()

# Show page transition on first page load
from utils.page_transition import transition_on_first_load
transition_on_first_load("Home")

# Apply design system styles
apply_global_styles()

# Custom CSS for page-specific styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 0.5rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
        margin: 0.5rem 0;
    }
    .success-box {
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        border-radius: 5px;
        padding: 1rem;
        margin: 1rem 0;
    }
    .warning-box {
        background-color: #fff3cd;
        border: 1px solid #ffeaa7;
        border-radius: 5px;
        padding: 1rem;
        margin: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=30)
def fetch_home_metrics(_conn):
    """
    Fetch all home page metrics in a single optimized query.
    Uses CTE and single query to minimize database round-trips.
    Returns dict with all metrics or None on error.
    """
    logger = get_logger('data')
    start_time = time.time()
    try:
        query = """
            WITH metrics AS (
                SELECT
                    COALESCE(SUM(estimated_monthly_savings_eur), 0) as potential_savings,
                    COUNT(*) FILTER (WHERE status = 'pending') as pending_count
                FROM recommendations
            ),
            actions AS (
                SELECT COUNT(*) as success_count
                FROM actions_log
                WHERE action_status = 'success'
            )
            SELECT
                m.potential_savings,
                m.pending_count,
                a.success_count
            FROM metrics m
            CROSS JOIN actions a;
        """

        cursor = _conn.cursor()
        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()

        if result:
            duration_ms = (time.time() - start_time) * 1000
            log_db_query('fetch_home_metrics', duration_ms, success=True)
            return {
                'potential_savings': float(result[0]),
                'pending_count': int(result[1]),
                'actions_count': int(result[2])
            }
        return None

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_home_metrics', duration_ms, success=False)
        log_error(e, context='fetch_home_metrics')
        st.error(f"❌ Failed to fetch metrics: {e}")
        return None

@st.cache_data(ttl=30)
def fetch_recent_waste(_conn, limit=5):
    """Fetch recent waste detected with error handling."""
    try:
        query = """
            SELECT
                resource_id,
                waste_type,
                monthly_waste_eur,
                confidence_score,
                created_at
            FROM waste_detected
            ORDER BY created_at DESC
            LIMIT %s
        """
        return pd.read_sql(query, _conn, params=(limit,))
    except Exception as e:
        st.error(f"❌ Failed to fetch waste data: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=30)
def fetch_recent_actions(_conn, limit=5):
    """Fetch recent actions with error handling."""
    try:
        query = """
            SELECT
                resource_id,
                action_type,
                action_status,
                action_date
            FROM actions_log
            ORDER BY action_date DESC
            LIMIT %s
        """
        return pd.read_sql(query, _conn, params=(limit,))
    except Exception as e:
        st.error(f"❌ Failed to fetch actions data: {e}")
        return pd.DataFrame()

# Setup sidebar with logo and navigation
from utils.sidebar import setup_sidebar
conn = setup_sidebar()
if not conn:
    st.stop()

# Main page content - Title only (logo is in sidebar)
st.markdown('<h1 class="main-header">W A S T E L E S S</h1>', unsafe_allow_html=True)
st.markdown("---")

# Check backend availability and show warning if not available
backend_valid, backend_message = validate_backend_at_startup()
if not backend_valid:
    st.warning(f"""
    ⚠️ **Backend Not Available**

    The wasteless backend is required for executing remediation actions.

    **Path checked:** `{get_backend_path()}`

    You can still view recommendations and history, but execution features are disabled.

    To enable full functionality, clone the backend repository next to wasteless-ui.
    """)
    st.session_state['backend_available'] = False
else:
    st.session_state['backend_available'] = True

# Welcome section - Display metrics
col1, col2, col3 = st.columns(3)

# Fetch all metrics with a single optimized query and spinner
with st.spinner("Loading metrics..."):
    metrics = fetch_home_metrics(conn)

if metrics:
    potential_savings = metrics['potential_savings']
    pending_count = metrics['pending_count']
    actions_count = metrics['actions_count']

    # Display metrics
    with col1:
        st.metric(
            label="💵 Potential Monthly Savings",
            value=f"€{potential_savings:,.2f}",
            delta=f"€{potential_savings * 12:,.0f}/year"
        )

    with col2:
        st.metric(
            label="📋 Pending Recommendations",
            value=f"{pending_count}",
            delta="Ready to review"
        )

    with col3:
        st.metric(
            label="✅ Actions Executed",
            value=f"{actions_count}",
            delta="Successfully applied"
        )
else:
    st.error("❌ Failed to load metrics. Please refresh the page.")

st.markdown("---")

# Recent activity
st.subheader("📊 Recent Activity")

col1, col2 = st.columns(2)

with col1:
    st.markdown("### 🔍 Latest Waste Detected")

    with st.spinner("Loading waste data..."):
        df_waste = fetch_recent_waste(conn, limit=5)

    if not df_waste.empty:
        st.dataframe(
            df_waste,
            column_config={
                "resource_id": "Instance",
                "waste_type": "Type",
                "monthly_waste_eur": st.column_config.NumberColumn(
                    "€/month",
                    format="€%.2f"
                ),
                "confidence_score": st.column_config.ProgressColumn(
                    "Confidence",
                    min_value=0,
                    max_value=1,
                ),
                "created_at": st.column_config.DatetimeColumn(
                    "Detected",
                    format="DD/MM/YY HH:mm"
                )
            },
            hide_index=True,
            width="stretch"
        )
    else:
        st.info("No waste detected yet. Run the detection pipeline first.")

with col2:
    st.markdown("### 🚀 Latest Actions")

    with st.spinner("Loading actions..."):
        df_actions = fetch_recent_actions(conn, limit=5)

    if not df_actions.empty:
        # Add emoji based on status
        def status_emoji(status):
            return "✅" if status == "success" else "❌" if status == "failed" else "🔄"

        df_actions['status_display'] = df_actions['action_status'].apply(status_emoji)

        st.dataframe(
            df_actions[['resource_id', 'action_type', 'status_display', 'action_date']],
            column_config={
                "resource_id": "Instance",
                "action_type": "Action",
                "status_display": "Status",
                "action_date": st.column_config.DatetimeColumn(
                    "Date",
                    format="DD/MM/YY HH:mm"
                )
            },
            hide_index=True,
            width="stretch"
        )
    else:
        st.info("No actions executed yet.")

st.markdown("---")

# Quick actions
st.subheader("⚡ Quick Actions")

col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("📋 View Recommendations", width="stretch"):
        st.switch_page("pages/2_📋_Recommendations.py")

with col2:
    if st.button("📊 Open Dashboard", width="stretch"):
        st.switch_page("pages/1_📊_Dashboard.py")

with col3:
    if st.button("📜 View History", width="stretch"):
        st.switch_page("pages/3_📜_History.py")

with col4:
    if st.button("⚙️ Settings", width="stretch"):
        st.switch_page("pages/4_⚙️_Settings.py")

st.markdown("---")

# Footer
st.markdown("""
<div style="text-align: center; color: #666; padding: 2rem 0;">
    <p><strong>Wasteless.io</strong> - Stop monitoring waste. Start eliminating it.</p>
    <p style="font-size: 0.9rem;">
        Built with precision for CFOs who demand results. |
        <a href="https://github.com/wastelessio/wasteless-ui" target="_blank">GitHub</a> |
        <a href="mailto:wasteless.io.entreprise@gmail.com">Contact</a>
    </p>
</div>
""", unsafe_allow_html=True)
