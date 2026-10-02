"""
Data access objects and business helpers for websites and monitoring logs.
Kept simple, clear, and easy to maintain.
"""
from datetime import datetime, timezone
from database import query_db, execute_db
from config import FAILURE_THRESHOLD


def get_all_websites(include_disabled=True):
    """Retrieves all monitored websites."""
    if include_disabled:
        return query_db("SELECT * FROM websites ORDER BY id ASC")
    return query_db("SELECT * FROM websites WHERE enabled ORDER BY id ASC")


def get_website_by_id(site_id):
    """Retrieves a single website by primary key ID."""
    return query_db("SELECT * FROM websites WHERE id = %s", (site_id,), one=True)


def add_website(name, website_url, health_url=None, github_repo=None, failure_threshold=None):
    """Adds a new website to be monitored."""
    threshold = failure_threshold or FAILURE_THRESHOLD
    website_url = website_url.strip()
    health_url = health_url.strip() if health_url else None
    github_repo = github_repo.strip() if github_repo else None

    return execute_db("""
        INSERT INTO websites (
            name, website_url, health_url, github_repo, 
            enabled, consecutive_failures, last_status, failure_threshold
        ) VALUES (%s, %s, %s, %s, true, 0, 'UNKNOWN', %s)
    """, (name.strip(), website_url, health_url, github_repo, threshold))


def update_website(site_id, name, website_url, health_url=None, github_repo=None, failure_threshold=None, enabled=True):
    """Updates an existing website's configuration."""
    threshold = failure_threshold or FAILURE_THRESHOLD
    website_url = website_url.strip()
    health_url = health_url.strip() if health_url else None
    github_repo = github_repo.strip() if github_repo else None

    return execute_db("""
        UPDATE websites SET 
            name = %s,
            website_url = %s,
            health_url = %s,
            github_repo = %s,
            failure_threshold = %s,
            enabled = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (name.strip(), website_url, health_url, github_repo, threshold, enabled, site_id))


def toggle_website(site_id):
    """Toggles the enabled status of a website."""
    site = get_website_by_id(site_id)
    if not site:
        return False
    new_state = not site.get("enabled", True)
    execute_db("UPDATE websites SET enabled = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (new_state, site_id))
    return new_state


def delete_website(site_id):
    """Deletes a website and cascades deletion of its logs."""
    return execute_db("DELETE FROM websites WHERE id = %s", (site_id,))


def record_check_result(website_id, is_success, http_status, response_time_ms, target_url, error_message, status_result):
    """
    Inserts a check log entry and updates the website's aggregated status:
    - If success: resets consecutive_failures to 0, sets last_success_at
    - If failure: increments consecutive_failures
    - Sets last_status, last_http_status, last_response_time_ms, last_check_at
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Insert history log
    execute_db("""
        INSERT INTO check_logs (
            website_id, checked_at, is_success, http_status, 
            response_time_ms, target_url, error_message, status_result
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        website_id, now, is_success, http_status, 
        response_time_ms, target_url, error_message, status_result
    ))

    # 2. Update website table
    site = get_website_by_id(website_id)
    current_failures = site.get("consecutive_failures", 0) if site else 0

    if is_success:
        new_failures = 0
        execute_db("""
            UPDATE websites SET 
                consecutive_failures = %s,
                last_status = %s,
                last_http_status = %s,
                last_response_time_ms = %s,
                last_check_at = %s,
                last_success_at = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (new_failures, status_result, http_status, response_time_ms, now, now, website_id))
    else:
        new_failures = current_failures + 1
        execute_db("""
            UPDATE websites SET 
                consecutive_failures = %s,
                last_status = %s,
                last_http_status = %s,
                last_response_time_ms = %s,
                last_check_at = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (new_failures, status_result, http_status, response_time_ms, now, website_id))


def get_recent_logs(website_id=None, limit=50):
    """Retrieves recent check logs for a specific website or across all websites."""
    if website_id:
        return query_db("""
            SELECT l.*, w.name as website_name 
            FROM check_logs l
            JOIN websites w ON w.id = l.website_id
            WHERE l.website_id = %s
            ORDER BY l.checked_at DESC
            LIMIT %s
        """, (website_id, limit))
    else:
        return query_db("""
            SELECT l.*, w.name as website_name 
            FROM check_logs l
            JOIN websites w ON w.id = l.website_id
            ORDER BY l.checked_at DESC
            LIMIT %s
        """, (limit,))


def get_website_stats(site_id):
    """Calculates total checks, successful checks, failed checks, uptime %, and avg response time."""
    totals = query_db("""
        SELECT 
            COUNT(*) as total_checks,
            SUM(CASE WHEN is_success THEN 1 ELSE 0 END) as successful_checks,
            SUM(CASE WHEN NOT is_success THEN 1 ELSE 0 END) as failed_checks,
            AVG(CASE WHEN is_success THEN response_time_ms ELSE NULL END) as avg_response_time
        FROM check_logs
        WHERE website_id = %s
    """, (site_id,), one=True)

    total = (totals.get("total_checks") or 0) if totals else 0
    successful = (totals.get("successful_checks") or 0) if totals else 0
    failed = (totals.get("failed_checks") or 0) if totals else 0
    avg_resp = (totals.get("avg_response_time") or 0) if totals else 0

    uptime_pct = round((successful / total * 100), 2) if total > 0 else 100.0

    return {
        "total_checks": total,
        "successful_checks": successful,
        "failed_checks": failed,
        "uptime_percentage": uptime_pct,
        "avg_response_time_ms": round(avg_resp) if avg_resp else 0
    }


def get_dashboard_summary():
    """Calculates count of Total, Online, Warning, Offline, and Disabled websites."""
    sites = get_all_websites(include_disabled=True)
    summary = {
        "total": len(sites),
        "online": 0,
        "warning": 0,
        "offline": 0,
        "disabled": 0,
        "unknown": 0
    }
    for site in sites:
        if not site.get("enabled", True):
            summary["disabled"] += 1
        else:
            status = (site.get("last_status") or "UNKNOWN").upper()
            if status == "ONLINE":
                summary["online"] += 1
            elif status == "WARNING":
                summary["warning"] += 1
            elif status == "OFFLINE":
                summary["offline"] += 1
            else:
                summary["unknown"] += 1
    return summary
