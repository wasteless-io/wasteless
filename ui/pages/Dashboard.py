#!/usr/bin/env python3
"""
Dashboard Page
==============

Executive dashboard with KPIs, charts, and trends.
Optimized with cached queries for better performance.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(
    page_title="Dashboard - Wasteless.io",
    page_icon="static/images/favicon.svg",
    layout="wide",
    initial_sidebar_state="expanded"
)

from utils.page_transition import transition_on_first_load
from utils.sidebar import setup_sidebar
from utils.logger import get_logger, log_db_query, log_error

# Show page transition on first load
transition_on_first_load("Dashboard")

# Cached data fetching functions
@st.cache_data(ttl=30)
def fetch_dashboard_kpis(_conn):
    """
    Fetch all dashboard KPIs in a single optimized query.
    Uses CTE and CROSS JOIN to minimize database round-trips.
    Returns dict with all metrics or None on error.
    """
    logger = get_logger('data')
    start_time = time.time()

    try:
        query = """
            WITH metrics AS (
                SELECT
                    COALESCE(SUM(estimated_monthly_savings_eur), 0) as potential_monthly
                FROM recommendations
                WHERE status = 'pending'
            ),
            savings AS (
                SELECT COALESCE(SUM(actual_savings_eur), 0) as verified_savings
                FROM savings_realized
            ),
            waste AS (
                SELECT COUNT(*) as waste_count
                FROM waste_detected
            ),
            actions AS (
                SELECT
                    COUNT(CASE WHEN action_status='success' THEN 1 END)::float /
                    NULLIF(COUNT(*), 0) * 100 as success_rate
                FROM actions_log
            )
            SELECT
                m.potential_monthly,
                s.verified_savings,
                w.waste_count,
                COALESCE(a.success_rate, 0) as success_rate
            FROM metrics m
            CROSS JOIN savings s
            CROSS JOIN waste w
            CROSS JOIN actions a;
        """

        cursor = _conn.cursor()
        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()

        if result:
            duration_ms = (time.time() - start_time) * 1000
            log_db_query('fetch_dashboard_kpis', duration_ms, success=True)
            return {
                'potential_monthly': float(result[0]),
                'verified_savings': float(result[1]),
                'waste_count': int(result[2]),
                'success_rate': float(result[3])
            }
        return None

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_dashboard_kpis', duration_ms, success=False)
        log_error(e, context='fetch_dashboard_kpis')
        st.error(f"❌ Failed to fetch KPIs: {e}")
        return None

@st.cache_data(ttl=60)
def fetch_waste_over_time(_conn):
    """Fetch waste detection trend for last 30 days."""
    try:
        query = """
            SELECT
                DATE(created_at) as date,
                COUNT(*) as count,
                SUM(monthly_waste_eur) as total_waste
            FROM waste_detected
            WHERE created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at)
            ORDER BY date
        """
        return pd.read_sql(query, _conn)
    except Exception as e:
        st.error(f"❌ Failed to fetch waste trend: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def fetch_recommendations_by_type(_conn):
    """Fetch recommendation distribution by type."""
    try:
        query = """
            SELECT
                recommendation_type,
                COUNT(*) as count
            FROM recommendations
            WHERE status = 'pending'
            GROUP BY recommendation_type
        """
        return pd.read_sql(query, _conn)
    except Exception as e:
        st.error(f"❌ Failed to fetch recommendations: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=30)
def fetch_recent_waste(_conn, limit=10):
    """Fetch latest waste detected."""
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
        st.error(f"❌ Failed to fetch recent waste: {e}")
        return pd.DataFrame()

st.title("📊 Executive Dashboard")
st.markdown("Real-time cloud cost optimization metrics")
st.markdown("---")

# Setup sidebar and get database connection
conn = setup_sidebar()
if not conn:
    st.error("❌ Cannot connect to database")
    st.stop()

# Fetch all KPIs with optimized single query
with st.spinner("Loading metrics..."):
    kpis = fetch_dashboard_kpis(conn)

if not kpis:
    st.error("❌ Failed to load dashboard data. Please refresh the page.")
    st.stop()

# Top KPIs - Display metrics from cached query
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "💰 Potential Savings/Month",
        f"€{kpis['potential_monthly']:,.2f}",
        delta=f"€{kpis['potential_monthly'] * 12:,.0f}/year"
    )

with col2:
    st.metric(
        "✅ Verified Savings",
        f"€{kpis['verified_savings']:,.2f}",
        delta="Confirmed by AWS"
    )

with col3:
    st.metric(
        "🔍 Waste Detected",
        f"{kpis['waste_count']}",
        delta="Instances"
    )

with col4:
    st.metric(
        "🎯 Success Rate",
        f"{kpis['success_rate']:.1f}%",
        delta="Actions executed"
    )

st.markdown("---")

# Charts - Fetch data with caching
col1, col2 = st.columns(2)

with col1:
    st.subheader("📈 Waste Detection Over Time")

    with st.spinner("Loading trend data..."):
        df_waste = fetch_waste_over_time(conn)

    if not df_waste.empty:
        fig = px.line(df_waste, x='date', y='total_waste',
                     title="Daily Waste Detected (€/month)",
                     labels={'total_waste': 'Waste (€/month)', 'date': 'Date'})
        fig.update_traces(line_color='#ff6b6b')
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data available yet")

with col2:
    st.subheader("📊 Recommendations by Type")

    with st.spinner("Loading recommendations..."):
        df_recs = fetch_recommendations_by_type(conn)

    if not df_recs.empty:
        fig = px.pie(df_recs, values='count', names='recommendation_type',
                    title="Recommendation Distribution")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No pending recommendations")

st.markdown("---")

# Recent waste detected
st.subheader("🔍 Latest Waste Detected")

with st.spinner("Loading recent waste..."):
    df_recent = fetch_recent_waste(conn, limit=10)

if not df_recent.empty:
    st.dataframe(
        df_recent,
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
                max_value=1
            ),
            "created_at": st.column_config.DatetimeColumn(
                "Detected",
                format="DD/MM/YY HH:mm"
            )
        },
        hide_index=True,
        use_container_width=True
    )
else:
    st.info("No waste detected yet")

# Add refresh button in sidebar
with st.sidebar:
    st.markdown("---")
    if st.button("🔄 Refresh Dashboard", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
