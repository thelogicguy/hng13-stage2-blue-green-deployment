#!/usr/bin/env python3
"""
Nginx Log Watcher for Blue-Green Deployment Monitoring
Monitors Nginx access logs and sends Slack alerts for:
- Failover events (pool changes)
- High error rates (5xx responses)
- Recovery events (return to primary pool)
"""

import os
import re
import sys
import time
import subprocess
import requests
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

# ============================================================================
# CONFIGURATION - Load from environment variables
# ============================================================================

SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')
ERROR_RATE_THRESHOLD = float(os.environ.get('ERROR_RATE_THRESHOLD', '2.0'))
WINDOW_SIZE = int(os.environ.get('WINDOW_SIZE', '200'))
ALERT_COOLDOWN_SEC = int(os.environ.get('ALERT_COOLDOWN_SEC', '300'))
MAINTENANCE_MODE = os.environ.get('MAINTENANCE_MODE', 'false').lower() == 'true'
LOG_FILE = '/var/log/nginx/access.log'
MAINTENANCE_FLAG_FILE = '/app/state/maintenance.flag'

# ============================================================================
# STATE MANAGEMENT
# ============================================================================

# Sliding window to track recent requests
request_window = deque(maxlen=WINDOW_SIZE)

# Track the last known pool to detect changes
last_known_pool: Optional[str] = None

# Track last alert times to implement cooldown
last_alert_times: Dict[str, datetime] = {
    'failover': datetime.min,
    'error_rate': datetime.min,
    'recovery': datetime.min
}

# Track if we've seen a failover (to detect recovery)
failover_occurred = False
failover_from_pool: Optional[str] = None

# ============================================================================
# LOG PARSING
# ============================================================================

# Regex pattern to parse the detailed log format
# Format: pool=<pool> release=<release> status=<status> upstream_status=<upstream_status> ...
LOG_PATTERN = re.compile(
    r'pool=(?P<pool>[^\s]*)\s+'
    r'release=(?P<release>[^\s]*)\s+'
    r'status=(?P<status>\d+)\s+'
    r'upstream_status=(?P<upstream_status>[^\s]*)\s+'
    r'upstream=(?P<upstream>[^\s]*)\s+'
    r'request_time=(?P<request_time>[^\s]*)\s+'
    r'upstream_response_time=(?P<upstream_response_time>[^\s]*)\s+'
    r'method=(?P<method>[^\s]*)\s+'
    r'uri=(?P<uri>[^\s]*)\s+'
    r'time=(?P<time>[^\s]*)'
)


def parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a log line and extract relevant fields.

    Args:
        line: Raw log line from Nginx

    Returns:
        Dictionary with parsed fields or None if parsing fails
    """
    match = LOG_PATTERN.match(line.strip())
    if not match:
        return None

    data = match.groupdict()

    # Convert status to integer
    try:
        data['status'] = int(data['status'])
    except (ValueError, TypeError):
        data['status'] = 0

    # Parse upstream_status (can be multiple values like "502, 200")
    try:
        upstream_statuses = data['upstream_status'].split(',')
        data['upstream_status_codes'] = [int(s.strip()) for s in upstream_statuses if s.strip().isdigit()]
    except (ValueError, AttributeError):
        data['upstream_status_codes'] = []

    return data


# ============================================================================
# SLACK NOTIFICATION
# ============================================================================

def send_slack_alert(alert_type: str, message: str, details: Dict[str, Any]) -> bool:
    """
    Send an alert to Slack using webhook.

    Args:
        alert_type: Type of alert (failover, error_rate, recovery)
        message: Main alert message
        details: Additional details to include

    Returns:
        True if alert sent successfully, False otherwise
    """
    if not SLACK_WEBHOOK_URL:
        print(f"[ALERT] {alert_type.upper()}: {message}")
        print(f"[ALERT] Details: {details}")
        print("[WARNING] SLACK_WEBHOOK_URL not configured - alert not sent to Slack")
        return False

    # Check if in maintenance mode
    if is_maintenance_mode():
        print(f"[MAINTENANCE MODE] Alert suppressed: {alert_type}")
        return False

    # Check cooldown
    now = datetime.now()
    last_alert = last_alert_times.get(alert_type, datetime.min)
    cooldown_delta = timedelta(seconds=ALERT_COOLDOWN_SEC)

    if now - last_alert < cooldown_delta:
        remaining = (last_alert + cooldown_delta - now).total_seconds()
        print(f"[COOLDOWN] Alert {alert_type} suppressed (cooldown: {remaining:.0f}s remaining)")
        return False

    # Prepare Slack message with rich formatting
    emoji_map = {
        'failover': '🔄',
        'error_rate': '🚨',
        'recovery': '✅'
    }

    emoji = emoji_map.get(alert_type, '⚠️')

    # Build formatted message
    slack_message = {
        "text": f"{emoji} {alert_type.upper().replace('_', ' ')} DETECTED",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {alert_type.upper().replace('_', ' ')} DETECTED"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*{key}:*\n{value}"
                    }
                    for key, value in details.items()
                ]
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    }
                ]
            }
        ]
    }

    # Send to Slack
    try:
        response = requests.post(
            SLACK_WEBHOOK_URL,
            json=slack_message,
            timeout=10
        )
        response.raise_for_status()

        # Update last alert time
        last_alert_times[alert_type] = now

        print(f"[SLACK] Alert sent successfully: {alert_type}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to send Slack alert: {e}")
        return False


# ============================================================================
# MAINTENANCE MODE
# ============================================================================

def is_maintenance_mode() -> bool:
    """
    Check if maintenance mode is active.
    Maintenance mode can be enabled via:
    1. Environment variable MAINTENANCE_MODE=true
    2. Presence of maintenance flag file

    Returns:
        True if maintenance mode is active
    """
    # Check environment variable
    if MAINTENANCE_MODE:
        return True

    # Check flag file
    if os.path.exists(MAINTENANCE_FLAG_FILE):
        return True

    return False


# ============================================================================
# MONITORING LOGIC
# ============================================================================

def check_failover(pool: str, release: str) -> None:
    """
    Check if a failover has occurred (pool change).

    Args:
        pool: Current pool serving the request
        release: Current release ID
    """
    global last_known_pool, failover_occurred, failover_from_pool

    # Skip if pool is unknown or empty
    if not pool or pool == '-':
        return

    # Initialize on first valid pool
    if last_known_pool is None:
        last_known_pool = pool
        print(f"[INIT] Initial pool detected: {pool}")
        return

    # Check if pool has changed
    if pool != last_known_pool:
        print(f"[FAILOVER DETECTED] {last_known_pool} → {pool}")

        # Send Slack alert
        send_slack_alert(
            alert_type='failover',
            message=f"*Traffic has switched from {last_known_pool} to {pool}*\n\nThe backup pool is now serving requests.",
            details={
                "From Pool": last_known_pool,
                "To Pool": pool,
                "New Release": release,
                "Action Required": "Check health of the failed pool",
                "Debug Command": f"`docker logs app_{last_known_pool}`"
            }
        )

        # Track failover state
        failover_occurred = True
        failover_from_pool = last_known_pool
        last_known_pool = pool


def check_recovery(pool: str, release: str) -> None:
    """
    Check if recovery has occurred (return to original pool).

    Args:
        pool: Current pool serving the request
        release: Current release ID
    """
    global failover_occurred, failover_from_pool

    # Only check recovery if a failover occurred
    if not failover_occurred or not failover_from_pool:
        return

    # Check if we've returned to the original pool
    if pool == failover_from_pool:
        print(f"[RECOVERY DETECTED] Returned to {pool}")

        # Send Slack alert
        send_slack_alert(
            alert_type='recovery',
            message=f"*Service has recovered and returned to {pool} pool*\n\nPrimary pool is now serving traffic again.",
            details={
                "Recovered Pool": pool,
                "Release": release,
                "Status": "Primary pool is healthy",
                "Action": "Monitor for stability"
            }
        )

        # Reset failover state
        failover_occurred = False
        failover_from_pool = None


def check_error_rate() -> None:
    """
    Check if error rate exceeds threshold over the sliding window.
    """
    if len(request_window) < WINDOW_SIZE:
        # Not enough data yet
        return

    # Count 5xx errors in the window
    error_count = sum(1 for req in request_window if req.get('is_error', False))
    error_rate = (error_count / len(request_window)) * 100

    if error_rate > ERROR_RATE_THRESHOLD:
        print(f"[HIGH ERROR RATE] {error_rate:.2f}% (threshold: {ERROR_RATE_THRESHOLD}%)")

        # Get current pool from most recent request
        current_pool = request_window[-1].get('pool', 'unknown')

        # Send Slack alert
        send_slack_alert(
            alert_type='error_rate',
            message=f"*Error rate is {error_rate:.2f}% over the last {WINDOW_SIZE} requests*\n\nThreshold: {ERROR_RATE_THRESHOLD}%",
            details={
                "Error Rate": f"{error_rate:.2f}%",
                "Threshold": f"{ERROR_RATE_THRESHOLD}%",
                "Window Size": f"{WINDOW_SIZE} requests",
                "Errors": f"{error_count}/{len(request_window)}",
                "Current Pool": current_pool,
                "Action Required": "Inspect upstream logs and consider toggling pools",
                "Debug Command": f"`docker logs app_{current_pool}`"
            }
        )


def process_log_entry(data: Dict[str, Any]) -> None:
    """
    Process a parsed log entry and perform all monitoring checks.

    Args:
        data: Parsed log data dictionary
    """
    pool = data.get('pool', '-')
    release = data.get('release', '-')
    status = data.get('status', 0)

    # Determine if this is an error (5xx status)
    is_error = 500 <= status < 600

    # Add to sliding window
    request_window.append({
        'pool': pool,
        'release': release,
        'status': status,
        'is_error': is_error,
        'timestamp': data.get('time', '')
    })

    # Check for failover
    check_failover(pool, release)

    # Check for recovery
    check_recovery(pool, release)

    # Check error rate
    check_error_rate()


# ============================================================================
# LOG TAILING
# ============================================================================

def tail_log_file(log_file: str) -> None:
    """
    Tail the log file and process entries in real-time.

    Args:
        log_file: Path to the log file to tail
    """
    print(f"[START] Watching log file: {log_file}")
    print(f"[CONFIG] Error threshold: {ERROR_RATE_THRESHOLD}%, Window: {WINDOW_SIZE}, Cooldown: {ALERT_COOLDOWN_SEC}s")
    print(f"[CONFIG] Maintenance mode: {is_maintenance_mode()}")

    if not SLACK_WEBHOOK_URL:
        print("[WARNING] SLACK_WEBHOOK_URL is not configured - alerts will only be logged to console")

    # Wait for log file to be created
    while not os.path.exists(log_file):
        print(f"[WAITING] Log file not found: {log_file}")
        time.sleep(5)

    print(f"[READY] Log file found, starting to tail...")

    # Use tail -F to follow the log file (handles log rotation)
    process = subprocess.Popen(
        ['tail', '-F', '-n', '0', log_file],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1
    )

    try:
        # Process each line as it arrives
        for line in iter(process.stdout.readline, ''):
            if not line:
                continue

            # Parse the log line
            data = parse_log_line(line)
            if data:
                process_log_entry(data)

    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Received interrupt signal")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
    finally:
        process.terminate()
        process.wait()
        print("[SHUTDOWN] Watcher stopped")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point for the log watcher."""
    print("=" * 80)
    print("NGINX LOG WATCHER - Blue/Green Deployment Monitor")
    print("=" * 80)

    # Validate configuration
    if not SLACK_WEBHOOK_URL:
        print("[WARNING] SLACK_WEBHOOK_URL not configured - running in console-only mode")

    try:
        tail_log_file(LOG_FILE)
    except Exception as e:
        print(f"[FATAL ERROR] {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
