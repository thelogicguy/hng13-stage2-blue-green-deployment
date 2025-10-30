# 📚 Blue-Green Deployment Operations Runbook

This runbook provides step-by-step instructions for operators to respond to alerts from the blue-green deployment monitoring system.

---

## 🎯 Alert Types & Response Procedures

### 1. 🔄 FAILOVER DETECTED

**What It Means:**
Traffic has automatically switched from the primary pool to the backup pool due to health issues or errors in the primary pool.

**Alert Details:**
- From Pool: The pool that was serving traffic (e.g., `blue`)
- To Pool: The pool now serving traffic (e.g., `green`)
- Time: When the failover occurred

**Root Causes:**
- Application crashes or restarts in primary pool
- High error rates (5xx responses) from primary pool
- Timeout issues in primary pool
- Container health check failures

**Immediate Actions:**

1. **Check the failed pool's health status:**
   ```bash
   docker ps
   docker logs app_<failed_pool>
   ```

2. **Verify the backup pool is healthy:**
   ```bash
   curl -i http://localhost:8080/version
   curl -i http://localhost:8080/healthz
   ```

3. **Investigate the failed pool's logs:**
   ```bash
   # Check recent errors
   docker logs --tail 100 app_<failed_pool>

   # Follow logs in real-time
   docker logs -f app_<failed_pool>
   ```

4. **Check Nginx error logs:**
   ```bash
   docker exec nginx_proxy cat /var/log/nginx/error.log
   ```

5. **Test the failed pool directly (bypassing Nginx):**
   ```bash
   # If blue failed (port 8081)
   curl -i http://localhost:8081/version
   curl -i http://localhost:8081/healthz

   # If green failed (port 8082)
   curl -i http://localhost:8082/version
   curl -i http://localhost:8082/healthz
   ```

**Recovery Actions:**

1. **If the issue is temporary (e.g., chaos testing):**
   ```bash
   # Stop chaos mode on the failed pool
   curl -X POST http://localhost:<port>/chaos/stop
   ```

2. **If the container crashed:**
   ```bash
   # Restart the failed pool
   docker compose restart app_<failed_pool>

   # Wait for health checks to pass
   docker ps
   ```

3. **If the issue persists:**
   ```bash
   # Check resource usage
   docker stats --no-stream

   # Inspect container details
   docker inspect app_<failed_pool>

   # Rebuild and restart if necessary
   docker compose up -d --force-recreate app_<failed_pool>
   ```

4. **Monitor for recovery alert:**
   - Once the primary pool recovers, you should receive a "RECOVERY" alert
   - Traffic will automatically switch back to the primary pool

**Escalation:**
- If the failed pool cannot be recovered within 15 minutes, escalate to the development team
- Gather logs and container details before escalating

---

### 2. 🚨 HIGH ERROR RATE DETECTED

**What It Means:**
The percentage of 5xx errors over the last N requests (default: 200) has exceeded the configured threshold (default: 2%).

**Alert Details:**
- Error Rate: Current error percentage
- Threshold: Configured acceptable error rate
- Window Size: Number of requests analyzed
- Errors: Count of errors (e.g., `5/200`)
- Current Pool: Which pool is experiencing errors

**Root Causes:**
- Application bugs or exceptions
- Database connection issues
- Downstream service failures
- Resource exhaustion (CPU, memory)
- Configuration errors

**Immediate Actions:**

1. **Verify current error rate:**
   ```bash
   # Check recent Nginx logs
   docker exec nginx_proxy tail -50 /var/log/nginx/access.log | grep "status=5"
   ```

2. **Identify the error types:**
   ```bash
   # Check application logs for exceptions
   docker logs --tail 100 app_<current_pool> | grep -i error
   docker logs --tail 100 app_<current_pool> | grep -i exception
   ```

3. **Check system resources:**
   ```bash
   # Monitor resource usage
   docker stats --no-stream

   # Check if containers are being restarted
   docker ps -a
   ```

4. **Test application endpoints:**
   ```bash
   # Test the problematic pool directly
   curl -i http://localhost:<direct_port>/version

   # Test through Nginx
   curl -i http://localhost:8080/version
   ```

5. **Check dependent services:**
   ```bash
   # If using external databases or APIs, verify connectivity
   docker logs app_<current_pool> | grep -i connection
   docker logs app_<current_pool> | grep -i timeout
   ```

**Resolution Actions:**

1. **If error rate is transient:**
   - Monitor for a few minutes to see if it stabilizes
   - Check if the issue correlates with external events (deployments, traffic spikes)

2. **If specific requests are failing:**
   ```bash
   # Analyze which URIs are failing
   docker exec nginx_proxy grep "status=5" /var/log/nginx/access.log | grep -o "uri=[^ ]*" | sort | uniq -c
   ```

3. **If the current pool is unhealthy:**
   ```bash
   # Manually trigger chaos mode to force failover to the other pool
   curl -X POST http://localhost:<current_pool_port>/chaos/start?mode=error

   # Traffic will automatically failover to the backup pool
   # This gives you time to investigate the problematic pool
   ```

4. **If both pools are experiencing errors:**
   - This indicates a systemic issue (not pool-specific)
   - Check external dependencies, databases, APIs
   - Review recent deployments or configuration changes
   - Consider rolling back to a known-good version

5. **After resolving the issue:**
   ```bash
   # Stop chaos mode if it was enabled
   curl -X POST http://localhost:<port>/chaos/stop

   # Restart services if necessary
   docker compose restart
   ```

**Escalation:**
- If error rate remains above threshold for >10 minutes, escalate to development team
- If error rate is extremely high (>10%), escalate immediately
- Document the specific error messages and steps taken

---

### 3. ✅ RECOVERY DETECTED

**What It Means:**
The primary pool has recovered and is now successfully serving traffic again after a previous failover.

**Alert Details:**
- Recovered Pool: Which pool has recovered
- Release: Current release ID serving traffic
- Status: Health status of the recovered pool

**Immediate Actions:**

1. **Verify the recovery is stable:**
   ```bash
   # Monitor logs for the next 5-10 minutes
   docker logs -f app_<recovered_pool>

   # Check for any error patterns
   docker logs --since 5m app_<recovered_pool> | grep -i error
   ```

2. **Test the recovered pool:**
   ```bash
   # Test through Nginx
   curl -i http://localhost:8080/version

   # Verify the correct pool is responding
   curl -i http://localhost:8080/version | grep "X-App-Pool"
   ```

3. **Monitor error rates:**
   ```bash
   # Watch the Nginx access logs for any 5xx responses
   docker exec nginx_proxy tail -f /var/log/nginx/access.log
   ```

4. **Check that both pools are healthy:**
   ```bash
   docker ps
   docker compose ps
   ```

**Post-Recovery Actions:**

1. **Document the incident:**
   - Record the time of failover and recovery
   - Note the root cause if identified
   - Document any manual interventions performed

2. **Review logs for the failure period:**
   ```bash
   # Export logs for analysis
   docker logs app_<recovered_pool> > failover_incident_$(date +%Y%m%d_%H%M%S).log
   ```

3. **Verify monitoring is functioning:**
   ```bash
   # Check watcher logs
   docker logs alert_watcher
   ```

4. **If the root cause is unclear:**
   - Schedule a post-mortem review
   - Analyze patterns that led to the failure
   - Consider adding additional monitoring

**Escalation:**
- If recovery is followed by another failover within 30 minutes, escalate immediately
- This indicates an unstable system that requires urgent attention

---

## 🛠️ Maintenance Mode

**When to Use:**
Use maintenance mode to suppress alerts during planned maintenance windows or when performing controlled failover testing.

**Enable Maintenance Mode:**

**Option 1: Environment Variable (requires restart)**
```bash
# Edit .env file
MAINTENANCE_MODE=true

# Restart the watcher
docker compose restart alert_watcher
```

**Option 2: File-Based Flag (no restart required)**
```bash
# Create the maintenance flag file
docker exec alert_watcher touch /app/state/maintenance.flag

# Verify maintenance mode is active
docker logs alert_watcher | grep -i maintenance
```

**Disable Maintenance Mode:**

**Option 1: Environment Variable**
```bash
# Edit .env file
MAINTENANCE_MODE=false

# Restart the watcher
docker compose restart alert_watcher
```

**Option 2: Remove Flag File**
```bash
# Remove the maintenance flag file
docker exec alert_watcher rm -f /app/state/maintenance.flag

# Restart watcher to re-enable alerts
docker compose restart alert_watcher
```

**Best Practices:**
- Always announce maintenance windows to the team
- Document the maintenance window start and end times
- Re-enable alerts immediately after maintenance
- Test that alerts are working after re-enabling

---

## 🔍 Troubleshooting

### Alert Not Received

**Check watcher service:**
```bash
docker ps | grep alert_watcher
docker logs alert_watcher
```

**Verify Slack webhook:**
```bash
docker exec alert_watcher env | grep SLACK_WEBHOOK_URL
```

**Test Slack webhook manually:**
```bash
curl -X POST -H 'Content-Type: application/json' \
  -d '{"text":"Test alert from Blue-Green deployment"}' \
  YOUR_SLACK_WEBHOOK_URL
```

### False Positive Alerts

**Adjust thresholds:**
```bash
# Edit .env file
ERROR_RATE_THRESHOLD=5  # Increase threshold
WINDOW_SIZE=500         # Increase window size
ALERT_COOLDOWN_SEC=600  # Increase cooldown

# Restart watcher
docker compose restart alert_watcher
```

### Too Many Alerts

**Enable cooldown:**
- Default cooldown is 5 minutes per alert type
- Increase `ALERT_COOLDOWN_SEC` in .env to reduce alert frequency

**Enable maintenance mode:**
- If system is known to be unstable, enable maintenance mode temporarily

### Watcher Not Processing Logs

**Check log volume:**
```bash
# Verify logs are being written
docker exec nginx_proxy ls -lh /var/log/nginx/

# Check log content
docker exec nginx_proxy tail /var/log/nginx/access.log
```

**Restart watcher:**
```bash
docker compose restart alert_watcher
docker logs -f alert_watcher
```

---

## 📊 Useful Commands

### View Real-Time Logs
```bash
# Nginx access logs
docker exec nginx_proxy tail -f /var/log/nginx/access.log

# Watcher logs
docker logs -f alert_watcher

# Application logs
docker logs -f app_blue
docker logs -f app_green
```

### Manual Failover Testing
```bash
# Trigger failure on blue pool
curl -X POST http://localhost:8081/chaos/start?mode=error

# Verify green is serving traffic
curl -i http://localhost:8080/version | grep "X-App-Pool: green"

# Stop chaos to trigger recovery
curl -X POST http://localhost:8081/chaos/stop

# Verify blue is serving traffic again
curl -i http://localhost:8080/version | grep "X-App-Pool: blue"
```

### Check System Health
```bash
# All containers status
docker compose ps

# Resource usage
docker stats --no-stream

# Health checks
curl http://localhost:8080/healthz
curl http://localhost:8081/healthz
curl http://localhost:8082/healthz
```

### Configuration Check
```bash
# View current configuration
docker exec alert_watcher env | grep -E "THRESHOLD|WINDOW|COOLDOWN|MAINTENANCE"

# View Nginx configuration
docker exec nginx_proxy cat /etc/nginx/nginx.conf
```

---

## 📞 Escalation Matrix

| Severity | Condition | Action | Time to Escalate |
|----------|-----------|--------|------------------|
| **P1 - Critical** | Both pools failing, service down | Page on-call engineer immediately | 0 minutes |
| **P2 - High** | Repeated failovers, error rate >10% | Notify development team | 5 minutes |
| **P3 - Medium** | Single failover, error rate 5-10% | Create incident ticket, notify team | 15 minutes |
| **P4 - Low** | Single failover, error rate <5% | Log incident, investigate during business hours | Next business day |

---

## 📝 Incident Documentation Template

When escalating or documenting an incident, include:

```
Incident Time: <timestamp>
Alert Type: <failover|error_rate|recovery>
Affected Pool: <blue|green>
Error Rate: <percentage>
Actions Taken:
- <action 1>
- <action 2>

Root Cause: <if known>
Resolution: <what fixed it>
Logs Attached: <yes|no>
```

---

## 🔗 Additional Resources

- **Docker Compose Docs**: https://docs.docker.com/compose/
- **Nginx Upstream Docs**: http://nginx.org/en/docs/http/ngx_http_upstream_module.html
- **Slack Webhook Setup**: https://api.slack.com/messaging/webhooks
- **Project Repository**: [Link to your repository]

---

**Last Updated:** 2025-10-30
**Maintained By:** DevOps Team
**Contact:** [Your contact information]
