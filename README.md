# Website Uptime + Keep-Alive Monitoring System

A standalone, lightweight monitoring and keep-alive engine built with **Python, Flask, and PostgreSQL**. 

Designed specifically for modern cloud services (such as Render) where background schedulers sleep if bound to web workers. This system completely decouples the **monitoring scheduler** (powered by GitHub Actions) from the **monitoring dashboard** (hosted on Render or run locally).

---

## 1. What the Project Does

- **Keeps Web Services Warm:** Periodically pings deployed web applications to reduce cold starts and avoid idle suspension on free cloud tiers.
- **Tracks Availability & Latency:** Measures exact response times in milliseconds and logs HTTP status codes.
- **Consecutive Failure Thresholds:** Distinguishes temporary network glitches from actual outages. A single failed ping triggers a `WARNING` state; only persistent failures reaching the configured threshold flip status to `OFFLINE`.
- **Complete Inspection History:** Maintains a searchable log of historical checks, uptime percentages, and latency trends.
- **Centralized Management Dashboard:** Clean, human-crafted developer interface to add new websites, edit endpoints, toggle monitoring, and inspect performance metrics without modifying Python code.

---

## 2. Architecture

```
                    GitHub Actions Runner
                              |
                              | Scheduled cron (every 10 minutes)
                              v
                   Monitoring Script (monitor.py)
                     /      |      \      \
                    /       |       \      \
                   v        v        v      v
              SmartMine  Resume  Stories  Stories
                Guard    Forge     Hub    Hub Bot
                   \        |       /       /
                    \       |      /       /
                     v      v     v       v
                   Shared PostgreSQL Database
                              ^
                              |
                              | Reads history & manages targets
                              v
                  Flask Dashboard (Render / Local)
```

### Key Separation of Concerns
1. **GitHub Actions is the Scheduler:** It runs in a temporary virtual runner, checks every enabled website, saves the results to the shared database, and cleanly exits.
2. **Dashboard is for Humans:** It displays metrics, allows adding/editing websites, and runs on demand. Even if the dashboard container sleeps on Render, the monitoring schedule continues uninterrupted via GitHub Actions.

---

## 3. How GitHub Actions Works

The workflow file is located at [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml).

- **Schedule (`cron: '*/10 * * * *'`):** Triggers automatically every 10 minutes.
- **Manual Trigger (`workflow_dispatch`):** Allows triggering an immediate check run from the GitHub Actions web interface with an optional single website ID filter.
- **Secure Secrets:** The database connection string is passed securely via `${{ secrets.DATABASE_URL }}` without exposing credentials in code.
- **Resilient Execution:** If one website fails or times out, the script catches the error, logs the failure, and continues checking all remaining targets.

---

## 4. How Websites Are Monitored

1. The monitor fetches all active website records from the `websites` table.
2. For each website, the script determines the target check endpoint:
   - If `health_url` is configured, it sends a `GET` request there.
   - Otherwise, it falls back to the main `website_url`.
3. The HTTP request sends a designated user-agent header (`Website-Monitor-Bot/1.0`).
4. The system measures elapsed wall-clock latency (ms) and captures the HTTP status code.
5. Status Classification:
   - **`ONLINE`:** HTTP status in the `200..399` range. Resets consecutive failure counter to `0`.
   - **`WARNING`:** HTTP error or request timeout, but consecutive failures are less than the target's `failure_threshold` (default: 3).
   - **`OFFLINE`:** Consecutive failures equal or exceed `failure_threshold`.
6. The result is committed to the `check_logs` table, and aggregated counters on `websites` are updated.

---

## 5. How Keep-Alive Requests Work

Free tier cloud providers (such as Render) suspend idle web services after 15 minutes without incoming traffic. A suspended service incurs a cold boot penalty of 20 to 50 seconds when a user finally visits.

By scheduling a lightweight request every 10 minutes:
- The server container is kept warm during active hours.
- If already sleeping, the scheduled request initiates the container boot before human users arrive.
- The dedicated `/health` endpoint executes minimal Python code with zero database queries, keeping server CPU and memory usage negligible.

---

## 6. Database Setup

The project supports both **PostgreSQL** (production) and **SQLite** (local development/testing).

### Production PostgreSQL Setup (e.g. Render Postgres / Neon / Supabase)
1. Create a PostgreSQL instance on your preferred provider.
2. Copy the external connection string:
   ```text
   postgresql://username:password@hostname:5432/dbname?sslmode=require
   ```
3. Set the `DATABASE_URL` environment variable.
4. The application automatically initializes the required tables and indexes on first startup (`init_db()`).

### SQL Schema Overview
- **`websites`**: Stores target names, home URLs, health URLs, GitHub repo metadata, status, consecutive failures, and thresholds.
- **`check_logs`**: Time-series log containing each check timestamp, latency in ms, HTTP response code, target URL, and error messages.

---

## 7. Environment Variables

Create a `.env` file in the project root (see [`.env.example`](.env.example)):

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | Production | *SQLite local fallback* | Shared PostgreSQL connection string |
| `SECRET_KEY` | Recommended | *dev-key* | Flask session cryptographic signing key |
| `PORT` | Optional | `5000` | Port for the Flask dashboard web server |
| `REQUEST_TIMEOUT` | Optional | `35` | Timeout in seconds per website check |
| `FAILURE_THRESHOLD` | Optional | `3` | Failures before a site is marked OFFLINE |
| `ADMIN_USERNAME` | Optional | `admin` | Username for dashboard management |
| `ADMIN_PASSWORD` | Optional | *None (open)* | If set, dashboard mutations require login |

---

## 8. Local Development

1. **Clone this repository & open the folder:**
   ```bash
   cd "bot for all website"
   ```

2. **Create and activate a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run a manual monitoring check:**
   ```bash
   python monitor.py
   ```
   *(Without a `DATABASE_URL`, this automatically creates and populates `data/monitor.db` using SQLite).*

5. **Start the local Flask dashboard:**
   ```bash
   python app.py
   ```
   Open your browser to `http://localhost:5000`.

---

## 9. Deployment Guide

### Deploying the Dashboard on Render
1. Create a **New Web Service** on [Render](https://render.com/).
2. Connect your GitHub repository containing this monitoring code.
3. Configure settings:
   - **Environment:** `Python`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
4. In the **Environment Variables** section, add:
   - `DATABASE_URL`: Your shared PostgreSQL connection URI.
   - `SECRET_KEY`: A strong random string.
   - `ADMIN_USERNAME`: `admin`
   - `ADMIN_PASSWORD`: Your private admin dashboard password.

### Configuring GitHub Secrets for GitHub Actions
1. In your GitHub repository for this monitoring project, navigate to:
   **Settings &rarr; Secrets and variables &rarr; Actions &rarr; New repository secret**.
2. Name: `DATABASE_URL`
3. Value: Your shared PostgreSQL connection string.
4. Save the secret. GitHub Actions is now ready to run on schedule.

---

## 10. How to Add Another Website

You do **not** need to modify Python code to monitor new websites.

1. Open the dashboard in your browser.
2. Click the **+ Add Website** button in the top right.
3. Provide:
   - **Website Name:** e.g., `Portfolio Site`
   - **Website Home URL:** `https://my-portfolio.onrender.com/`
   - **Health Check URL (optional):** `https://my-portfolio.onrender.com/health`
   - **GitHub Repository URL (optional):** `https://github.com/my-user/portfolio`
   - **Failure Threshold:** e.g. `3`
4. Click **Save Website**.
5. The new website is immediately included in the database and will be checked in the next GitHub Actions cycle.

---

## 11. How to Manually Run a Monitoring Check

### Option A: From the Dashboard
- Click **"Run All Checks"** in the top navigation bar to test all active websites.
- Or click **"Test"** on any specific website row.

### Option B: Via Command Line
```bash
# Check all enabled websites
python monitor.py

# Check only a specific website ID
python monitor.py --site-id 2
```

### Option C: Via GitHub Actions
1. Go to your repository on GitHub &rarr; **Actions**.
2. Select **Website Keep-Alive and Uptime Monitor** from the sidebar.
3. Click **Run workflow** &rarr; optionally provide a website ID or leave blank &rarr; click **Run workflow**.

---

## 12. Limitations & Disclosures

> [!IMPORTANT]
> **Keep-Alive &ne; 100% Uptime Guarantee**
> - **Cold Start Mitigation:** Keep-alive requests prevent services from sleeping due to inactivity. However, if a service *does* go to sleep between checks, the first request may take 20–45 seconds to respond.
> - **Platform Outages:** If Render or another cloud provider suffers an infrastructure failure, network routing issues, or database outages, scheduled pings cannot keep the service operational.
> - **Deployment Downtime:** While a new build is deploying, the endpoint may temporarily return HTTP 502 or 503 until container initialization is complete.
