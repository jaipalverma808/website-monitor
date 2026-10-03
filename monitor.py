"""
Standalone Website Uptime and Keep-Alive Monitoring Engine.
Executed periodically by GitHub Actions or manually via CLI/Dashboard.

Does NOT depend on a continuously running background loop or awake dashboard.
"""
import sys
import time
import argparse
import logging
import requests
from datetime import datetime

from config import REQUEST_TIMEOUT, FAILURE_THRESHOLD, USER_AGENT
from database import init_db
from models import (
    get_all_websites,
    get_website_by_id,
    record_check_result
)

# Configure human-friendly logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("monitor")


def check_single_website(site, timeout=None):
    """
    Pings a single website, measures latency, determines status,
    and stores the monitoring result in the database.
    
    Returns a dictionary summarizing the check result.
    """
    timeout = timeout or REQUEST_TIMEOUT
    site_id = site["id"]
    site_name = site["name"]
    target_url = (site.get("health_url") or site.get("website_url") or "").strip()
    threshold = site.get("failure_threshold") or FAILURE_THRESHOLD
    current_failures = site.get("consecutive_failures") or 0

    if not target_url:
        logger.error(f"Website ID {site_id} ({site_name}) has no valid URL configured.")
        return None

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*"
    }

    start_time = time.time()
    http_status = None
    response_time_ms = None
    is_success = False
    error_message = None

    try:
        response = requests.get(
            target_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True
        )
        elapsed_sec = time.time() - start_time
        response_time_ms = int(elapsed_sec * 1000)
        http_status = response.status_code

        # Accept 2xx and 3xx as reachable/online responses
        if 200 <= http_status < 400:
            is_success = True
        elif http_status in (502, 503, 504):
            # Render cold boot blip: wait 3 seconds and retry once
            logger.info(f"Received HTTP {http_status} for {site_name}; retrying once after 3s (cold boot recovery)...")
            time.sleep(3)
            try:
                retry_resp = requests.get(target_url, headers=headers, timeout=timeout, allow_redirects=True)
                http_status = retry_resp.status_code
                elapsed_sec = time.time() - start_time
                response_time_ms = int(elapsed_sec * 1000)
                if 200 <= http_status < 400:
                    is_success = True
                    error_message = None
                else:
                    is_success = False
                    error_message = f"HTTP {http_status} returned (after cold-boot retry)"
            except Exception as re:
                elapsed_sec = time.time() - start_time
                response_time_ms = int(elapsed_sec * 1000)
                is_success = False
                error_message = f"Cold boot retry failed: {re}"
        elif http_status == 404 and site.get("website_url") and target_url != site.get("website_url"):
            # If health endpoint returns 404 (e.g. deployment building), fallback to website_url
            try:
                fb_resp = requests.get(site["website_url"], headers=headers, timeout=timeout, allow_redirects=True)
                if 200 <= fb_resp.status_code < 400:
                    is_success = True
                    http_status = fb_resp.status_code
                    target_url = site["website_url"]
                    elapsed_sec = time.time() - start_time
                    response_time_ms = int(elapsed_sec * 1000)
                    error_message = None
                else:
                    is_success = False
                    error_message = f"Health 404; Root HTTP {fb_resp.status_code}"
            except Exception as fbe:
                is_success = False
                error_message = f"Health 404; Root failed: {fbe}"
        else:
            is_success = False
            error_message = f"HTTP {http_status} returned"

    except requests.exceptions.Timeout:
        elapsed_sec = time.time() - start_time
        response_time_ms = int(elapsed_sec * 1000)
        is_success = False
        error_message = f"Request timed out after {timeout}s (service may be cold booting or unreachable)"

    except requests.exceptions.ConnectionError as ce:
        elapsed_sec = time.time() - start_time
        response_time_ms = int(elapsed_sec * 1000)
        is_success = False
        error_message = f"Connection failed: {str(ce).split(':')[-1].strip()}"

    except Exception as ex:
        elapsed_sec = time.time() - start_time
        response_time_ms = int(elapsed_sec * 1000)
        is_success = False
        error_message = f"Request error: {str(ex)}"

    # Determine status: ONLINE, WARNING, or OFFLINE
    if is_success:
        status_result = "ONLINE"
    else:
        new_failures = current_failures + 1
        if new_failures >= threshold:
            status_result = "OFFLINE"
        else:
            status_result = "WARNING"

    # Record result to database
    try:
        record_check_result(
            website_id=site_id,
            is_success=is_success,
            http_status=http_status,
            response_time_ms=response_time_ms,
            target_url=target_url,
            error_message=error_message,
            status_result=status_result
        )
    except Exception as dbe:
        logger.error(f"Failed to record check result for {site_name}: {dbe}")

    # Log human-readable summary
    if is_success:
        logger.info(
            f"[{status_result}] {site_name} | {target_url} | HTTP {http_status} | {response_time_ms} ms"
        )
    else:
        logger.warning(
            f"[{status_result}] {site_name} | {target_url} | HTTP {http_status or '-'} | {error_message}"
        )

    return {
        "website_id": site_id,
        "name": site_name,
        "target_url": target_url,
        "is_success": is_success,
        "http_status": http_status,
        "response_time_ms": response_time_ms,
        "status_result": status_result,
        "error_message": error_message
    }


def run_monitor(site_id=None):
    """
    Loads all enabled websites (or single specified site),
    executes checks sequentially, and ensures one failure never stops others.
    """
    init_db()

    if site_id:
        site = get_website_by_id(site_id)
        if not site:
            logger.error(f"Website with ID {site_id} not found.")
            return []
        websites = [site]
    else:
        websites = get_all_websites(include_disabled=False)

    if not websites:
        logger.info("No enabled websites found to monitor.")
        return []

    logger.info(f"Starting keep-alive and uptime check for {len(websites)} website(s)...")

    results = []
    for site in websites:
        try:
            res = check_single_website(site)
            if res:
                results.append(res)
        except Exception as e:
            logger.error(f"Unexpected error while monitoring {site.get('name')}: {e}")
            continue

    online_count = sum(1 for r in results if r["status_result"] == "ONLINE")
    warning_count = sum(1 for r in results if r["status_result"] == "WARNING")
    offline_count = sum(1 for r in results if r["status_result"] == "OFFLINE")

    logger.info(
        f"Completed check run: {len(results)} total | "
        f"{online_count} ONLINE | {warning_count} WARNING | {offline_count} OFFLINE"
    )
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Website Uptime and Keep-Alive Monitoring Engine"
    )
    parser.add_argument(
        "--site-id",
        type=int,
        default=None,
        help="Optional specific website ID to check"
    )
    args = parser.parse_args()

    results = run_monitor(site_id=args.site_id)
    sys.exit(0)


if __name__ == "__main__":
    main()
