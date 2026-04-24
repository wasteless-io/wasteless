"""
Database connection utility for Wasteless UI
"""
import os
import streamlit as st
import psycopg2
from pathlib import Path
from dotenv import load_dotenv


# Ensure environment variables are loaded
APP_DIR = Path(__file__).parent.parent
ENV_PATH = APP_DIR / '.env'
load_dotenv(dotenv_path=ENV_PATH)


# List of insecure/placeholder passwords that should not be used
_INSECURE_PASSWORDS = {
    'CHANGE_ME_USE_STRONG_PASSWORD',
    'password',
    'admin',
    '123456',
    'wasteless',
    '',
}


@st.cache_resource
def get_db_connection():
    """Get PostgreSQL database connection."""
    # Get database credentials
    db_host = os.getenv('DB_HOST', 'localhost')
    db_port = os.getenv('DB_PORT', '5432')
    db_name = os.getenv('DB_NAME', 'wasteless')
    db_user = os.getenv('DB_USER', 'wasteless')
    db_password = os.getenv('DB_PASSWORD')

    # Check if password is set
    if not db_password:
        st.error("❌ DB_PASSWORD environment variable is not set")
        st.info("💡 Please set DB_PASSWORD in your .env file for security")
        return None

    # Warn about insecure passwords (don't block, just warn)
    if db_password in _INSECURE_PASSWORDS:
        st.warning(
            "⚠️ **Insecure Password Detected**\n\n"
            "You are using a placeholder or weak password. "
            "Please set a strong, unique password in your `.env` file before deploying to production."
        )

    try:
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_password
        )
        return conn
    except Exception as e:
        st.error(f"❌ Database connection failed: {e}")
        st.info(f"💡 Connection details: host={db_host}, port={db_port}, database={db_name}, user={db_user}")
        st.info("💡 Make sure PostgreSQL is running and credentials are correct in .env")
        return None
