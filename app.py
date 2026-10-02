"""
Flask Web Dashboard for Website Uptime + Keep-Alive Monitoring System.
Provides a clean, human-designed developer interface to view status,
inspect history, add/edit/test websites, and configure the system.
"""
from datetime import datetime, timezone
from functools import wraps
from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, flash, session
)

from config import (
    SECRET_KEY, PORT, ADMIN_USERNAME, ADMIN_PASSWORD,
    AUTH_REQUIRED, IS_POSTGRES, REQUEST_TIMEOUT, FAILURE_THRESHOLD
)
from database import init_db
from models import (
    get_all_websites, get_website_by_id, add_website,
    update_website, delete_website, toggle_website,
    get_recent_logs, get_website_stats, get_dashboard_summary
)
from monitor import check_single_website, run_monitor

app = Flask(__name__)
app.secret_key = SECRET_KEY


# ------------------------------------------------------------------------------
# Security & Authentication Helpers
# ------------------------------------------------------------------------------

def admin_required(f):
    """Protects modification actions if an admin password is configured."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not AUTH_REQUIRED:
            return f(*args, **kwargs)
        if not session.get("is_admin"):
            if request.is_json:
                return jsonify({"success": False, "error": "Authentication required"}), 401
            flash("Admin login required to perform this action.", "warning")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


@app.after_request
def set_security_headers(response):
    """Applies sensible standard security headers."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ------------------------------------------------------------------------------
# Jinja Template Filters
# ------------------------------------------------------------------------------

@app.template_filter("time_ago")
def time_ago_filter(dt):
    """Formats a datetime into a human-friendly relative string."""
    if not dt:
        return "Never"

    if isinstance(dt, str):
        try:
            # Handle standard ISO formats
            cleaned = dt.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
        except Exception:
            return str(dt)[:19]

    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt
    total_seconds = int(diff.total_seconds())

    if total_seconds < 0:
        return "just now"
    if total_seconds < 60:
        return f"{total_seconds}s ago"
    if total_seconds < 3600:
        mins = total_seconds // 60
        return f"{mins} min ago" if mins == 1 else f"{mins} mins ago"
    if total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    days = total_seconds // 86400
    return f"{days} day ago" if days == 1 else f"{days} days ago"


@app.template_filter("format_date")
def format_date_filter(dt):
    """Formats a datetime into YYYY-MM-DD HH:MM:SS UTC."""
    if not dt:
        return "-"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return str(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ------------------------------------------------------------------------------
# Dashboard Routes
# ------------------------------------------------------------------------------

@app.route("/")
def dashboard():
    """Renders the main monitoring dashboard overview."""
    init_db()
    websites = get_all_websites(include_disabled=True)
    summary = get_dashboard_summary()
    recent_logs = get_recent_logs(limit=10)

    return render_template(
        "dashboard.html",
        websites=websites,
        summary=summary,
        recent_logs=recent_logs,
        auth_required=AUTH_REQUIRED,
        is_admin=session.get("is_admin", False),
        is_postgres=IS_POSTGRES
    )


@app.route("/website/<int:site_id>")
def website_details(site_id):
    """Renders the detailed view and history for a single monitored website."""
    site = get_website_by_id(site_id)
    if not site:
        flash(f"Website ID {site_id} not found.", "error")
        return redirect(url_for("dashboard"))

    stats = get_website_stats(site_id)
    logs = get_recent_logs(website_id=site_id, limit=50)

    return render_template(
        "website.html",
        site=site,
        stats=stats,
        logs=logs,
        auth_required=AUTH_REQUIRED,
        is_admin=session.get("is_admin", False)
    )


@app.route("/settings")
def settings():
    """Renders system architecture, schedule details, and configuration status."""
    return render_template(
        "settings.html",
        is_postgres=IS_POSTGRES,
        request_timeout=REQUEST_TIMEOUT,
        failure_threshold=FAILURE_THRESHOLD,
        auth_required=AUTH_REQUIRED,
        is_admin=session.get("is_admin", False)
    )


# ------------------------------------------------------------------------------
# Action APIs & Form Handlers
# ------------------------------------------------------------------------------

@app.route("/api/websites", methods=["POST"])
@admin_required
def api_add_website():
    """Adds a new website to the database."""
    name = request.form.get("name", "").strip()
    website_url = request.form.get("website_url", "").strip()
    health_url = request.form.get("health_url", "").strip()
    github_repo = request.form.get("github_repo", "").strip()
    try:
        failure_threshold = int(request.form.get("failure_threshold") or FAILURE_THRESHOLD)
    except ValueError:
        failure_threshold = FAILURE_THRESHOLD

    if not name or not website_url:
        flash("Website name and main URL are required.", "error")
        return redirect(request.referrer or url_for("dashboard"))

    if not (website_url.startswith("http://") or website_url.startswith("https://")):
        flash("Website URL must start with http:// or https://", "error")
        return redirect(request.referrer or url_for("dashboard"))

    try:
        new_id = add_website(
            name=name,
            website_url=website_url,
            health_url=health_url if health_url else None,
            github_repo=github_repo if github_repo else None,
            failure_threshold=failure_threshold
        )
        flash(f"Website '{name}' added successfully!", "success")
    except Exception as e:
        flash(f"Error adding website: {e}", "error")

    return redirect(url_for("dashboard"))


@app.route("/api/websites/<int:site_id>/edit", methods=["POST"])
@admin_required
def api_edit_website(site_id):
    """Updates an existing website."""
    site = get_website_by_id(site_id)
    if not site:
        flash("Website not found.", "error")
        return redirect(url_for("dashboard"))

    name = request.form.get("name", "").strip()
    website_url = request.form.get("website_url", "").strip()
    health_url = request.form.get("health_url", "").strip()
    github_repo = request.form.get("github_repo", "").strip()
    enabled = request.form.get("enabled") == "on"

    try:
        failure_threshold = int(request.form.get("failure_threshold") or FAILURE_THRESHOLD)
    except ValueError:
        failure_threshold = FAILURE_THRESHOLD

    if not name or not website_url:
        flash("Website name and URL are required.", "error")
        return redirect(url_for("website_details", site_id=site_id))

    try:
        update_website(
            site_id=site_id,
            name=name,
            website_url=website_url,
            health_url=health_url if health_url else None,
            github_repo=github_repo if github_repo else None,
            failure_threshold=failure_threshold,
            enabled=enabled
        )
        flash(f"Website '{name}' updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating website: {e}", "error")

    return redirect(url_for("website_details", site_id=site_id))


@app.route("/api/websites/<int:site_id>/toggle", methods=["POST"])
@admin_required
def api_toggle_website(site_id):
    """Enables or disables monitoring for a website."""
    new_state = toggle_website(site_id)
    status_str = "enabled" if new_state else "disabled"
    flash(f"Website monitoring has been {status_str}.", "info")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/websites/<int:site_id>/delete", methods=["POST"])
@admin_required
def api_delete_website(site_id):
    """Deletes a website and its history from the database."""
    site = get_website_by_id(site_id)
    if site:
        delete_website(site_id)
        flash(f"Website '{site['name']}' deleted.", "info")
    return redirect(url_for("dashboard"))


@app.route("/api/websites/<int:site_id>/test", methods=["POST"])
def api_test_website(site_id):
    """Executes an immediate manual check for a single website."""
    site = get_website_by_id(site_id)
    if not site:
        return jsonify({"success": False, "error": "Website not found"}), 404

    result = check_single_website(site)
    if not result:
        return jsonify({"success": False, "error": "Test check failed"}), 500

    if request.is_json:
        return jsonify({"success": True, "result": result})

    if result["is_success"]:
        flash(
            f"Test check succeeded: {site['name']} is ONLINE (HTTP {result['http_status']}, {result['response_time_ms']} ms).",
            "success"
        )
    else:
        flash(
            f"Test check result: {site['name']} status is {result['status_result']} ({result['error_message']}).",
            "warning"
        )

    return redirect(request.referrer or url_for("website_details", site_id=site_id))


@app.route("/api/check-all", methods=["POST"])
def api_check_all():
    """Triggers an immediate check across all enabled websites."""
    results = run_monitor()
    online_count = sum(1 for r in results if r["status_result"] == "ONLINE")
    flash(
        f"Completed manual check for {len(results)} website(s): {online_count} ONLINE.",
        "success"
    )
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/status", methods=["GET"])
def api_status_json():
    """Provides machine-readable JSON status of all websites."""
    websites = get_all_websites(include_disabled=True)
    summary = get_dashboard_summary()
    return jsonify({
        "summary": summary,
        "websites": websites,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


# ------------------------------------------------------------------------------
# Optional Authentication Routes
# ------------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    """Admin login page for dashboard actions."""
    if not AUTH_REQUIRED:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["is_admin"] = True
            flash("Logged in successfully.", "success")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("dashboard"))
        else:
            flash("Invalid admin credentials.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Clears admin session."""
    session.pop("is_admin", None)
    flash("Signed out.", "info")
    return redirect(url_for("dashboard"))


# ------------------------------------------------------------------------------
# Application Entry Point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("WEBSITE UPTIME + KEEP-ALIVE MONITOR DASHBOARD")
    print(f"Server URL:     http://localhost:{PORT}")
    print(f"Database Mode:  {'Shared PostgreSQL' if IS_POSTGRES else 'Local SQLite'}")
    print(f"Protected Mode: {'Enabled' if AUTH_REQUIRED else 'Open developer mode'}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=PORT, debug=False)
