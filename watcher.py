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
import json
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
BREACH_LOG_FILE = '/app/state/error_rate_breaches.log'

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

def get_current_error_rate() -> tuple[float, int, int]:
    """
    Calculate the current error rate from the sliding window.

    Returns:
        Tuple of (error_rate_percentage, error_count, total_count)
    """
    if len(request_window) == 0:
        return 0.0, 0, 0

    error_count = sum(1 for req in request_window if req.get('is_error', False))
    total_count = len(request_window)
    error_rate = (error_count / total_count) * 100

    return error_rate, error_count, total_count


def format_log_snippet(num_lines: int = 3, errors_only: bool = False) -> str:
    """
    Format recent log entries as a snippet showing structured fields.

    Args:
        num_lines: Number of log lines to include
        errors_only: If True, only include error responses

    Returns:
        Formatted log snippet string
    """
    if len(request_window) == 0:
        return "_No recent requests_"

    # Get recent requests (filter for errors if requested)
    recent_requests = list(request_window)[-20:]  # Last 20 requests
    if errors_only:
        recent_requests = [req for req in recent_requests if req.get('is_error', False)]

    # Take the last num_lines
    recent_requests = recent_requests[-num_lines:]

    if not recent_requests:
        return "_No matching requests_"

    # Format each request as a log line
    lines = []
    for req in recent_requests:
        status_emoji = "🔴" if req.get('is_error', False) else "🟢"
        log_line = (
            f"{status_emoji} `pool={req.get('pool', '-')} "
            f"release={req.get('release', '-')} "
            f"status={req.get('status', '-')} "
            f"upstream_status={req.get('upstream_status', '-')} "
            f"upstream={req.get('upstream', '-')} "
            f"request_time={req.get('request_time', '-')} "
            f"upstream_response_time={req.get('upstream_response_time', '-')} "
            f"method={req.get('method', '-')} "
            f"uri={req.get('uri', '-')}`"
        )
        lines.append(log_line)

    return "\n".join(lines)


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

    title_map = {
        'failover': 'FAILOVER DETECTED',
        'error_rate': 'HIGH ERROR RATE ALERT',
        'recovery': 'RECOVERY DETECTED'
    }

    emoji = emoji_map.get(alert_type, '⚠️')
    title = title_map.get(alert_type, alert_type.upper().replace('_', ' '))

    # Get current error rate and log snippets
    error_rate, error_count, total_count = get_current_error_rate()
    log_snippet = details.pop('log_snippet', None)  # Get custom snippet if provided

    # Build formatted message
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {title}",
                "emoji": True
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": message
            }
        },
        {
            "type": "divider"
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
        }
    ]

    # Add error rate section if we have data
    if total_count > 0:
        error_rate_emoji = "🔴" if error_rate > ERROR_RATE_THRESHOLD else "🟢"
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📊 Current Error Rate*\n{error_rate_emoji} *{error_rate:.2f}%* ({error_count}/{total_count} requests in last {WINDOW_SIZE} request window)"
                }
            }
        ])

    # Add log snippet section if available
    if log_snippet:
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📋 Recent Nginx Log Entries*\n{log_snippet}"
                }
            }
        ])

    # Add timestamp footer
    blocks.extend([
        {
            "type": "divider"
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"⏰ *Timestamp:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                }
            ]
        }
    ])

    slack_message = {
        "text": f"{emoji} {title}",
        "blocks": blocks
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


def log_error_rate_breach(error_rate: float, error_count: int, total_count: int, pool: str) -> None:
    """
    Log error rate threshold breach to persistent file.
    This captures breach events even when Slack alerts are suppressed due to cooldown.

    Args:
        error_rate: Current error rate percentage
        error_count: Number of errors in window
        total_count: Total number of requests in window
        pool: Current pool serving requests
    """
    try:
        breach_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp_iso': datetime.now().isoformat(),
            'error_rate': round(error_rate, 2),
            'error_count': error_count,
            'total_count': total_count,
            'threshold': ERROR_RATE_THRESHOLD,
            'pool': pool,
            'window_size': WINDOW_SIZE,
            'exceeded_by': round(error_rate - ERROR_RATE_THRESHOLD, 2)
        }

        # Ensure state directory exists
        os.makedirs(os.path.dirname(BREACH_LOG_FILE), exist_ok=True)

        # Append breach record to log file (one JSON object per line)
        with open(BREACH_LOG_FILE, 'a') as f:
            f.write(json.dumps(breach_data) + '\n')

        print(f"[BREACH LOGGED] {error_rate:.2f}% in {pool} pool (threshold: {ERROR_RATE_THRESHOLD}%)")

    except Exception as e:
        print(f"[ERROR] Failed to log breach to file: {e}")


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

        # Get log snippet showing recent errors and the failover
        log_snippet = format_log_snippet(num_lines=3, errors_only=False)

        # Send Slack alert
        send_slack_alert(
            alert_type='failover',
            message=f"*Failover Event Detected!*\n\nTraffic has automatically switched from the *{last_known_pool.upper()}* pool to the *{pool.upper()}* pool. The {last_known_pool} pool is experiencing failures or health check issues.\n\n_The backup pool is now serving all incoming requests._",
            details={
                "From Pool": last_known_pool.upper(),
                "To Pool": pool.upper(),
                "New Release": release,
                "Status": f"⚠️ {last_known_pool.upper()} pool unhealthy, {pool.upper()} pool active",
                "Action Required": f"Investigate {last_known_pool} pool health immediately",
                "Debug Command": f"`docker logs app_{last_known_pool}`",
                "log_snippet": log_snippet
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

        # Get log snippet showing the recovery
        log_snippet = format_log_snippet(num_lines=3, errors_only=False)

        # Send Slack alert
        send_slack_alert(
            alert_type='recovery',
            message=f"*Recovery Complete!*\n\nThe *{pool.upper()}* pool has recovered and is now serving traffic again. The system has automatically failed back to the primary pool.\n\n_Normal operations resumed._",
            details={
                "Recovered Pool": pool.upper(),
                "Release": release,
                "Status": "✅ Primary pool healthy and active",
                "Previous State": f"Was using {last_known_pool.upper()} as backup",
                "Action": "Continue monitoring for stability",
                "log_snippet": log_snippet
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

        # Log breach to persistent file BEFORE cooldown check
        # This ensures we capture ALL threshold breaches, even during cooldown
        log_error_rate_breach(error_rate, error_count, len(request_window), current_pool)

        # Get log snippet showing error responses only
        log_snippet = format_log_snippet(num_lines=5, errors_only=True)

        # Send Slack alert (subject to cooldown)
        send_slack_alert(
            alert_type='error_rate',
            message=f"*High Error Rate Detected!*\n\nThe current error rate is *{error_rate:.2f}%* which exceeds the configured threshold of *{ERROR_RATE_THRESHOLD}%*.\n\n_This indicates the {current_pool.upper()} pool is experiencing issues and returning 5xx errors._",
            details={
                "Current Error Rate": f"🔴 {error_rate:.2f}%",
                "Threshold": f"{ERROR_RATE_THRESHOLD}%",
                "Window Size": f"{WINDOW_SIZE} requests",
                "Error Count": f"{error_count} errors out of {len(request_window)} requests",
                "Current Pool": current_pool.upper(),
                "Severity": "⚠️ High - Immediate attention required",
                "Action Required": "Check application logs and consider manual pool toggle",
                "Debug Command": f"`docker logs app_{current_pool}`",
                "log_snippet": log_snippet
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

    # Add to sliding window (including raw log line for snippets)
    request_window.append({
        'pool': pool,
        'release': release,
        'status': status,
        'is_error': is_error,
        'timestamp': data.get('time', ''),
        'upstream_status': data.get('upstream_status', '-'),
        'request_time': data.get('request_time', '-'),
        'upstream_response_time': data.get('upstream_response_time', '-'),
        'method': data.get('method', '-'),
        'uri': data.get('uri', '-'),
        'upstream': data.get('upstream', '-')
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
