#!/usr/bin/env python3
"""
History Page
============

Complete audit trail of all remediation actions.
Optimized with cached queries for better performance.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(
    page_title="History - Wasteless.io",
    page_icon="static/images/favicon.svg",
    layout="wide"
)

from utils.page_transition import transition_on_first_load
from utils.sidebar import setup_sidebar
from utils.logger import get_logger, log_db_query, log_error

# Configurable query limits (can be overridden via environment)
MAX_HISTORY_RECORDS = int(os.getenv('MAX_HISTORY_RECORDS', '100'))

# Show page transition on first load
transition_on_first_load("History")

# Cached data fetching functions
@st.cache_data(ttl=30)
def fetch_action_history(_conn, status_filter="All", action_filter="All",
                         days_back=30, dry_run_filter="All"):
    """
    Fetch action history with filters applied. Cached for 30 seconds.
    Returns DataFrame with action details or empty DataFrame on error.
    """
    logger = get_logger('data')
    start_time = time.time()

    try:
        query = """
            SELECT
                a.id,
                a.resource_id,
                a.action_type,
                a.action_status,
                a.dry_run,
                a.action_date,
                a.error_message,
                a.executed_by,
                r.estimated_monthly_savings_eur
            FROM actions_log a
            LEFT JOIN recommendations r ON a.recommendation_id = r.id
            WHERE a.action_date >= NOW() - INTERVAL '%s days'
        """

        params = [days_back]

        if status_filter != "All":
            query += " AND a.action_status = %s"
            params.append(status_filter)

        if action_filter != "All":
            query += " AND a.action_type = %s"
            params.append(action_filter)

        if dry_run_filter == "Dry-Run Only":
            query += " AND a.dry_run = TRUE"
        elif dry_run_filter == "Production Only":
            query += " AND a.dry_run = FALSE"

        query += f" ORDER BY a.action_date DESC LIMIT {MAX_HISTORY_RECORDS}"

        df = pd.read_sql(query, _conn, params=tuple(params))

        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_action_history', duration_ms, success=True)
        return df

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_action_history', duration_ms, success=False)
        log_error(e, context='fetch_action_history')
        st.error(f"❌ Failed to fetch action history: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def fetch_rollback_candidates(_conn):
    """
    Fetch available rollback snapshots. Cached for 60 seconds.
    Returns DataFrame with rollback candidates or empty DataFrame on error.
    """
    logger = get_logger('data')
    start_time = time.time()

    try:
        query = """
            SELECT
                rs.id,
                rs.resource_id,
                rs.created_at,
                rs.rollback_expiry,
                rs.can_rollback,
                a.action_type
            FROM rollback_snapshots rs
            JOIN actions_log a ON rs.action_log_id = a.id
            WHERE rs.can_rollback = TRUE
              AND rs.rollback_expiry > NOW()
            ORDER BY rs.created_at DESC
        """

        df = pd.read_sql(query, _conn)

        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_rollback_candidates', duration_ms, success=True)
        return df

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_rollback_candidates', duration_ms, success=False)
        log_error(e, context='fetch_rollback_candidates')
        st.error(f"❌ Failed to fetch rollback candidates: {e}")
        return pd.DataFrame()

st.title("📜 Action History")
st.markdown("Complete audit trail of all remediation actions")
st.markdown("---")

# Setup sidebar and get database connection
conn = setup_sidebar()
if not conn:
    st.error("❌ Cannot connect to database")
    st.stop()

# Filters
col1, col2, col3, col4 = st.columns(4)

with col1:
    status_filter = st.selectbox(
        "Status",
        ["All", "success", "failed", "pending", "blocked"]
    )

with col2:
    action_filter = st.selectbox(
        "Action Type",
        ["All", "stop", "start", "terminate"]
    )

with col3:
    days_back = st.number_input(
        "Days Back",
        min_value=1,
        max_value=90,
        value=30
    )

with col4:
    dry_run_filter = st.selectbox(
        "Mode",
        ["All", "Dry-Run Only", "Production Only"]
    )

st.markdown("---")

# Fetch action history with filters
with st.spinner("Loading action history..."):
    df = fetch_action_history(conn, status_filter, action_filter, days_back, dry_run_filter)

# Display summary
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("📊 Total Actions", len(df))

with col2:
    success_count = len(df[df['action_status'] == 'success']) if not df.empty else 0
    st.metric("✅ Successful", success_count)

with col3:
    failed_count = len(df[df['action_status'] == 'failed']) if not df.empty else 0
    st.metric("❌ Failed", failed_count)

with col4:
    total_savings = df['estimated_monthly_savings_eur'].sum() if not df.empty else 0
    st.metric("💰 Total Savings", f"€{total_savings:,.2f}/mo")

st.markdown("---")

# Display actions table
if df.empty:
    st.info("📭 No actions found matching your filters")
else:
    st.subheader(f"📋 {len(df)} Actions")

    # Handle NULL/NaN values in numeric columns
    if 'estimated_monthly_savings_eur' in df.columns:
        df['estimated_monthly_savings_eur'] = pd.to_numeric(
            df['estimated_monthly_savings_eur'], errors='coerce'
        ).fillna(0.0)

    # Add status emoji with safe handling
    def status_icon(status):
        if pd.isna(status):
            return '❓'
        icons = {
            'success': '✅',
            'failed': '❌',
            'pending': '⏳',
            'blocked': '🛑'
        }
        return icons.get(str(status), '❓')

    df['status_display'] = df['action_status'].apply(status_icon)

    st.dataframe(
        df[[
            'id',
            'resource_id',
            'action_type',
            'status_display',
            'dry_run',
            'action_date',
            'estimated_monthly_savings_eur'
        ]],
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "resource_id": st.column_config.TextColumn("Instance", width="medium"),
            "action_type": st.column_config.TextColumn("Action", width="small"),
            "status_display": st.column_config.TextColumn("Status", width="small"),
            "dry_run": st.column_config.CheckboxColumn("Dry-Run", width="small"),
            "action_date": st.column_config.DatetimeColumn(
                "Date",
                format="DD/MM/YY HH:mm",
                width="medium"
            ),
            "estimated_monthly_savings_eur": st.column_config.NumberColumn(
                "💰 Savings",
                format="€%.2f",
                width="small"
            )
        },
        hide_index=True,
        width="stretch"
    )

    # Show errors if any
    errors = df[df['error_message'].notna()]
    if not errors.empty:
        st.markdown("---")
        st.subheader("❌ Actions with Errors")

        for _, row in errors.iterrows():
            with st.expander(f"Action #{row['id']} - {row['resource_id']}"):
                st.error(f"**Error:** {row['error_message']}")
                st.caption(f"Date: {row['action_date']}")

st.markdown("---")

# Rollback section
st.subheader("🔄 Rollback Available")

with st.spinner("Loading rollback candidates..."):
    df_rollback = fetch_rollback_candidates(conn)

if not df_rollback.empty:
    st.dataframe(
        df_rollback,
        column_config={
            "id": "Snapshot ID",
            "resource_id": "Instance",
            "action_type": "Action",
            "created_at": st.column_config.DatetimeColumn("Created"),
            "rollback_expiry": st.column_config.DatetimeColumn("Expires"),
            "can_rollback": st.column_config.CheckboxColumn("Available")
        },
        hide_index=True,
        width="stretch"
    )

    st.info("💡 Rollback functionality coming soon!")
else:
    st.info("No rollback snapshots available")

# Add refresh button in sidebar
with st.sidebar:
    st.markdown("---")
    if st.button("🔄 Refresh History", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
