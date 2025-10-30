# 🧠 DECISION.md — Blue/Green Deployment with Nginx

This document summarizes the key technical choices made for this Blue/Green Deployment project and the reasons behind them. It's meant to give anyone reading the repo a quick understanding of *why* things were built the way they are.

---

## 🎯 Goal

Build a simple, reliable deployment setup where traffic automatically switches between two identical Node.js services (Blue and Green) using Nginx — with no downtime or failed requests. Enhanced with real-time observability and Slack alerting for production-ready monitoring.

---

## ⚙️ Key Decisions

### 1. Blue/Green Setup
We used two identical app instances (Blue and Green). Blue serves as the active environment, while Green stays on standby for quick failover or testing new releases.

**Why:** This pattern makes rollbacks instant and ensures users don't experience downtime during deployment or errors.

**Implementation Details:**
- Both pools run in separate Docker containers with identical configurations
- Each pool has its own direct port (8081 for blue, 8082 for green) for testing and chaos engineering
- Pools are health-checked every 5 seconds to ensure availability
- Environment variables control which pool is primary and which is backup

---

### 2. Nginx as Gateway and Failover Manager
Nginx was chosen to sit in front of both services. It routes requests to the active pool and automatically retries on the backup if the primary fails.

**Why:** Nginx is lightweight, fast, supports "primary/backup" upstreams out of the box, and needs no extra dependencies. It's also easy to reload and monitor.

**Implementation Details:**
- Nginx upstream module with `backup` directive for automatic failover
- Aggressive timeout settings (2-3 seconds) for fast failover detection
- `proxy_next_upstream` configured to retry on errors (5xx), timeouts, and connection failures
- Maximum 2 upstream tries to prevent excessive latency
- Request-level failover: failed requests are automatically retried on the backup pool

**Failover Configuration:**
```nginx
upstream backend {
    server app_${ACTIVE_POOL}:${PORT} max_fails=2 fail_timeout=10s;
    server app_${BACKUP_POOL}:${PORT} backup max_fails=2 fail_timeout=10s;
}
```

---

### 3. Docker Compose for Orchestration
Everything runs under Docker Compose for consistency and portability.

**Why:** It allows the entire system to spin up with a single command (`docker compose up -d`), works locally and on EC2, and keeps setup simple.

**Service Architecture:**
- **app_blue & app_green**: Identical Node.js application instances
- **nginx**: Reverse proxy with failover logic
- **alert_watcher**: Python service for log monitoring and Slack alerts

**Volume Strategy:**
- `nginx_logs`: Shared volume between nginx and alert_watcher for log access
- `watcher_state`: Persistent volume for maintenance mode flag

---

### 4. Environment Variables and Version Headers
Parameterized setup with `.env` variables like `ACTIVE_POOL`, `RELEASE_ID_BLUE`, and `RELEASE_ID_GREEN`.
Each app exposes headers (`X-App-Pool`, `X-Release-Id`) to show which instance handled a request.

**Why:** This helps in debugging, CI/CD automation, and tracing which release is live — without touching the code.

**Configuration Philosophy:**
- All secrets and configuration in `.env` (never committed)
- Template-based nginx configuration with environment variable substitution
- Runtime configuration changes without rebuilding containers

---

### 5. Enhanced Logging for Observability
Custom nginx log format captures detailed request metadata including pool, release, status codes, and latency.

**Why:** Standard nginx logs don't capture enough information to detect failovers or calculate error rates effectively.

**Log Format Design:**
```
pool=$upstream_http_x_app_pool
release=$upstream_http_x_release_id
status=$status
upstream_status=$upstream_status
upstream=$upstream_addr
request_time=$request_time
upstream_response_time=$upstream_response_time
method=$request_method
uri=$request_uri
time=$time_iso8601
```

**Key Insight:** By including the pool name in logs, we can track which backend served each request and detect when traffic switches between pools.

---

### 6. Python-Based Log Watcher
A dedicated Python service monitors nginx logs in real-time and sends Slack alerts.

**Why Python:**
- Excellent string parsing and regex support
- Simple HTTP client (requests library)
- Easy to read and maintain
- No need for heavy monitoring stack (Prometheus, Grafana, etc.)

**Architecture Decision:**
- Tail logs using `tail -F` subprocess (handles log rotation)
- Parse logs with regex for reliable field extraction
- Sliding window analysis for error rate calculation
- State machine for tracking failovers and recovery

**Alert Logic:**
- **Failover Detection**: Track `last_known_pool`, alert when it changes
- **Error Rate**: Sliding window of last N requests (default 200), alert when 5xx errors exceed threshold
- **Recovery**: After failover, detect return to original pool
- **Cooldown**: Prevent alert spam with configurable cooldown periods (default 5 minutes)

---

### 7. Slack Webhooks for Notifications
Incoming webhooks for alert delivery instead of email, PagerDuty, or other channels.

**Why:**
- Simple to set up (no authentication, no API keys)
- Rich message formatting with blocks
- Integrates with most team communication workflows
- Instant notifications
- Free tier sufficient for most use cases

**Message Design:**
- Emoji indicators for quick visual identification (🔄, 🚨, ✅)
- Structured blocks with key information
- Actionable debug commands included in alerts
- Timestamp for correlation with logs

---

### 8. Failover and Chaos Testing
The apps include `/chaos/start` and `/chaos/stop` endpoints to simulate downtime and confirm that Nginx switches over automatically.

**Why:** Testing failure scenarios proves that the system is actually resilient.

**Test Strategy:**
- `mode=error`: Return 5xx errors to trigger failover
- `mode=latency`: Introduce artificial delays to test timeout handling
- `mode=crash`: Simulate application crashes
- Automated test suite (`test_alerts.sh`) validates all scenarios

---

## 🔧 Implementation Challenges & Solutions

### Challenge 1: Nginx Log Access
**Problem:** Default nginx:alpine image symlinks logs to `/dev/stdout`, preventing other containers from reading them via shared volumes.

**Solution:** Modified `nginx/entrypoint.sh` to remove symlinks and create actual log files on startup:
```bash
rm -f /var/log/nginx/access.log /var/log/nginx/error.log
touch /var/log/nginx/access.log /var/log/nginx/error.log
chmod 644 /var/log/nginx/access.log /var/log/nginx/error.log
```

**Why This Matters:** Without real log files, the alert_watcher cannot tail logs from the shared volume, breaking the entire observability system.

---

### Challenge 2: Alert Spam Prevention
**Problem:** Transient errors could trigger excessive alerts, causing alert fatigue.

**Solutions Implemented:**
1. **Cooldown Period**: 5-minute cooldown between alerts of the same type
2. **Sliding Window**: Error rate calculated over 200 requests (configurable), not per-request
3. **Maintenance Mode**: Ability to suppress alerts during planned maintenance
4. **Configurable Thresholds**: Easy adjustment via `.env` without code changes

---

### Challenge 3: Detecting Recovery vs New Failover
**Problem:** Need to distinguish between return to primary pool (recovery) and switch to backup pool (failover).

**Solution:** Track failover state with two variables:
- `failover_occurred`: Boolean flag set on initial failover
- `failover_from_pool`: Remember which pool originally failed

Recovery is detected when traffic returns to `failover_from_pool`, then state is reset.

---

## 🎨 Design Principles

1. **Simplicity Over Complexity**: Use simple tools correctly rather than complex tools partially
2. **Fail Fast**: Aggressive timeouts ensure quick detection and failover
3. **Observable by Default**: Every request generates structured logs
4. **Configuration Over Code**: Changes via `.env`, not code modifications
5. **Testability**: Built-in chaos endpoints and automated test suite
6. **Production-Ready**: Real monitoring, real alerts, real operational procedures

---

## 🚀 What We Didn't Choose (and Why)

### Kubernetes
**Why Not:** Adds significant complexity for a feature (blue-green deployment) that nginx handles natively. K8s makes sense for large-scale orchestration but is overkill here.

### Prometheus + Grafana
**Why Not:** Heavy stack requiring additional infrastructure. Our custom log watcher achieves the same goals with 200 lines of Python.

### Commercial Monitoring (DataDog, New Relic)
**Why Not:** Requires accounts, API keys, and often costs money. Slack webhooks are free and sufficient.

### Load Balancer Services (HAProxy, Envoy)
**Why Not:** Nginx is simpler, more widely known, and perfectly suited for this use case.

---

## ✅ Summary

This setup focuses on **simplicity, reliability, and transparency** — using just Nginx and Docker Compose to achieve zero-downtime deployments with production-grade observability.

**Key Achievements:**
- Zero-downtime deployments and rollbacks
- Automatic failover in milliseconds
- Real-time monitoring with Slack integration
- Comprehensive testing and validation
- Clear operational procedures

It's a clean, minimal approach that demonstrates real-world Blue/Green deployment and failover behavior without the overhead of Kubernetes or complex service meshes, while still providing enterprise-level observability.

---

**Author:** Macdonald Daniel
**Date:** October 2025
**Last Updated:** October 30, 2025
**Purpose:** Document the reasoning behind the design choices, implementation challenges, and solutions in this project.
