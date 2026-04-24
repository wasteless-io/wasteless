"""
Database connection utilities for Wasteless.

Provides centralized database connection management
with connection pooling for all Wasteless modules.
"""

import os
import sys
import logging
import atexit
from typing import Optional, List, Tuple, Any, Union
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool, DatabaseError, OperationalError
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Global connection pool
_connection_pool: Optional[pool.ThreadedConnectionPool] = None


class DatabaseError(Exception):
    """Custom exception for database operations."""
    pass


def _get_db_config() -> dict:
    """
    Get database configuration from environment variables.

    Returns:
        dict: Database configuration parameters

    Raises:
        ValueError: If required environment variables are missing
    """
    required_vars = ['DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
    missing = [var for var in required_vars if not os.getenv(var)]

    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return {
        'host': os.getenv('DB_HOST'),
        'port': int(os.getenv('DB_PORT')),
        'database': os.getenv('DB_NAME'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'connect_timeout': 10,
    }


def init_connection_pool(min_connections: int = 2, max_connections: int = 10) -> pool.ThreadedConnectionPool:
    """
    Initialize the database connection pool.

    Args:
        min_connections: Minimum number of connections to keep open
        max_connections: Maximum number of connections allowed

    Returns:
        ThreadedConnectionPool: The initialized connection pool

    Raises:
        DatabaseError: If pool initialization fails
    """
    global _connection_pool

    if _connection_pool is not None:
        return _connection_pool

    try:
        config = _get_db_config()
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=min_connections,
            maxconn=max_connections,
            **config
        )
        logger.info(f"Database connection pool initialized (min={min_connections}, max={max_connections})")
        return _connection_pool

    except (OperationalError, psycopg2.Error) as e:
        logger.error(f"Failed to initialize connection pool: {e}")
        raise DatabaseError(f"Could not initialize database pool: {e}") from e


def get_connection_pool() -> pool.ThreadedConnectionPool:
    """
    Get the connection pool, initializing it if necessary.

    Returns:
        ThreadedConnectionPool: The database connection pool
    """
    global _connection_pool

    if _connection_pool is None:
        init_connection_pool()

    return _connection_pool


def get_db_connection():
    """
    Get a database connection from the pool.

    Returns:
        psycopg2.connection: Database connection object

    Raises:
        DatabaseError: If connection cannot be obtained
    """
    try:
        pool = get_connection_pool()
        conn = pool.getconn()

        if conn is None:
            raise DatabaseError("Could not get connection from pool")

        return conn

    except (OperationalError, psycopg2.Error) as e:
        logger.error(f"Failed to get database connection: {e}")
        raise DatabaseError(f"Could not get database connection: {e}") from e


def release_connection(conn) -> None:
    """
    Return a connection to the pool.

    Args:
        conn: The connection to release
    """
    if conn is not None and _connection_pool is not None:
        try:
            _connection_pool.putconn(conn)
        except Exception as e:
            logger.warning(f"Error releasing connection: {e}")


@contextmanager
def get_connection():
    """
    Context manager for database connections.
    Automatically returns connection to pool after use.

    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM table")

    Yields:
        psycopg2.connection: Database connection
    """
    conn = None
    try:
        conn = get_db_connection()
        yield conn
    finally:
        release_connection(conn)


@contextmanager
def get_cursor(dict_cursor: bool = False):
    """
    Context manager for database cursor with automatic connection management.

    Args:
        dict_cursor: If True, return results as dictionaries

    Usage:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM table")
            results = cursor.fetchall()

    Yields:
        psycopg2.cursor: Database cursor
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor_factory = RealDictCursor if dict_cursor else None
        cursor = conn.cursor(cursor_factory=cursor_factory)
        yield cursor
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        release_connection(conn)


def execute_query(
    query: str,
    params: Optional[Union[Tuple, List]] = None,
    fetch_one: bool = False,
    fetch_all: bool = False
) -> Optional[Any]:
    """
    Execute a SQL query with automatic connection management.

    Args:
        query: SQL query to execute
        params: Query parameters (tuple or list)
        fetch_one: If True, return one result
        fetch_all: If True, return all results

    Returns:
        Query result depending on fetch flags, or None for non-SELECT queries

    Raises:
        DatabaseError: If query execution fails

    Example:
        # Fetch all
        results = execute_query("SELECT * FROM waste_detected", fetch_all=True)

        # Fetch one
        result = execute_query(
            "SELECT * FROM waste_detected WHERE id = %s",
            (1,),
            fetch_one=True
        )

        # Insert/Update (no fetch)
        execute_query(
            "UPDATE recommendations SET status = %s WHERE id = %s",
            ('completed', 1)
        )
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)

        if fetch_one:
            result = cursor.fetchone()
        elif fetch_all:
            result = cursor.fetchall()
        else:
            result = None

        conn.commit()
        return result

    except psycopg2.IntegrityError as e:
        if conn:
            conn.rollback()
        logger.error(f"Integrity error executing query: {e}")
        raise DatabaseError(f"Integrity constraint violation: {e}") from e

    except psycopg2.ProgrammingError as e:
        if conn:
            conn.rollback()
        logger.error(f"Programming error in query: {e}")
        raise DatabaseError(f"SQL syntax or programming error: {e}") from e

    except (OperationalError, psycopg2.Error) as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error executing query: {e}")
        raise DatabaseError(f"Database operation failed: {e}") from e

    finally:
        if cursor:
            cursor.close()
        release_connection(conn)


def execute_many(
    query: str,
    params_list: List[Tuple]
) -> int:
    """
    Execute a query multiple times with different parameters.
    More efficient than calling execute_query in a loop.

    Args:
        query: SQL query to execute
        params_list: List of parameter tuples

    Returns:
        Number of rows affected

    Raises:
        DatabaseError: If execution fails
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.executemany(query, params_list)
        rowcount = cursor.rowcount
        conn.commit()
        return rowcount

    except (OperationalError, psycopg2.Error) as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error executing batch query: {e}")
        raise DatabaseError(f"Batch operation failed: {e}") from e

    finally:
        if cursor:
            cursor.close()
        release_connection(conn)


def close_pool() -> None:
    """
    Close all connections in the pool.
    Should be called when shutting down the application.
    """
    global _connection_pool

    if _connection_pool is not None:
        try:
            _connection_pool.closeall()
            logger.info("Database connection pool closed")
        except Exception as e:
            logger.warning(f"Error closing connection pool: {e}")
        finally:
            _connection_pool = None


# Register cleanup on application exit
atexit.register(close_pool)


def health_check() -> bool:
    """
    Check if database is reachable.

    Returns:
        True if database is healthy, False otherwise
    """
    try:
        result = execute_query("SELECT 1", fetch_one=True)
        return result is not None and result[0] == 1
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False
