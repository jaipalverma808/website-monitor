"""
Database connection, schema migration, and query execution layer.
Supports shared PostgreSQL in production and SQLite for local development.
"""
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

from config import DATABASE_URL, IS_POSTGRES, SQLITE_PATH, FAILURE_THRESHOLD

# Initial website catalog required by the system
INITIAL_WEBSITES = [
    {
        "name": "SmartMineGuard",
        "website_url": "https://smartmineguard.onrender.com/",
        "health_url": "https://smartmineguard.onrender.com/health",
        "github_repo": "https://github.com/jaipalverma808/smartmineguard",
    },
    {
        "name": "ResumeForge",
        "website_url": "https://theresumeforge.onrender.com/",
        "health_url": "https://theresumeforge.onrender.com/health",
        "github_repo": "https://github.com/jaipalverma808/ResumeForge",
    },
    {
        "name": "Stories Hub",
        "website_url": "https://stories-hub.onrender.com/",
        "health_url": "https://stories-hub.onrender.com/health",
        "github_repo": "https://github.com/jaipalverma808/Stories-hub",
    },
    {
        "name": "Stories Hub Bot",
        "website_url": "https://stories-hub-bot-u57q.onrender.com/",
        "health_url": None,
        "github_repo": "https://github.com/jaipalverma808/stories-hub-bot",
    }
]


@contextmanager
def get_connection():
    """Yields a database connection and handles commit/rollback cleanly."""
    if IS_POSTGRES:
        try:
            conn = psycopg2.connect(DATABASE_URL)
        except Exception as conn_err:
            raise RuntimeError(
                f"FATAL: Could not establish connection to PostgreSQL. Error: {conn_err}. "
                "Please verify that DATABASE_URL is valid and network access is permitted."
            ) from conn_err
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(SQLITE_PATH))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def query_db(sql, params=(), one=False):
    """
    Executes a SELECT query and returns results as dictionaries.
    Uses %s placeholder syntax for cross-database compatibility.
    """
    with get_connection() as conn:
        if IS_POSTGRES:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            results = [dict(r) for r in rows]
        else:
            # Replace %s with ? for SQLite
            sqlite_sql = sql.replace("%s", "?")
            cursor = conn.cursor()
            cursor.execute(sqlite_sql, params)
            rows = cursor.fetchall()
            cursor.close()
            results = [dict(r) for r in rows]

        if one:
            return results[0] if results else None
        return results


def execute_db(sql, params=()):
    """
    Executes an INSERT, UPDATE, or DELETE statement.
    Returns the cursor rowcount or inserted id when applicable.
    """
    with get_connection() as conn:
        if IS_POSTGRES:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rowcount = cursor.rowcount
            cursor.close()
            return rowcount
        else:
            sqlite_sql = sql.replace("%s", "?")
            cursor = conn.cursor()
            cursor.execute(sqlite_sql, params)
            last_id = cursor.lastrowid
            cursor.close()
            return last_id


def init_db():
    """
    Initializes database tables and seeds the initial website catalog
    if the database is newly initialized.
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        if IS_POSTGRES:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS websites (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    website_url VARCHAR(500) NOT NULL,
                    health_url VARCHAR(500),
                    github_repo VARCHAR(500),
                    enabled BOOLEAN DEFAULT TRUE NOT NULL,
                    consecutive_failures INTEGER DEFAULT 0 NOT NULL,
                    last_status VARCHAR(20) DEFAULT 'UNKNOWN' NOT NULL,
                    last_http_status INTEGER,
                    last_response_time_ms INTEGER,
                    last_check_at TIMESTAMP,
                    last_success_at TIMESTAMP,
                    failure_threshold INTEGER DEFAULT 3 NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                );

                CREATE TABLE IF NOT EXISTS check_logs (
                    id BIGSERIAL PRIMARY KEY,
                    website_id INTEGER NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    is_success BOOLEAN NOT NULL,
                    http_status INTEGER,
                    response_time_ms INTEGER,
                    target_url VARCHAR(500) NOT NULL,
                    error_message TEXT,
                    status_result VARCHAR(20) NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_check_logs_site_time 
                ON check_logs(website_id, checked_at DESC);
            """)
        else:
            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS websites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(120) NOT NULL,
                    website_url VARCHAR(500) NOT NULL,
                    health_url VARCHAR(500),
                    github_repo VARCHAR(500),
                    enabled BOOLEAN DEFAULT 1 NOT NULL,
                    consecutive_failures INTEGER DEFAULT 0 NOT NULL,
                    last_status VARCHAR(20) DEFAULT 'UNKNOWN' NOT NULL,
                    last_http_status INTEGER,
                    last_response_time_ms INTEGER,
                    last_check_at TIMESTAMP,
                    last_success_at TIMESTAMP,
                    failure_threshold INTEGER DEFAULT 3 NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                );

                CREATE TABLE IF NOT EXISTS check_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    website_id INTEGER NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    is_success BOOLEAN NOT NULL,
                    http_status INTEGER,
                    response_time_ms INTEGER,
                    target_url VARCHAR(500) NOT NULL,
                    error_message TEXT,
                    status_result VARCHAR(20) NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_check_logs_site_time 
                ON check_logs(website_id, checked_at DESC);
            """)

        cursor.close()

    # Seed default websites if none exist
    existing = query_db("SELECT COUNT(*) as count FROM websites", one=True)
    if existing and existing.get("count", 0) == 0:
        for site in INITIAL_WEBSITES:
            execute_db("""
                INSERT INTO websites (
                    name, website_url, health_url, github_repo, 
                    enabled, consecutive_failures, last_status, failure_threshold
                ) VALUES (%s, %s, %s, %s, %s, 0, 'UNKNOWN', %s)
            """, (
                site["name"], site["website_url"], site["health_url"],
                site["github_repo"], True, FAILURE_THRESHOLD
            ))
