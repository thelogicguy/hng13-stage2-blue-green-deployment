# 🧠 DECISION.md — Blue/Green Deployment with Nginx

This document summarizes the key technical choices made for this Blue/Green Deployment project and the reasons behind them. It’s meant to give anyone reading the repo a quick understanding of *why* things were built the way they are.

---

## 🎯 Goal

Build a simple, reliable deployment setup where traffic automatically switches between two identical Node.js services (Blue and Green) using Nginx — with no downtime or failed requests.

---

## ⚙️ Key Decisions

### 1. Blue/Green Setup
We used two identical app instances (Blue and Green). Blue serves as the active environment, while Green stays on standby for quick failover or testing new releases.

**Why:** This pattern makes rollbacks instant and ensures users don’t experience downtime during deployment or errors.

---

### 2. Nginx as Gateway and Failover Manager
Nginx was chosen to sit in front of both services. It routes requests to the active pool and automatically retries on the backup if the primary fails.

**Why:** Nginx is lightweight, fast, supports “primary/backup” upstreams out of the box, and needs no extra dependencies. It’s also easy to reload and monitor.

---

### 3. Docker Compose for Orchestration
Everything runs under Docker Compose for consistency and portability.

**Why:** It allows the entire system to spin up with a single command (`docker compose up -d`), works locally and on EC2, and keeps setup simple.

---

### 4. Environment Variables and Version Headers
Parameterized setup with `.env` variables like `ACTIVE_POOL`, `RELEASE_ID_BLUE`, and `RELEASE_ID_GREEN`.  
Each app exposes headers (`X-App-Pool`, `X-Release-Id`) to show which instance handled a request.

**Why:** This helps in debugging, CI/CD automation, and tracing which release is live — without touching the code.

---

### 5. Failover and Chaos Testing
The apps include `/chaos/start` and `/chaos/stop` endpoints to simulate downtime and confirm that Nginx switches over automatically.

**Why:** Testing failure scenarios proves that the system is actually resilient.

---

## ✅ Summary

This setup focuses on **simplicity, reliability, and transparency** — using just Nginx and Docker Compose to achieve zero-downtime deployments.  
It’s a clean, minimal approach that still demonstrates real-world Blue/Green and failover behavior without the overhead of Kubernetes or complex service meshes.

---

**Author:** Macdonald Daniel  
**Date:** October 2025  
**Purpose:** Document the reasoning behind the design choices in this project.
