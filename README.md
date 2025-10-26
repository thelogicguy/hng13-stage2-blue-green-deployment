# 🟦🟩 Blue-Green Deployment with Nginx Auto-Failover

This project demonstrates a **Blue/Green deployment strategy** using **Nginx upstreams** for seamless failover between two identical Node.js application instances.

---

## 🚀 Overview

- **Blue (active)** and **Green (backup)** services run as separate containers.  
- **Nginx** routes traffic to the active service (Blue by default).  
- If Blue fails (timeout or 5xx errors), Nginx automatically retries the request to Green — clients do not experience any error or downtime.  
- Failover happens **within the same request** (instant switch).  
- Headers are preserved and forwarded to clients.

---

**Author:** Macdonald Daniel
**Purpose:** Demonstrate resilient Blue/Green deployments with Nginx auto-failover.

