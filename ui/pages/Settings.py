#!/usr/bin/env python3
"""
Settings Page
=============

Manage safeguards and auto-remediation configuration.
Optimized with cached queries for better performance.
"""

import streamlit as st
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(
    page_title="Settings - Wasteless.io",
    page_icon="static/images/favicon.svg",
    layout="wide"
)

from utils.page_transition import transition_on_first_load
from utils.sidebar import setup_sidebar
from utils.config_manager import ConfigManager
from utils.logger import get_logger, log_db_query, log_error

# Show page transition on first load
transition_on_first_load("Settings")

# Cached data fetching functions
@st.cache_data(ttl=60)
def fetch_database_stats(_conn):
    """
    Fetch database table counts in a single optimized query.
    Uses CTE to fetch all counts efficiently. Cached for 60 seconds.
    Returns dict with table counts or None on error.
    """
    logger = get_logger('data')
    start_time = time.time()

    try:
        query = """
            WITH counts AS (
                SELECT
                    (SELECT COUNT(*) FROM ec2_metrics) as ec2_metrics,
                    (SELECT COUNT(*) FROM waste_detected) as waste_detected,
                    (SELECT COUNT(*) FROM recommendations) as recommendations,
                    (SELECT COUNT(*) FROM actions_log) as actions_log,
                    (SELECT COUNT(*) FROM savings_realized) as savings_realized
            )
            SELECT * FROM counts;
        """

        cursor = _conn.cursor()
        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()

        if result:
            duration_ms = (time.time() - start_time) * 1000
            log_db_query('fetch_database_stats', duration_ms, success=True)
            return {
                'ec2_metrics': int(result[0]),
                'waste_detected': int(result[1]),
                'recommendations': int(result[2]),
                'actions_log': int(result[3]),
                'savings_realized': int(result[4])
            }
        return None

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_db_query('fetch_database_stats', duration_ms, success=False)
        log_error(e, context='fetch_database_stats')
        st.error(f"❌ Failed to fetch database stats: {e}")
        return None

st.title("⚙️ Settings & Configuration")
st.markdown("Manage auto-remediation policies and safeguards")
st.markdown("---")

# Initialize config manager
config_manager = ConfigManager()

# Check if config file is accessible
if not config_manager.is_config_file_accessible():
    st.error(f"""
    ❌ **Configuration file not accessible**

    Expected location: `{config_manager.config_path}`

    Make sure the backend repository is cloned and accessible.
    """)
    st.stop()

# Setup sidebar and get database connection
conn = setup_sidebar()
if not conn:
    st.error("❌ Cannot connect to database")
    st.stop()

# Load current config
try:
    config = config_manager.load_config()
except Exception as e:
    st.error(f"❌ Failed to load configuration: {e}")
    st.stop()

# =============================================================================
# AUTO-REMEDIATION CONTROLS
# =============================================================================

st.subheader("🤖 Auto-Remediation Controls")

col1, col2 = st.columns([2, 1])

with col1:
    auto_enabled = config.get('auto_remediation', {}).get('enabled', False)

    st.markdown("### Master Switch")

    new_enabled = st.toggle(
        "Enable Auto-Remediation",
        value=auto_enabled,
        help="⚠️ When enabled, the system will execute REAL AWS actions (stop/terminate instances). When disabled, only dry-run mode is active.",
        key="auto_remediation_toggle"
    )

    # Show status based on toggle
    if new_enabled:
        st.error("⚠️ **AUTO-REMEDIATION IS ENABLED** - Real AWS actions will be executed!")
    else:
        st.success("✅ **AUTO-REMEDIATION IS DISABLED** - Only dry-run mode is active (safe)")

    # Save if changed
    if new_enabled != auto_enabled:
        with st.spinner("Saving configuration..."):
            if config_manager.set_auto_remediation_enabled(new_enabled):
                st.success("✅ Configuration saved successfully!")
                st.rerun()
            else:
                st.error("❌ Failed to save configuration")

with col2:
    dry_run_days = config.get('auto_remediation', {}).get('dry_run_days', 7)
    st.metric(
        "Mandatory Dry-Run Period",
        f"{dry_run_days} days",
        help="New recommendations must be in dry-run for this many days before execution"
    )

    # Allow editing dry-run days
    with st.expander("⚙️ Modify Dry-Run Period"):
        new_dry_run_days = st.number_input(
            "Days",
            min_value=0,
            max_value=30,
            value=dry_run_days,
            help="Set to 0 to disable mandatory dry-run period (not recommended)"
        )

        if st.button("💾 Save Dry-Run Period"):
            try:
                from utils.config_manager import ConfigValidationError
                if config_manager.set_dry_run_days(new_dry_run_days):
                    st.success("✅ Dry-run period updated!")
                    st.rerun()
                else:
                    st.error("❌ Failed to update")
            except ConfigValidationError as e:
                st.error(f"❌ Invalid value: {e}")
            except Exception as e:
                st.error(f"❌ Error: {e}")

st.markdown("---")

# =============================================================================
# SAFEGUARDS CONFIGURATION
# =============================================================================

st.subheader("🛡️ Safeguard Configuration")

protection = config.get('protection', {})

col1, col2, col3 = st.columns(3)

with col1:
    min_age = protection.get('min_instance_age_days', 30)
    st.metric(
        "Min Instance Age",
        f"{min_age} days",
        help="Instances must be older than this to be eligible for remediation"
    )

    with st.expander("⚙️ Edit"):
        new_min_age = st.slider("Days", 1, 90, min_age, key="min_age_slider")
        if st.button("💾 Save", key="save_min_age"):
            try:
                from utils.config_manager import ConfigValidationError
                if config_manager.update_protection_rule('min_instance_age_days', new_min_age):
                    st.success("✅ Updated!")
                    st.rerun()
            except ConfigValidationError as e:
                st.error(f"❌ Invalid value: {e}")

with col2:
    min_idle = protection.get('min_idle_days', 14)
    st.metric(
        "Min Idle Days",
        f"{min_idle} days",
        help="Instance must be idle for this long before remediation"
    )

    with st.expander("⚙️ Edit"):
        new_min_idle = st.slider("Days", 1, 60, min_idle, key="min_idle_slider")
        if st.button("💾 Save", key="save_min_idle"):
            try:
                from utils.config_manager import ConfigValidationError
                if config_manager.update_protection_rule('min_idle_days', new_min_idle):
                    st.success("✅ Updated!")
                    st.rerun()
            except ConfigValidationError as e:
                st.error(f"❌ Invalid value: {e}")

with col3:
    min_confidence = protection.get('min_confidence_score', 0.8)
    st.metric(
        "Min Confidence",
        f"{min_confidence:.0%}",
        help="Detection confidence threshold"
    )

    with st.expander("⚙️ Edit"):
        new_min_confidence = st.slider(
            "Confidence",
            0.0, 1.0, min_confidence,
            step=0.05,
            key="min_confidence_slider",
            format="%.0%"
        )
        if st.button("💾 Save", key="save_min_confidence"):
            try:
                from utils.config_manager import ConfigValidationError
                if config_manager.update_protection_rule('min_confidence_score', new_min_confidence):
                    st.success("✅ Updated!")
                    st.rerun()
            except ConfigValidationError as e:
                st.error(f"❌ Invalid value: {e}")

col1, col2 = st.columns(2)

with col1:
    max_per_run = protection.get('max_instances_per_run', 3)
    st.metric(
        "Max Instances Per Run",
        max_per_run,
        help="Blast radius control - limit simultaneous actions"
    )

    with st.expander("⚙️ Edit"):
        new_max_per_run = st.number_input(
            "Max instances",
            min_value=1,
            max_value=50,
            value=max_per_run,
            key="max_per_run_input"
        )
        if st.button("💾 Save", key="save_max_per_run"):
            if config_manager.update_protection_rule('max_instances_per_run', new_max_per_run):
                st.success("✅ Updated!")
                st.rerun()

st.markdown("---")

# =============================================================================
# WHITELIST MANAGEMENT
# =============================================================================

st.subheader("🔒 Protected Resources")

whitelist = config.get('whitelist', {})

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Protected Instance IDs:**")
    instance_ids = whitelist.get('instance_ids', [])

    if instance_ids:
        for instance_id in instance_ids:
            col_id, col_btn = st.columns([3, 1])
            with col_id:
                st.code(instance_id)
            with col_btn:
                if st.button("🗑️", key=f"remove_{instance_id}"):
                    if config_manager.remove_instance_from_whitelist(instance_id):
                        st.success(f"✅ Removed {instance_id}")
                        st.rerun()
    else:
        st.info("No specific instances whitelisted")

    # Add new instance
    with st.expander("➕ Add Instance to Whitelist"):
        new_instance_id = st.text_input("Instance ID", placeholder="i-1234567890abcdef0")
        if st.button("➕ Add to Whitelist"):
            if new_instance_id:
                try:
                    from utils.config_manager import ConfigValidationError
                    if config_manager.add_instance_to_whitelist(new_instance_id):
                        st.success(f"✅ Added {new_instance_id} to whitelist")
                        st.rerun()
                except ConfigValidationError as e:
                    st.error(f"❌ Invalid instance ID: {e}")
                except Exception as e:
                    st.error(f"❌ Failed to add instance: {e}")
            else:
                st.warning("Please enter an instance ID")

with col2:
    st.markdown("**Protected Tags:**")
    tags = whitelist.get('tags', [])
    if tags:
        for tag in tags:
            st.code(f"{tag.get('key')} = {tag.get('value')}")
    else:
        st.info("No tag-based protection configured")

    st.caption("💡 To modify tags, edit the YAML file directly")

st.markdown("---")

# =============================================================================
# SCHEDULE
# =============================================================================

st.subheader("⏰ Execution Schedule")

schedule = config.get('schedule', {})

col1, col2, col3 = st.columns(3)

with col1:
    allowed_days = schedule.get('allowed_days', [])
    st.markdown("**Allowed Days:**")
    if allowed_days:
        for day in allowed_days:
            st.write(f"• {day}")
    else:
        st.info("No restrictions")

with col2:
    allowed_hours = schedule.get('allowed_hours', [])
    st.markdown("**Allowed Hours:**")
    if allowed_hours:
        hours_str = ', '.join([f"{h}:00" for h in allowed_hours])
        st.write(hours_str)
    else:
        st.info("No restrictions")

with col3:
    timezone = schedule.get('timezone', 'UTC')
    st.metric("Timezone", timezone)

st.caption("💡 To modify schedule, edit the YAML file directly")

st.markdown("---")

# =============================================================================
# NOTIFICATIONS
# =============================================================================

st.subheader("📧 Notifications")

notifications = config.get('notifications', {})

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Email:**")
    email = notifications.get('email', '')
    if email:
        st.code(email)
    else:
        st.info("No email configured")

with col2:
    st.markdown("**Slack Webhook:**")
    slack = notifications.get('slack_webhook', '')
    if slack:
        st.code("Configured ✅")
    else:
        st.info("Not configured")

notify_before = notifications.get('notify_before_action', False)
notify_after = notifications.get('notify_after_action', False)
notify_error = notifications.get('notify_on_error', False)

col1, col2, col3 = st.columns(3)
with col1:
    st.checkbox("Notify before action", value=notify_before, disabled=True)
with col2:
    st.checkbox("Notify after action", value=notify_after, disabled=True)
with col3:
    st.checkbox("Notify on error", value=notify_error, disabled=True)

st.caption("💡 To modify notifications, edit the YAML file directly")

st.markdown("---")

# =============================================================================
# DATABASE STATS
# =============================================================================

st.subheader("💾 Database Statistics")

with st.spinner("Loading database statistics..."):
    stats = fetch_database_stats(conn)

if stats:
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Metrics", f"{stats['ec2_metrics']:,}")

    with col2:
        st.metric("Waste", stats['waste_detected'])

    with col3:
        st.metric("Recommendations", stats['recommendations'])

    with col4:
        st.metric("Actions", stats['actions_log'])

    with col5:
        st.metric("Savings", stats['savings_realized'])
else:
    st.warning("⚠️ Unable to load database statistics")

st.markdown("---")

# =============================================================================
# HELP SECTION
# =============================================================================

with st.expander("ℹ️ Help - Understanding Settings"):
    st.markdown("""
    ### Auto-Remediation Settings

    **Master Switch (Enable/Disable)**
    - **Disabled (default)**: System runs in dry-run mode only - safe, no real AWS actions
    - **Enabled**: System will execute real AWS actions (stop/terminate instances)

    **Dry-Run Period**
    - New recommendations must wait this many days before real execution
    - Gives you time to review and reject false positives
    - Set to 0 to disable (not recommended)

    ### Safeguards

    **Min Instance Age**: Protects newly created instances
    **Min Idle Days**: Ensures instance has been idle long enough
    **Min Confidence**: Detection confidence threshold (higher = safer)
    **Max Per Run**: Limits blast radius

    ### Whitelist

    Add critical instances to the whitelist to ensure they're NEVER touched by auto-remediation.

    ### Important Notes

    - ⚠️ Always start with auto-remediation **disabled**
    - Test with dry-run mode first
    - Monitor the first few real executions closely
    - Whitelist all production-critical instances
    - Start with conservative safeguards, relax gradually
    """)

# Config file path info
st.caption(f"📁 Configuration file: `{config_manager.config_path}`")

# Add refresh button in sidebar
with st.sidebar:
    st.markdown("---")
    if st.button("🔄 Refresh Settings", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
