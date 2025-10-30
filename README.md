# 🟦🟩 Blue-Green Deployment with Nginx Auto-Failover & Observability

This project demonstrates a **Blue/Green deployment strategy** using **Nginx upstreams** for seamless failover between two identical Node.js application instances, enhanced with **real-time monitoring and Slack alerts**.

---

## 🚀 Overview

- **Blue (active)** and **Green (backup)** services run as separate containers.
- **Nginx** routes traffic to the active service (Blue by default).
- If Blue fails (timeout or 5xx errors), Nginx automatically retries the request to Green — clients do not experience any error or downtime.
- Failover happens **within the same request** (instant switch).
- **Log Watcher** monitors Nginx access logs in real-time and sends Slack alerts for failovers, high error rates, and recovery events.
- Headers are preserved and forwarded to clients.

---

## 🆕 Features (Stage 3: Observability & Alerts)

- **📊 Enhanced Logging**: Nginx logs capture pool, release, upstream status, latency, and response times
- **👀 Real-Time Monitoring**: Python log-watcher service continuously monitors Nginx logs
- **💬 Slack Alerts**: Automatic notifications to Slack for:
  - 🔄 Failover events (blue → green or green → blue)
  - 🚨 High error rates (configurable threshold over sliding window)
  - ✅ Recovery events (return to primary pool)
- **⏱️ Alert Cooldown**: Prevents alert spam with configurable cooldown periods
- **🛠️ Maintenance Mode**: Suppress alerts during planned maintenance (via env var or flag file)
- **📚 Operator Runbook**: Comprehensive guide for responding to alerts

---

## 📋 Prerequisites

- Docker & Docker Compose
- Slack workspace with admin access (for webhook creation)
- curl (for testing)

---

## 🔧 Quick Start

### 1. Clone & Configure

```bash
# Clone the repository
git clone <your-repo-url>
cd hng13-stage2-blue-green-deployment

# Copy the example env file
cp .env.example .env

# Edit .env and configure your Slack webhook (see below)
nano .env
```

### 2. Set Up Slack Webhook

**Step-by-step instructions:**

1. Go to https://api.slack.com/apps
2. Click **"Create New App"** → Choose **"From scratch"**
3. Enter app name (e.g., "Blue-Green Alerts") and select your workspace
4. Click **"Incoming Webhooks"** in the left sidebar
5. Toggle **"Activate Incoming Webhooks"** to **On**
6. Click **"Add New Webhook to Workspace"**
7. Select the channel where you want alerts (e.g., #deployments or #alerts)
8. Click **"Allow"**
9. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`)
10. Paste it into your `.env` file:

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### 3. Start All Services

```bash
docker compose up -d
```

### 4. Verify Services

```bash
# Check all containers are running
docker compose ps

# Should see: app_blue, app_green, nginx_proxy, alert_watcher

# Test the application
curl -i http://localhost:8080/version
```

---

## 🧪 Testing the Observability System

### Option 1: Automated Test Suite (Recommended)

Run the comprehensive test script:

```bash
./test_alerts.sh
```

This will automatically:
1. ✅ Verify baseline (blue serving traffic)
2. 🔄 Trigger failover (blue → green) and verify Slack alert
3. 🚨 Generate high error rate and verify Slack alert
4. ✅ Trigger recovery (green → blue) and verify Slack alert
5. 🛠️ Test maintenance mode suppression

### Option 2: Manual Testing

**Test 1: Baseline Verification**
```bash
# Blue should be serving traffic
curl -i http://localhost:8080/version
# Look for: X-App-Pool: blue
```

**Test 2: Failover Detection**
```bash
# Trigger chaos on blue pool
curl -X POST http://localhost:8081/chaos/start?mode=error

# Make requests through Nginx
for i in {1..10}; do curl http://localhost:8080/version; done

# Verify green is now serving traffic
curl -i http://localhost:8080/version | grep "X-App-Pool: green"

# CHECK SLACK: You should see a "FAILOVER DETECTED" alert
```

**Test 3: Error Rate Alert**
```bash
# Green is now serving, enable chaos on it too
curl -X POST http://localhost:8082/chaos/start?mode=error

# Generate errors (100 requests)
for i in {1..100}; do curl -s http://localhost:8080/version > /dev/null; sleep 0.1; done

# CHECK SLACK: You should see a "HIGH ERROR RATE" alert
```

**Test 4: Recovery Detection**
```bash
# Stop chaos on blue to allow recovery
curl -X POST http://localhost:8081/chaos/stop

# Wait a few seconds and make requests
sleep 5
for i in {1..10}; do curl http://localhost:8080/version; sleep 0.5; done

# Verify blue is serving again
curl -i http://localhost:8080/version | grep "X-App-Pool: blue"

# Stop chaos on green too
curl -X POST http://localhost:8082/chaos/stop

# CHECK SLACK: You should see a "RECOVERY DETECTED" alert
```

---

## 📊 Viewing Logs

**Nginx Access Logs (Enhanced Format):**
```bash
docker exec nginx_proxy tail -f /var/log/nginx/access.log
```

**Alert Watcher Logs:**
```bash
docker logs -f alert_watcher
```

**Application Logs:**
```bash
docker logs -f app_blue
docker logs -f app_green
```

---

## ⚙️ Configuration

All configuration is managed through environment variables in `.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `SLACK_WEBHOOK_URL` | Slack webhook for alerts | (required) |
| `ERROR_RATE_THRESHOLD` | Error rate percentage threshold | `2` (2%) |
| `WINDOW_SIZE` | Number of requests in sliding window | `200` |
| `ALERT_COOLDOWN_SEC` | Seconds between alerts of same type | `300` (5 min) |
| `MAINTENANCE_MODE` | Suppress all alerts | `false` |

**Adjusting Thresholds:**

```bash
# Edit .env
ERROR_RATE_THRESHOLD=5    # Increase to 5%
WINDOW_SIZE=500           # Larger window, more stable
ALERT_COOLDOWN_SEC=600    # 10 minute cooldown

# Restart watcher to apply changes
docker compose restart alert_watcher
```

---

## 🛠️ Maintenance Mode

**Enable maintenance mode** to suppress alerts during planned maintenance:

**Option 1: Environment Variable (requires restart)**
```bash
# Edit .env
MAINTENANCE_MODE=true

# Restart services
docker compose restart alert_watcher
```

**Option 2: File-Based Flag (no restart)**
```bash
# Enable
docker exec alert_watcher touch /app/state/maintenance.flag

# Disable
docker exec alert_watcher rm -f /app/state/maintenance.flag
docker compose restart alert_watcher
```

---

## 📚 Operator Documentation

See **[runbook.md](runbook.md)** for detailed:
- Alert types and meanings
- Step-by-step response procedures
- Troubleshooting guides
- Escalation procedures
- Useful commands

---

## 🏗️ Architecture

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────┐
│  Nginx Reverse Proxy                │
│  - Routes to active pool            │
│  - Auto-failover on errors          │
│  - Enhanced logging                 │
└──────┬──────────────┬───────────────┘
       │              │
       │              │ (logs)
       │              ▼
       │        ┌────────────────┐
       │        │ Alert Watcher  │
       │        │ - Tails logs   │
       │        │ - Detects      │
       │        │   failovers    │
       │        │ - Calculates   │
       │        │   error rates  │
       │        └────────┬───────┘
       │                 │
       │                 ▼
       │          ┌───────────┐
       │          │   Slack   │
       │          │  Alerts   │
       │          └───────────┘
       │
   ┌───┴────┐
   ▼        ▼
┌──────┐ ┌──────┐
│ Blue │ │Green │
│ Pool │ │ Pool │
└──────┘ └──────┘
```

---

## 🐛 Troubleshooting

### Common Issues & Solutions

#### 1. No alerts in Slack

**Symptoms:** Alert system running but no Slack notifications received.

**Solution:**
```bash
# Step 1: Check watcher is running
docker ps | grep alert_watcher

# Step 2: Check watcher logs for errors
docker logs alert_watcher

# Step 3: Verify webhook URL is configured
docker exec alert_watcher env | grep SLACK_WEBHOOK_URL

# Step 4: Test webhook manually (replace with your actual webhook URL)
curl -X POST -H 'Content-Type: application/json' \
  -d '{"text":"Test alert"}' \
  YOUR_SLACK_WEBHOOK_URL

# Expected response: "ok"
# If you get "no_service", your webhook URL is invalid/expired

# Step 5: If webhook is invalid, update .env with new webhook URL
# Then restart: docker compose restart alert_watcher
```

#### 2. Watcher not processing logs (Critical Fix)

**Symptoms:** Watcher running but not detecting failovers or errors.

**Root Cause:** Nginx logs may be symlinked to `/dev/stdout` instead of actual files, preventing the watcher from reading them via the shared volume.

**Solution:**
```bash
# Step 1: Check if logs are actual files or symlinks
docker exec nginx_proxy ls -la /var/log/nginx/
# Bad: lrwxrwxrwx ... access.log -> /dev/stdout  (symlink)
# Good: -rw-r--r-- ... access.log                 (regular file)

# Step 2: If symlinks, check entrypoint.sh has the fix
cat nginx/entrypoint.sh | grep -A3 "Remove default symlinks"

# The fix should include:
# rm -f /var/log/nginx/access.log /var/log/nginx/error.log
# touch /var/log/nginx/access.log /var/log/nginx/error.log
# chmod 644 /var/log/nginx/access.log /var/log/nginx/error.log

# Step 3: Restart nginx to apply the fix
docker compose restart nginx alert_watcher

# Step 4: Verify logs are now regular files
docker exec nginx_proxy ls -la /var/log/nginx/

# Step 5: Generate traffic and check watcher detects it
curl http://localhost:8080/version
docker logs alert_watcher --tail 10
# You should see: [INIT] Initial pool detected: blue
```

#### 3. alert_watcher container not running

**Symptoms:** `docker ps` doesn't show alert_watcher.

**Solution:**
```bash
# Start the watcher service
docker compose up -d alert_watcher

# If it fails, check logs
docker compose logs alert_watcher

# Common causes:
# - Missing Python dependencies (should auto-install)
# - Invalid SLACK_WEBHOOK_URL format
# - Port conflicts

# Restart all services if needed
docker compose down
docker compose up -d
```

#### 4. Too many false positive alerts

**Symptoms:** Receiving alerts for minor, transient issues.

**Solution:**
```bash
# Adjust thresholds in .env
ERROR_RATE_THRESHOLD=5      # Increase from 2 to 5 (less sensitive)
WINDOW_SIZE=500             # Increase from 200 to 500 (larger sample)
ALERT_COOLDOWN_SEC=600      # Increase from 300 to 600 (10 min cooldown)

# Restart watcher to apply changes
docker compose restart alert_watcher
```

#### 5. Invalid webhook URL error

**Symptoms:** Webhook test returns "invalid_token" or "no_service".

**Solution:**
1. Your Slack webhook URL has expired or been revoked
2. Go to https://api.slack.com/apps
3. Select your app → "Incoming Webhooks"
4. Generate a new webhook URL
5. Update `.env` with the new URL
6. Restart: `docker compose restart alert_watcher`

#### 6. Containers not starting

**Symptoms:** Services fail to start or immediately exit.

**Solution:**
```bash
# Check for port conflicts
sudo lsof -i :8080  # Nginx public port
sudo lsof -i :8081  # Blue pool
sudo lsof -i :8082  # Green pool

# View service logs
docker compose logs

# Clean restart
docker compose down
docker compose up -d

# If still failing, rebuild
docker compose down
docker compose up -d --build
```

---

## 📁 Project Structure

```
.
├── docker-compose.yml                          # Service orchestration
├── .env                                        # Environment configuration (not in git)
├── .env.example                                # Configuration template
├── .gitignore                                  # Git ignore patterns
│
├── nginx/
│   ├── nginx.conf.template                     # Nginx config with enhanced logging
│   └── entrypoint.sh                           # Nginx startup script (with log fix)
│
├── watcher.py                                  # Log monitoring & alerting service
├── requirements.txt                            # Python dependencies (requests)
├── test_alerts.sh                              # Automated test suite
│
├── README.md                                   # This file - Quick start guide
├── runbook.md                                  # Operator response procedures
├── DECISION.md                                 # Architecture decisions & rationale
└── Blue-Green_Deployment_Documentation.docx    # Complete documentation (Word)
```

**Key File Changes:**
- `nginx/entrypoint.sh` - Fixed to create actual log files instead of symlinks (critical for monitoring)
- `watcher.py` - Real-time log monitoring with Slack integration
- `docker-compose.yml` - Orchestrates all 4 services with shared volumes for log access

---

## 🔐 Security Notes

- **Never commit** your actual `SLACK_WEBHOOK_URL` to version control
- Use `.env` for secrets (already in `.gitignore`)
- Rotate webhook URLs periodically
- Restrict Slack app permissions to only necessary channels

---

## 📊 Expected Slack Alerts

Your Slack channel should receive three types of alerts:

### 🔄 Failover Detected
```
🔄 FAILOVER DETECTED
━━━━━━━━━━━━━━━━━━━━
From Pool: blue
To Pool: green
New Release: green-v1.0.0
Action Required: Check health of the failed pool
Debug Command: `docker logs app_blue`
━━━━━━━━━━━━━━━━━━━━
Timestamp: 2025-10-30 14:32:15 UTC
```

### 🚨 High Error Rate
```
🚨 ERROR RATE DETECTED
━━━━━━━━━━━━━━━━━━━━
Error rate is 5.2% over the last 200 requests
Threshold: 2.0%

Error Rate: 5.2%
Threshold: 2.0%
Window Size: 200 requests
Errors: 10/200
Current Pool: green
Action Required: Inspect upstream logs and consider toggling pools
Debug Command: `docker logs app_green`
━━━━━━━━━━━━━━━━━━━━
Timestamp: 2025-10-30 14:33:42 UTC
```

### ✅ Recovery Detected
```
✅ RECOVERY DETECTED
━━━━━━━━━━━━━━━━━━━━
Service has recovered and returned to blue pool
Primary pool is now serving traffic again.

Recovered Pool: blue
Release: blue-v1.0.0
Status: Primary pool is healthy
Action: Monitor for stability
━━━━━━━━━━━━━━━━━━━━
Timestamp: 2025-10-30 14:35:18 UTC
```

---

## 🔧 Recent Improvements & Fixes

### Critical Fix: Nginx Log File Configuration (2025-10-30)

**Issue:** The alert_watcher service was not detecting failovers or sending Slack notifications even though the webhook URL was valid and the watcher was running.

**Root Cause:** The default nginx:alpine Docker image creates symbolic links for `/var/log/nginx/access.log` → `/dev/stdout`. While this allows viewing logs via `docker logs`, it prevented the alert_watcher container from reading the logs through the shared volume.

**Fix Implemented:** Modified `nginx/entrypoint.sh` to:
1. Remove the default symlinks on container startup
2. Create actual log files with proper permissions
3. Ensure logs are written to real files accessible via shared volume

```bash
# Added to nginx/entrypoint.sh
rm -f /var/log/nginx/access.log /var/log/nginx/error.log
touch /var/log/nginx/access.log /var/log/nginx/error.log
chmod 644 /var/log/nginx/access.log /var/log/nginx/error.log
```

**Verification:**
```bash
# Check logs are regular files (not symlinks)
docker exec nginx_proxy ls -la /var/log/nginx/
# Should show: -rw-r--r-- (not lrwxrwxrwx)

# Verify watcher is processing logs
docker logs alert_watcher | grep "INIT"
# Should show: [INIT] Initial pool detected: blue
```

**Impact:** This fix enables the complete observability pipeline, allowing real-time monitoring and Slack notifications for all alert types (failovers, high error rates, and recovery events).

---

## 📖 Additional Documentation

- **[runbook.md](runbook.md)** - Operator response procedures for each alert type
- **[DECISION.md](DECISION.md)** - Architecture decisions and design rationale
- **[Blue-Green_Deployment_Documentation.docx](Blue-Green_Deployment_Documentation.docx)** - Complete project documentation in Word format

---

## 👤 Author

**Macdonald Daniel**

---

## 📝 License

This project is for educational and demonstration purposes.

---

## 🙏 Acknowledgments

- HNG Internship Stage 2/3 Project
- Blue-Green Deployment Pattern
- Nginx Upstream Module
- Slack Incoming Webhooks


