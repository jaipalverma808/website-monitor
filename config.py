"""
Configuration module for Website Uptime + Keep-Alive Monitoring System.
Reads environment variables with sensible production and local fallbacks.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Database URL: Handles Render's postgres:// syntax automatically
RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

# Default to local SQLite if no PostgreSQL URL is configured,
# but enforce PostgreSQL when REQUIRE_POSTGRES is enabled.
REQUIRE_POSTGRES = os.getenv("REQUIRE_POSTGRES", "false").lower() in ("true", "1", "yes")
IS_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgresql://"))

if REQUIRE_POSTGRES and not IS_POSTGRES:
    raise RuntimeError(
        "FATAL: Production monitoring requires a valid PostgreSQL DATABASE_URL. "
        "SQLite fallback is disabled when REQUIRE_POSTGRES=true. "
        "Please ensure DATABASE_URL is properly configured in your environment or GitHub Secrets."
    )

SQLITE_PATH = BASE_DIR / "data" / "monitor.db"

# Flask Settings
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-website-monitor-change-in-prod")
PORT = int(os.getenv("PORT", "5000"))

# Monitoring Defaults
# Free Render instances can take 25-40s to wake from cold sleep.
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "35"))
FAILURE_THRESHOLD = int(os.getenv("FAILURE_THRESHOLD", "3"))

# HTTP Client Identity
USER_AGENT = "Website-Monitor-Bot/1.0 (+https://github.com/jaipalverma808)"

# Optional Dashboard Protection
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
AUTH_REQUIRED = bool(ADMIN_PASSWORD)
