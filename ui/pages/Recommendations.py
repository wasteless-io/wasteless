#!/usr/bin/env python3
"""
Recommendations Page
====================

View, filter, and manage cost optimization recommendations.
Optimized with cached queries for better performance.
"""

import streamlit as st
import pandas as pd
import sys
import os
from datetime import datetime
import time

# Add parent directory to path to import app utilities
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(
    page_title="Recommendations - Wasteless.io",
    page_icon="static/images/favicon.svg",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Import utilities
from utils.page_transition import transition_on_first_load
from utils.sidebar import setup_sidebar
from utils.logger import get_logger, log_db_query, log_error, log_remediation_action

# Configurable query limits (can be overridden via environment)
MAX_RECOMMENDATIONS_PER_PAGE = int(os.getenv('MAX_RECOMMENDATIONS_PER_PAGE', '100'))
MAX_RECOMMENDATIONS_TOTAL = int(os.getenv('MAX_RECOMMENDATIONS_TOTAL', '500'))

# Show page transition on first load
transition_on_first_load("Recommendations")

# Import remediator integration
from utils.remediator import RemediatorProxy, check_backend_available, get_backend_path
from utils.config_manager import ConfigManager

# Cached data fetching function
@st.cache_data(ttl=30)
def fetch_recommendations(_conn, rec_type_filter="All", min_savings=0, min_confidence=0.0):
    """
    Fetch recommendations with filters applied.
    Cached for 30 seconds to avoid repeated queries on UI interactions.

    Args:
        _conn: Database connection (underscore prefix excludes from cache key)
        rec_type_filter: Filter by recommendation type
        min_savings: Minimum monthly savings filter
        min_confidence: Minimum confidence score filter

    Returns:
        DataFrame with filtered recommendations or empty DataFrame on error
    """
    logger = get_logger('data')
    start_time = time.time()

    try:
        # Build query
        query = """
            SELECT
                r.id,
                r.recommendation_type,
                w.resource_id,
                r.estimated_monthly_savings_eur,
                w.confidence_score,
                r.action_required,
                r.status,
                r.created_at,
                w.metadata->>'instance_type' as instance_type,
                (w.metadata->>'cpu_avg_7d')::numeric as cpu_avg
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status = 'pending'
        """

        # Apply filters
        params = []
        if rec_type_filter != "All":
            query += " AND r.recommendation_type = %s"
            params.append(rec_type_filter)

        if min_savings > 0:
            query += " AND r.estimated_monthly_savings_eur >= %s"
            params.append(min_savings)

        if min_confidence > 0:
            query += " AND w.confidence_score >= %s"
            params.append(min_confidence)

        query += f" ORDER BY r.estimated_monthly_savings_eur DESC LIMIT {MAX_RECOMMENDATIONS_TOTAL}"

        # Execute query
        df = pd.read_sql(query, _conn, params=params if params else None)

        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_recommendations', duration_ms, success=True)

        return df

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_recommendations', duration_ms, success=False)
        log_error(e, context='fetch_recommendations')
        st.error(f"❌ Failed to fetch recommendations: {e}")
        return pd.DataFrame()

st.title("📋 Cost Optimization Recommendations")
st.markdown("Review and manage idle resource recommendations")

# =============================================================================
# ACTIONABLE ALERTS - Configuration Status with Interactive Toggle
# =============================================================================

# Check current configuration
try:
    config_manager = ConfigManager()
    config = config_manager.load_config()
    auto_enabled = config.get('auto_remediation', {}).get('enabled', False)

    # Show status alert with interactive toggle
    col_status, col_toggle, col_settings = st.columns([3, 1, 1])

    with col_status:
        if not auto_enabled:
            st.success("✅ **AUTO-REMEDIATION IS DISABLED** - Only dry-run mode is active (safe)")
        else:
            st.error("⚠️ **AUTO-REMEDIATION IS ENABLED** - Real AWS actions will be executed!")

    with col_toggle:
        st.markdown("###")  # Spacing
        new_enabled = st.toggle(
            "Enable Auto-Remediation",
            value=auto_enabled,
            key="auto_remediation_toggle_recommendations",
            help="⚠️ When enabled, 'Approve (Execute)' will perform REAL AWS actions"
        )

        # Save if changed
        if new_enabled != auto_enabled:
            with st.spinner("Saving configuration..."):
                if config_manager.set_auto_remediation_enabled(new_enabled):
                    st.success("✅ Saved!")
                    st.rerun()
                else:
                    st.error("❌ Failed to save")

    with col_settings:
        st.markdown("###")  # Spacing
        if st.button("⚙️ All Settings", key="goto_settings_btn", width="stretch"):
            st.switch_page("pages/4_⚙️_Settings.py")

    # Schedule restrictions info and toggle
    schedule = config.get('schedule', {})
    allowed_days = schedule.get('allowed_days', [])
    allowed_hours = schedule.get('allowed_hours', [])
    schedule_enabled = bool(allowed_days) and bool(allowed_hours)

    if schedule_enabled:
        st.info(f"⏰ **Schedule Restrictions Active**: Only {', '.join(allowed_days)} at {', '.join([f'{h}:00' for h in allowed_hours])} ({schedule.get('timezone', 'UTC')})")

        col_schedule_msg, col_schedule_toggle = st.columns([4, 1])

        with col_schedule_msg:
            st.caption("💡 Schedule safeguard prevents execution outside maintenance windows")

        with col_schedule_toggle:
            if st.button("🔓 Disable Schedule", key="disable_schedule_btn", width="stretch", type="secondary"):
                if config_manager.disable_schedule_restrictions():
                    st.success("✅ Schedule restrictions disabled - execution allowed anytime!")
                    st.rerun()
                else:
                    st.error("❌ Failed to disable schedule")
    else:
        st.warning("⚠️ **Schedule Restrictions Disabled** - Execution allowed at any time")

        col_schedule_msg, col_schedule_toggle = st.columns([4, 1])

        with col_schedule_msg:
            st.caption("ℹ️ For production, consider enabling schedule restrictions")

        with col_schedule_toggle:
            if st.button("🔒 Enable Schedule", key="enable_schedule_btn", width="stretch", type="secondary"):
                # Restore default schedule (weekends, 2-5am)
                if config_manager.enable_schedule_restrictions(
                    days=["Saturday", "Sunday"],
                    hours=[2, 3, 4]
                ):
                    st.success("✅ Schedule restored to safe defaults (weekends 2-5am)")
                    st.rerun()
                else:
                    st.error("❌ Failed to enable schedule")

except Exception as e:
    st.warning(f"⚠️ Could not load configuration status: {e}")

st.markdown("---")

# Setup sidebar and get database connection
conn = setup_sidebar()
if not conn:
    st.error("❌ Cannot connect to database")
    st.stop()

# Filters
st.subheader("🔍 Filters")
col1, col2, col3 = st.columns(3)

with col1:
    rec_type_filter = st.selectbox(
        "Recommendation Type",
        ["All", "stop_instance", "terminate_instance", "downsize_instance"]
    )

with col2:
    min_savings = st.slider(
        "Min Monthly Savings (€)",
        min_value=0,
        max_value=100,
        value=0,
        step=5
    )

with col3:
    min_confidence = st.slider(
        "Min Confidence Score",
        min_value=0.0,
        max_value=1.0,
        value=0.0,
        step=0.1
    )

st.markdown("---")

# Fetch recommendations with caching
with st.spinner("Loading recommendations..."):
    df = fetch_recommendations(
        conn,
        rec_type_filter=rec_type_filter,
        min_savings=min_savings,
        min_confidence=min_confidence
    )

# Display summary
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "💼 Filtered Recommendations",
        len(df)
    )

with col2:
    total_savings = df['estimated_monthly_savings_eur'].sum() if not df.empty else 0
    st.metric(
        "💰 Total Potential Savings",
        f"€{total_savings:,.2f}/month",
        delta=f"€{total_savings * 12:,.0f}/year"
    )

with col3:
    avg_confidence = df['confidence_score'].mean() if not df.empty else 0
    st.metric(
        "🎯 Average Confidence",
        f"{avg_confidence:.1%}"
    )

st.markdown("---")

# Display recommendations table
if df.empty:
    st.info("📭 No recommendations match your filters")
else:
    st.subheader(f"📊 {len(df)} Recommendations")

    # Format dataframe for display
    df_display = df.copy()
    # Handle NULL/NaN values in cpu_avg - convert to numeric and fill NaN with 0
    df_display['cpu_avg'] = pd.to_numeric(df_display['cpu_avg'], errors='coerce').fillna(0.0)

    st.dataframe(
        df_display[[
            'id',
            'resource_id',
            'recommendation_type',
            'instance_type',
            'cpu_avg',
            'estimated_monthly_savings_eur',
            'confidence_score',
            'created_at'
        ]],
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "resource_id": st.column_config.TextColumn("Instance ID", width="medium"),
            "recommendation_type": st.column_config.TextColumn("Action", width="medium"),
            "instance_type": st.column_config.TextColumn("Type", width="small"),
            "cpu_avg": st.column_config.NumberColumn(
                "Avg CPU %",
                format="%.2f%%",
                width="small"
            ),
            "estimated_monthly_savings_eur": st.column_config.NumberColumn(
                "💰 Savings/mo",
                format="€%.2f",
                width="medium"
            ),
            "confidence_score": st.column_config.ProgressColumn(
                "🎯 Confidence",
                min_value=0,
                max_value=1,
                width="medium"
            ),
            "created_at": st.column_config.DatetimeColumn(
                "Created",
                format="DD/MM/YY",
                width="small"
            )
        },
        hide_index=True,
        width="stretch"
    )

    st.markdown("---")

    # Action section
    st.subheader("🚀 Take Action")

    # Mode indicator banner
    st.markdown("""
    <div style="padding: 10px; border-radius: 5px; margin-bottom: 15px; background-color: #e8f5e9; border-left: 4px solid #4caf50;">
        <strong>🧪 DRY-RUN MODE (DEFAULT)</strong> - No real AWS actions will be executed unless you explicitly choose "Approve (Execute)"
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])

    with col1:
        selected_ids = st.multiselect(
            "Select recommendation IDs to process",
            options=df['id'].tolist(),
            help="You can select multiple recommendations"
        )

    with col2:
        st.markdown("###")  # Spacing
        action_type = st.radio(
            "Action Mode",
            ["🧪 Dry-Run (Safe)", "⚡ EXECUTE (Production)", "❌ Reject"],
            help="⚠️ EXECUTE will perform REAL AWS actions!",
            captions=[
                "Simulate action - no changes",
                "⚠️ REAL AWS changes!",
                "Mark as rejected"
            ]
        )
        # Map display names to internal values
        action_map = {
            "🧪 Dry-Run (Safe)": "Approve (Dry-Run)",
            "⚡ EXECUTE (Production)": "Approve (Execute)",
            "❌ Reject": "Reject"
        }
        action_type = action_map.get(action_type, action_type)

    if selected_ids:
        st.info(f"📌 {len(selected_ids)} recommendation(s) selected: {selected_ids}")

        # Check if backend is available - use validate function for detailed message
        from utils.remediator import validate_backend_at_startup, get_backend_error
        backend_available = check_backend_available()
        if not backend_available:
            backend_error = get_backend_error()
            st.error(f"""
            ❌ **Backend Not Available - Actions Disabled**

            **Location checked:** `{get_backend_path()}`

            **Error:** {backend_error or 'Cannot import EC2Remediator module'}

            **To fix this:**
            ```bash
            cd {os.path.dirname(get_backend_path())}
            git clone https://github.com/wastelessio/wasteless.git
            cd wasteless
            pip install -r requirements.txt
            ```

            You can still view and reject recommendations, but **Approve** actions are disabled.
            """)

        # Special handling for Production mode - show confirmation BEFORE button
        if action_type == "Approve (Execute)":
            st.markdown("""
            <div style="padding: 15px; border-radius: 5px; margin: 10px 0; background-color: #ffebee; border: 2px solid #f44336;">
                <h3 style="color: #c62828; margin-top: 0;">⚠️ PRODUCTION MODE SELECTED</h3>
                <p style="color: #c62828; font-size: 16px;">
                    <strong>This will execute REAL AWS actions on your account!</strong>
                </p>
                <ul style="color: #c62828;">
                    <li>Instances will be <strong>STOPPED</strong> or <strong>TERMINATED</strong></li>
                    <li>This action may be <strong>IRREVERSIBLE</strong> for terminated instances</li>
                    <li>Ensure you have reviewed all selected recommendations</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

            confirm_production = st.checkbox(
                "✅ I CONFIRM: I understand this will modify my AWS infrastructure and have reviewed all selected recommendations",
                key="confirm_prod_checkbox"
            )
        elif action_type == "Approve (Dry-Run)":
            st.info("🧪 **DRY-RUN MODE** - Actions will be simulated. No actual AWS changes will be made.")
            confirm_production = True
        else:  # Reject
            st.info("❌ **REJECT** - Selected recommendations will be marked as rejected. No AWS actions.")
            confirm_production = True

        col1, col2, col3 = st.columns(3)

        with col1:
            # Disable button if production mode and not confirmed
            button_disabled = not backend_available or (action_type == "Approve (Execute)" and not confirm_production)

            button_label = "✅ Execute Selected" if action_type != "Approve (Execute)" else "⚡ EXECUTE ON AWS"
            button_type = "primary" if action_type != "Approve (Execute)" else "secondary"

            if st.button(button_label, type=button_type, width="stretch", disabled=button_disabled, key="execute_btn"):
                if action_type == "Reject":
                    # Reject recommendations
                    try:
                        remediator = RemediatorProxy()
                        result = remediator.reject_recommendations(conn, selected_ids)

                        # Log the action for audit trail
                        log_remediation_action(
                            action_type='reject',
                            recommendation_ids=selected_ids,
                            result=result,
                            dry_run=True  # Reject is always safe
                        )

                        if result['success']:
                            st.success(f"✅ {result['rejected_count']} recommendation(s) rejected!")
                            st.balloons()
                            # Clear cache to ensure fresh data on next load
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(f"❌ Failed to reject: {result.get('error', 'Unknown error')}")
                    except Exception as e:
                        st.error(f"❌ Error: {e}")
                        log_error(e, context='reject_recommendations')

                elif action_type == "Approve (Dry-Run)":
                    # Execute in dry-run mode
                    st.info("🧪 **DRY-RUN MODE** - No actual AWS actions will be executed")

                    try:
                        with st.spinner("🔄 Executing actions in dry-run mode..."):
                            remediator = RemediatorProxy(dry_run=True)
                            results = remediator.execute_recommendations(conn, selected_ids)

                        # Log the action for audit trail
                        log_remediation_action(
                            action_type='approve_dry_run',
                            recommendation_ids=selected_ids,
                            result=results,
                            dry_run=True
                        )

                        # Display results
                        success_count = len([r for r in results if r.get('success', False)])
                        failed_count = len(results) - success_count

                        if success_count > 0:
                            st.success(f"✅ {success_count}/{len(results)} actions completed successfully!")

                        if failed_count > 0:
                            st.warning(f"⚠️ {failed_count}/{len(results)} actions failed")

                        # Detailed results
                        st.markdown("### 📋 Execution Details")
                        for r in results:
                            with st.expander(f"{'✅' if r.get('success') else '❌'} Recommendation #{r['recommendation_id']} - {r.get('instance_id', 'unknown')}"):
                                st.json(r)

                        if success_count > 0:
                            st.balloons()
                            # Clear cache to ensure fresh data on next load
                            st.cache_data.clear()
                            if st.button("🔄 Refresh Page"):
                                st.rerun()

                    except Exception as e:
                        st.error(f"❌ Execution failed: {e}")
                        log_error(e, context='approve_dry_run')
                        import traceback
                        with st.expander("🔍 Error Details"):
                            st.code(traceback.format_exc())

                else:  # Approve (Execute)
                    # Production mode - execute real actions
                    st.info("⚡ **EXECUTING REAL AWS ACTIONS...**")

                    try:
                        with st.spinner("⚡ Executing REAL AWS actions..."):
                            remediator = RemediatorProxy(dry_run=False)
                            results = remediator.execute_recommendations(conn, selected_ids)

                        # CRITICAL: Log production actions for audit trail
                        log_remediation_action(
                            action_type='approve_execute',
                            recommendation_ids=selected_ids,
                            result=results,
                            dry_run=False  # PRODUCTION - this is logged at WARNING level
                        )

                        # Display results
                        success_count = len([r for r in results if r.get('success', False)])
                        failed_count = len(results) - success_count

                        if success_count > 0:
                            st.success(f"✅ {success_count}/{len(results)} REAL actions executed!")

                        if failed_count > 0:
                            st.error(f"❌ {failed_count}/{len(results)} actions failed")

                        # Detailed results
                        st.markdown("### 📋 Execution Results")
                        for r in results:
                            with st.expander(f"{'✅' if r.get('success') else '❌'} {r.get('instance_id', 'unknown')}"):
                                st.json(r)

                        if success_count > 0:
                            st.balloons()
                            # Clear cache to ensure fresh data on next load
                            st.cache_data.clear()
                            st.info("💡 Check the History page for complete audit trail")
                            if st.button("🔄 Refresh Page", key="refresh_after_execute"):
                                st.rerun()

                    except Exception as e:
                        st.error(f"❌ Execution failed: {e}")
                        log_error(e, context='approve_execute_production')
                        import traceback
                        with st.expander("🔍 Error Details"):
                            st.code(traceback.format_exc())

        with col2:
            if st.button("📊 View Details", width="stretch"):
                if selected_ids:
                    st.markdown("### 📋 Selected Recommendations Details")
                    for rec_id in selected_ids:
                        rec_data = df[df['id'] == rec_id].iloc[0]
                        with st.expander(f"Recommendation #{rec_id}"):
                            st.write(f"**Instance:** {rec_data['resource_id']}")
                            st.write(f"**Type:** {rec_data['recommendation_type']}")
                            st.write(f"**Instance Type:** {rec_data.get('instance_type', 'N/A')}")

                            # Safely convert to float for formatting, handle NaN/None
                            cpu_avg = rec_data.get('cpu_avg', 0)
                            try:
                                cpu_avg = float(cpu_avg) if cpu_avg is not None and not pd.isna(cpu_avg) else 0.0
                            except (ValueError, TypeError):
                                cpu_avg = 0.0
                            st.write(f"**CPU Avg:** {cpu_avg:.2f}%")

                            savings = float(rec_data['estimated_monthly_savings_eur'])
                            st.write(f"**Savings:** €{savings:.2f}/month")

                            confidence = float(rec_data['confidence_score'])
                            st.write(f"**Confidence:** {confidence:.0%}")

        with col3:
            if st.button("🔄 Refresh Data", width="stretch"):
                st.cache_data.clear()
                st.rerun()

    else:
        st.warning("⚠️ Please select at least one recommendation")

st.markdown("---")

# Help section
with st.expander("ℹ️ Help - Understanding Recommendations"):
    st.markdown("""
    ### Recommendation Types

    - **stop_instance**: Stop the instance (reversible, no data loss)
    - **terminate_instance**: Permanently delete the instance (irreversible)
    - **downsize_instance**: Change to a smaller instance type

    ### Confidence Score

    - **0.9-1.0**: Very high confidence (safe to auto-apply)
    - **0.8-0.9**: High confidence (review recommended)
    - **0.7-0.8**: Medium confidence (manual review required)
    - **<0.7**: Low confidence (investigate before acting)

    ### Safety

    All recommendations are filtered through 7-layer safeguards before execution.
    Protected instances (production, critical tags) are never recommended.
    """)

# Add refresh button in sidebar
with st.sidebar:
    st.markdown("---")
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
