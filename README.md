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

🐳 Running the App
1. Start All Services

- Run the project with Docker Compose: docker compose up -d


2. Check Running Containers
- docker ps

3. Test the Active Service :By default, Nginx routes all requests to Blue.
- curl -i http://localhost:8080/version

4. Simulate Failure (Failover Test): Trigger a failure on Blue:
- curl -X POST http://localhost:8081/chaos/start?mode=error

5. Now test again through Nginx
- curl -i http://localhost:8080/version

6. To restore Blue
- curl -X POST http://localhost:8081/chaos/stop


- **Author:** Macdonald Daniel
- **Purpose:** Demonstrate resilient Blue/Green deployments with Nginx auto-failover.

