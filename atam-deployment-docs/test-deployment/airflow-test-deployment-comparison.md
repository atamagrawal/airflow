# Airflow Test Deployment — Infrastructure Comparison
## EC2 vs ECS Fargate vs Kubernetes (EKS)

**Version:** 1.0  
**Date:** March 2026  
**Purpose:** Decision guide for choosing the right infrastructure approach for on-demand Airflow test deployments

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Approach Overview](#2-approach-overview)
3. [Detailed Comparison Matrix](#3-detailed-comparison-matrix)
4. [Startup Time](#4-startup-time)
5. [Scalability](#5-scalability)
6. [Isolation Model](#6-isolation-model)
7. [Cost Analysis](#7-cost-analysis)
8. [Operational Complexity](#8-operational-complexity)
9. [Session Lifecycle Management](#9-session-lifecycle-management)
10. [Networking & Routing](#10-networking--routing)
11. [Security](#11-security)
12. [Developer Experience](#12-developer-experience)
13. [Risk Analysis](#13-risk-analysis)
14. [Decision Guide](#14-decision-guide)
15. [Migration Paths](#15-migration-paths)
16. [Final Recommendation](#16-final-recommendation)

---

## 1. Executive Summary

This document compares three infrastructure approaches for delivering on-demand, per-session Airflow test environments to customers. Each approach represents a different point on the spectrum between **simplicity** and **scalability**.

| | EC2 + Docker Compose | ECS Fargate | Kubernetes (EKS) |
|---|:---:|:---:|:---:|
| **Best for** | < 20 sessions | 20–500 sessions | 500+ sessions |
| **Startup time** | 3–5 min | 30–60 sec | 5–30 sec |
| **Cost / session** | ~$0.09 | ~$0.11 | ~$0.05 |
| **Operational complexity** | ⭐ Low | ⭐⭐ Medium | ⭐⭐⭐ High |
| **Recommended** | Prototype / PoC | **Production default** | Hyper-scale |

> **TL;DR:** Start with **ECS Fargate** for production. Move to **Kubernetes** only when you exceed 500 concurrent sessions or need warm pod pools, custom scheduling, or multi-cloud portability.

---

## 2. Approach Overview

### 2.1 EC2 + Docker Compose

Each session launches a dedicated EC2 instance. Docker Compose runs all Airflow components (webserver, scheduler, PostgreSQL) on that single VM. The instance is terminated when the session ends.

```
Customer Request
      │
      ▼
  AWS EC2 (t3.large)
  ┌───────────────────────┐
  │  Docker Compose       │
  │  ├── webserver :8080  │
  │  ├── scheduler        │
  │  └── postgres         │
  └───────────────────────┘
  Terminated after TTL
```

**Core mechanism:** `boto3.run_instances()` → User Data bootstraps Docker → TTL watchdog runs `shutdown -h now`

---

### 2.2 ECS Fargate

Each session launches an ECS Fargate task — a group of containers (webserver, scheduler, PostgreSQL sidecar) running on AWS-managed serverless infrastructure. An ALB routes traffic to each session via path-based rules.

```
Customer Request
      │
      ▼
  ECS Fargate Task
  ┌───────────────────────┐
  │  Container: webserver │
  │  Container: scheduler │
  │  Container: postgres  │
  └───────────────────────┘
        ▲
        │ ALB path: /session/{id}/*
  Stopped after TTL
```

**Core mechanism:** `ecs.run_task()` → Containers start from ECR image → EventBridge Lambda terminates after TTL

---

### 2.3 Kubernetes (EKS)

Each session gets a dedicated Kubernetes namespace containing all Airflow resources (Deployments, StatefulSet, Services, NetworkPolicies). NGINX Ingress routes traffic. A warm pool of pre-initialized namespaces enables near-instant session start.

```
Customer Request
      │
      ├── Warm pool hit? ──YES──► Claim namespace (5 sec)
      │
      └── NO ──► Create namespace (30 sec)
                 Apply manifests
                 Wait for pods

  Namespace: session-{id}
  ┌──────────────────────────────┐
  │  Deployment: webserver       │
  │  Deployment: scheduler       │
  │  StatefulSet: postgres       │
  │  NetworkPolicy: deny-cross   │
  │  ResourceQuota: cpu+mem cap  │
  └──────────────────────────────┘
        ▲
        │ NGINX Ingress: /session/{id}/*
  Namespace deleted after TTL
```

**Core mechanism:** `kubernetes-client` creates namespace + applies manifests → EventBridge Lambda deletes namespace after TTL

---

## 3. Detailed Comparison Matrix

| Dimension | EC2 + Docker Compose | ECS Fargate | Kubernetes (EKS) |
|---|---|---|---|
| **Startup time (cold)** | 3–5 min | 30–60 sec | 20–30 sec |
| **Startup time (warm)** | N/A | N/A | 5–10 sec |
| **Max concurrent sessions** | ~16 (vCPU quota) | Hundreds | Thousands |
| **Cost per session (1 hr)** | ~$0.09 | ~$0.11 | ~$0.05 |
| **Cluster baseline cost** | $0 | $0 | ~$200/month |
| **Infrastructure mgmt** | Full VM ownership | AWS manages infra | AWS manages control plane |
| **Session isolation** | Full VM | Container (shared kernel) | Namespace (RBAC + NetworkPolicy) |
| **Network isolation** | Security Group per EC2 | Security Group per task | NetworkPolicy per namespace |
| **Resource quotas** | Manual cgroups | Task CPU/memory limits | Kubernetes ResourceQuota |
| **Node autoscaling** | Manual EC2 launch | Automatic (Fargate) | Karpenter (~60 sec) |
| **Warm pool** | ❌ | ❌ | ✅ |
| **Multi-cloud** | ❌ | ❌ AWS only | ✅ |
| **Custom scheduling** | ❌ | ❌ | ✅ |
| **Spot instance support** | ✅ (risky mid-session) | ❌ | ✅ (safe via node groups) |
| **Operational expertise needed** | Docker | Docker + AWS ECS | Kubernetes + AWS EKS |
| **Time to first deployment** | Hours | 1–2 days | 1–2 weeks |
| **Debugging difficulty** | SSH + `docker logs` | `aws ecs execute-command` | `kubectl exec` + `kubectl logs` |
| **TTL cleanup mechanism** | Watchdog on VM + Lambda | EventBridge Lambda | EventBridge Lambda |
| **Cleanup completeness** | Instance terminated | Task stopped + TG deleted | Namespace deleted (cascades all) |
| **TLS termination** | Manual (Let's Encrypt) | ACM via ALB | cert-manager |
| **URL routing** | One URL per EC2 | ALB path rules | NGINX Ingress path rules |

---

## 4. Startup Time

Startup time is the most customer-visible metric — it's how long from "I clicked Start Session" to "I can access Airflow."

### 4.1 Cold Start Breakdown

```
EC2 + Docker Compose
  ├── EC2 instance boot            ~60 sec
  ├── apt-get + Docker install     ~90 sec
  ├── Docker image pull (2 images) ~60 sec
  ├── Airflow db migrate           ~30 sec
  └── Webserver ready              ~30 sec
  ─────────────────────────────────────────
  Total:                       3–5 minutes

ECS Fargate
  ├── Task provisioning            ~10 sec
  ├── ECR image pull (cached)      ~10 sec
  ├── Container startup            ~10 sec
  ├── Airflow db migrate           ~20 sec
  └── Webserver ready              ~20 sec
  ─────────────────────────────────────────
  Total:                       30–60 seconds

Kubernetes (EKS) — Cold Start
  ├── Namespace creation           ~2 sec
  ├── Pod scheduling               ~5 sec
  ├── Image pull (node cache)      ~5 sec
  ├── Airflow init job             ~15 sec
  └── Webserver ready              ~10 sec
  ─────────────────────────────────────────
  Total:                       20–30 seconds

Kubernetes (EKS) — Warm Pool Hit
  ├── Claim namespace label        ~1 sec
  ├── Update Ingress rule          ~2 sec
  └── Return URL                   ~2 sec
  ─────────────────────────────────────────
  Total:                       5–10 seconds
```

### 4.2 Startup Time Chart

```
                    Startup Time (seconds)
                    0      60     120    180    240    300
                    │      │      │      │      │      │
EC2 (cold)          ████████████████████████████████████  3–5 min
ECS Fargate (cold)  ████                                  30–60 sec
K8s (cold)          ███                                   20–30 sec
K8s (warm pool)     █                                     5–10 sec
```

> **Verdict:** Kubernetes with warm pool wins. ECS Fargate is acceptable. EC2 is too slow for a responsive customer experience.

---

## 5. Scalability

### 5.1 Concurrent Session Limits

#### EC2 Hard Limit

```
AWS Default On-Demand vCPU Quota (us-east-1): 32 vCPUs
t3.large = 2 vCPUs
─────────────────────────────────────────────────────
Max concurrent sessions = 32 / 2 = 16 sessions

To get to 100 sessions: request quota increase to 200 vCPUs
To get to 500 sessions: request quota increase to 1,000 vCPUs
  → Slow process, requires AWS approval, takes days
```

#### ECS Fargate

```
AWS Default Fargate vCPU Quota: 256 vCPUs (separate from EC2)
Task requires 2 vCPUs
─────────────────────────────────────────────────────
Max concurrent sessions = 256 / 2 = 128 sessions (default)
With quota increase:     → Hundreds, relatively easy to increase
```

#### Kubernetes (EKS)

```
Karpenter provisions nodes on-demand in ~60 seconds
m5.xlarge = 4 vCPU → 2 sessions per node (with headroom)
─────────────────────────────────────────────────────
Default limit:  500 vCPU cap in Karpenter NodePool config
With adjustment: Thousands of sessions
  → No AWS quota requests needed beyond node instance quota
```

### 5.2 Scale-Up Behavior

| Scenario | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| 10 sessions | ✅ Fast, no issues | ✅ Instant | ✅ Instant |
| 50 sessions | ⚠️ Near quota limit | ✅ Fine | ✅ Fine |
| 100 sessions | ❌ Quota exceeded | ✅ With quota increase | ✅ Karpenter adds nodes |
| 500 sessions | ❌ Infeasible | ⚠️ Needs quota increase | ✅ Designed for this |
| 1000 sessions | ❌ Not possible | ❌ Very high cost | ✅ Astronomer-grade |

---

## 6. Isolation Model

Session isolation is critical — one customer's test DAGs must not interfere with another's.

### 6.1 Isolation Comparison

#### EC2 — Full VM Isolation (Strongest)

```
Customer A          Customer B
    │                   │
    ▼                   ▼
EC2 Instance A      EC2 Instance B
(separate kernel)   (separate kernel)
(separate network)  (separate network)
```

- **Strongest possible isolation** — separate Linux kernel, separate network stack, separate processes
- Customers literally cannot interfere even if they try
- Overkill for test environments; the cost (3–5 min startup, $0.09/session) is the tradeoff

#### ECS Fargate — Container Isolation

```
Customer A Task         Customer B Task
┌───────────────┐       ┌───────────────┐
│  containers   │       │  containers   │
│  (shared      │       │  (shared      │
│   kernel)     │       │   kernel)     │
└───────────────┘       └───────────────┘
       AWS-managed microVM boundary
```

- Fargate uses **Firecracker microVMs** — each task gets its own lightweight VM boundary
- Stronger than regular Docker containers, weaker than full EC2
- Network isolation via Security Groups — tasks can only be reached through the ALB
- Practical isolation is very strong for test environments

#### Kubernetes — Namespace Isolation

```
Namespace: session-A          Namespace: session-B
┌──────────────────────┐      ┌──────────────────────┐
│  Pods, Services      │      │  Pods, Services       │
│  Secrets, PVCs       │◄────►│  Secrets, PVCs        │
│  NetworkPolicy:DENY  │  ✗   │  NetworkPolicy:DENY   │
└──────────────────────┘      └──────────────────────┘
  (cross-namespace traffic blocked by NetworkPolicy)
```

- **NetworkPolicy** explicitly blocks pod-to-pod traffic across namespaces
- **ResourceQuota** prevents one session from starving another of CPU/memory
- **RBAC** scopes service account permissions to their own namespace
- Slightly weaker than VM-level isolation (shared kernel) but sufficient for test workloads

### 6.2 Isolation Strength Summary

```
Strongest ◄──────────────────────────────────────► Most Practical

   EC2 (Full VM)   ECS Fargate (microVM)   Kubernetes (Namespace)
        │                  │                        │
   Max isolation      Strong isolation         Good isolation
   Max cost           Medium cost              Min cost at scale
```

---

## 7. Cost Analysis

### 7.1 Cost Per Session

| Cost Component | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Compute | $0.083/hr (t3.large) | $0.040/hr (2 vCPU) | ~$0.048/hr (¼ m5.xlarge) |
| Memory | included | $0.018/hr (4 GB) | included |
| Storage | $0.002/hr (20 GB EBS) | Ephemeral (free) | $0.0001/hr (5 GB PVC) |
| Load balancer | $0.003/hr | $0.002/hr (shared) | $0.002/hr (shared NLB) |
| Cluster overhead | $0 | $0 | ~$0.002/hr (amortized) |
| **Total / session** | **~$0.09** | **~$0.11** | **~$0.05** |

> Kubernetes is cheapest at scale because sessions are **bin-packed** onto shared nodes.
> ECS Fargate is most expensive per session because each session gets dedicated Fargate vCPUs.
> EC2 is mid-range but has the highest **hidden cost**: operational overhead (patching, monitoring VMs).

### 7.2 Monthly Cost at Different Scale Levels

Assuming average session = 1 hour, sessions/day as shown:

| Sessions/Day | EC2 Monthly | ECS Fargate Monthly | Kubernetes Monthly |
|---|---|---|---|
| 10 | ~$27 | ~$33 | ~$215 (cluster cost dominates) |
| 50 | ~$135 | ~$165 | ~$290 |
| 100 | ~$270 | ~$330 | ~$350 |
| 500 | ❌ Quota limit | ~$1,650 | ~$750 |
| 1,000 | ❌ Not feasible | ~$3,300 | ~$1,500 |

> **Break-even point:** Kubernetes becomes cheaper than ECS Fargate at ~200 sessions/day due to bin-packing efficiency. At low volumes (< 50 sessions/day), ECS Fargate has lower total cost once you factor in the ~$200/month EKS cluster fee.

### 7.3 Cost at Low Volume (< 50 Sessions/Day)

```
ECS Fargate:   $0.11 × 50 sessions/day × 30 days = $165/month
               No cluster baseline cost
               Total = $165/month ✅

Kubernetes:    $0.05 × 50 sessions/day × 30 days = $75/month
               + EKS cluster fee = $200/month
               Total = $275/month ❌ (cluster cost dominates)
```

> At low volume, ECS Fargate is cheaper. Kubernetes cluster baseline (~$200/month) only pays off when session volume is high enough for bin-packing savings to exceed it.

### 7.4 Hidden Costs

| Hidden Cost | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Engineering time to set up | 1 day | 2 days | 2 weeks |
| Ongoing ops / patching | High (VMs to patch) | None | Medium (K8s upgrades) |
| Incident debugging time | Medium | Low | High |
| Training new engineers | Low (Docker basics) | Low-Medium | High (K8s expertise) |
| Quota increase requests | Frequent | Occasional | Rare |

---

## 8. Operational Complexity

### 8.1 Day-1 Setup Complexity

```
EC2 + Docker Compose
  ├── Write User Data bootstrap script       Medium
  ├── Configure Security Groups              Easy
  ├── Set up DynamoDB session table          Easy
  └── Deploy FastAPI Session Manager         Easy
  ──────────────────────────────────────────────────
  Estimated setup time:  4–8 hours
  Team expertise needed: Python + Docker + AWS basics

ECS Fargate
  ├── Create ECS cluster + task definition   Medium
  ├── Build + push custom ECR image          Easy
  ├── Configure ALB + target groups          Medium
  ├── Set up DynamoDB + EventBridge Lambda   Easy
  └── Deploy FastAPI Session Manager         Easy
  ──────────────────────────────────────────────────
  Estimated setup time:  1–2 days
  Team expertise needed: Docker + ECS + ALB + Lambda

Kubernetes (EKS)
  ├── Create EKS cluster (eksctl)            Medium
  ├── Install Karpenter                      Hard
  ├── Install NGINX Ingress Controller       Medium
  ├── Install cert-manager                   Medium
  ├── Write K8s manifests (8 files)          Hard
  ├── Implement warm pool manager            Hard
  ├── Configure RBAC + IRSA                  Hard
  ├── Set up Prometheus + Grafana            Medium
  └── Deploy Session Manager API             Medium
  ──────────────────────────────────────────────────
  Estimated setup time:  1–2 weeks
  Team expertise needed: Kubernetes + EKS + Karpenter + Helm
```

### 8.2 Day-2 Operational Complexity

| Operation | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Debug a failed session | SSH + `docker logs` | `aws ecs execute-command` | `kubectl logs pod -n session-{id}` |
| Upgrade Airflow version | Update AMI + User Data | Update ECR image tag | Update image in manifests |
| Scale cluster capacity | Request quota increase | Automatic (Fargate) | Karpenter auto-provisions |
| Rotate SSL certificate | Manual renewal | ACM auto-renews | cert-manager auto-renews |
| Roll back bad Airflow image | Stop instances, redeploy | Update task definition | `kubectl rollout undo` |
| Investigate noisy neighbor | Check EC2 metrics | Check ECS task metrics | `kubectl top pod -n session-{id}` |
| Add new customer-facing feature | Update User Data script | Update task definition | Update manifests + redeploy |

### 8.3 Failure Modes

| Failure | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Session fails to start | EC2 boot failure → CloudWatch logs | Task fails → ECS events | Pod CrashLoopBackOff → `kubectl describe pod` |
| Session not cleaned up | Instance keeps running → Cost leak | Task keeps running → Cost leak | Namespace stays → K8s TTL controller |
| Airflow unhealthy mid-session | Docker container exits, no auto-restart | ECS restarts container (essential flag) | Kubernetes liveness probe restarts pod |
| Concurrent session spike | Hard EC2 quota failure | Soft: returns error if Fargate quota hit | Karpenter provisions nodes, slight delay |
| Instance/node failure | Session lost (no HA) | Task rescheduled on new Fargate infra | Pod rescheduled on new node |

---

## 9. Session Lifecycle Management

### 9.1 Session Creation Flow

```
EC2 Approach:
  POST /sessions/start
    → boto3.run_instances()              # 1–2 sec
    → Wait: instance running             # ~60 sec
    → User Data runs on VM               # ~3–4 min
    → Poll /health                       # ~30 sec
    → Return URL                         Total: ~4–5 min

ECS Fargate Approach:
  POST /sessions/start
    → ecs.run_task()                     # 1 sec
    → Wait: task running                 # ~10 sec
    → Create ALB target group + rule     # ~5 sec
    → Poll /health                       # ~30–45 sec
    → Return URL                         Total: ~45–60 sec

Kubernetes Approach (warm pool):
  POST /sessions/start
    → Check warm pool → HIT              # ~1 sec
    → Claim namespace (update label)     # ~2 sec
    → Update Ingress rule                # ~2 sec
    → Return URL                         Total: ~5 sec

Kubernetes Approach (cold start):
  POST /sessions/start
    → kubectl create namespace           # ~2 sec
    → Apply 8 manifests                  # ~3 sec
    → Wait: pods running                 # ~15 sec
    → Poll /health                       # ~10 sec
    → Return URL                         Total: ~30 sec
```

### 9.2 Session Termination Flow

```
EC2 Approach:
  DELETE /sessions/{id}
    → ec2.terminate_instances()          # Instant
    → Instance terminated                # ~30 sec
    → EBS volume deleted automatically  # (DeleteOnTermination=true)
    → Update DynamoDB: TERMINATED        Cost: stops immediately ✅

ECS Fargate Approach:
  DELETE /sessions/{id}
    → ecs.stop_task()                    # Instant
    → Delete ALB listener rule           # ~2 sec
    → Delete ALB target group            # ~2 sec
    → Update DynamoDB: TERMINATED        Cost: stops immediately ✅

Kubernetes Approach:
  DELETE /sessions/{id}
    → kubectl delete namespace           # Instant (async)
    → K8s cascades to all resources:
        Pods terminated                  # ~30 sec
        PVCs deleted (EBS released)      # ~60 sec
        Services, Secrets cleaned        # ~30 sec
    → Update DynamoDB: TERMINATED        Cost: stops after ~2 min
```

### 9.3 TTL Cleanup Defense-in-Depth

All three approaches use the same two-layer cleanup strategy:

| Layer | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| **Primary** | Watchdog bash script on VM (`shutdown -h now`) | EventBridge Lambda (every 5 min) | EventBridge Lambda (every 5 min) |
| **Secondary** | Orphan Lambda: terminate instances > 3 hrs old | Orphan Lambda: stop tasks > 3 hrs old | Orphan Lambda: delete namespaces > 3 hrs old |

---

## 10. Networking & Routing

### 10.1 URL Strategy

All three approaches use **path-based routing** so customers get a clean URL:

```
https://test.yourdomain.com/session/{session-id}/
```

| Routing Mechanism | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Load balancer type | ALB (or direct EC2 DNS) | ALB | NLB + NGINX Ingress |
| Rule per session | ALB listener rule | ALB listener rule | NGINX Ingress path rule |
| Rule creation time | ~5 sec | ~5 sec | ~2 sec |
| Rule limit | 100 ALB rules (default) | 100 ALB rules (default) | No limit (NGINX config) |
| TLS | ACM via ALB | ACM via ALB | cert-manager (Let's Encrypt) |
| Path rewriting | ALB doesn't rewrite | ALB doesn't rewrite | NGINX rewrites + `X-Forwarded-Prefix` |

> **Important:** EC2 and ECS Fargate hit the ALB listener rule limit at ~100 concurrent sessions. Kubernetes NGINX Ingress has no such limit — a key advantage at high concurrency.

### 10.2 ALB Rule Limit Problem (EC2 & ECS Fargate)

```
AWS ALB listener rules limit: 100 rules per listener (default)
Each session adds 1 rule
─────────────────────────────────────────────────────────────
At 95 concurrent sessions → approaching limit
At 100 concurrent sessions → new sessions fail to route
→ Requires requesting ALB rule quota increase from AWS
```

Kubernetes NGINX Ingress does not have this limit — ingress rules are stored in Kubernetes config, not in AWS resources.

---

## 11. Security

### 11.1 Security Comparison

| Security Dimension | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| **Compute boundary** | Full VM (hypervisor) | Firecracker microVM | Shared kernel (namespace) |
| **Network isolation** | Security Group per instance | Security Group per task | NetworkPolicy per namespace |
| **Secret management** | AWS Secrets Manager | AWS Secrets Manager | K8s Secrets + AWS Secrets Manager |
| **Credential scope** | Per-session Secrets Manager secret | Per-session Secrets Manager secret | Per-namespace Kubernetes Secret |
| **IAM granularity** | Instance profile (coarse) | Task role (fine-grained) | IRSA per service account (finest) |
| **Container image trust** | Private ECR | Private ECR | Private ECR |
| **TLS** | Manual or ACM | ACM (auto-renew) | cert-manager (auto-renew) |
| **Audit logging** | CloudTrail | CloudTrail + ECS API logs | CloudTrail + K8s audit log |
| **Vulnerability scanning** | Manual / Inspector | ECR image scanning | ECR scanning + Trivy/Snyk |
| **Pod security policies** | N/A | N/A | PodSecurityStandards |

### 11.2 Attack Surface

```
EC2:   Large — full VM exposed; must patch OS, Docker daemon, Airflow
ECS:   Medium — no OS to manage; attack surface = container + ECS API
K8s:   Medium-Large — no OS to manage BUT K8s API is a large attack surface
       (misconfigured RBAC is a common K8s security issue)
```

### 11.3 Secrets Lifecycle

All three approaches should rotate credentials per session:

```python
# Per-session secrets (all three approaches)
secrets = {
    "db_password":          secrets.token_urlsafe(24),
    "fernet_key":           Fernet.generate_key().decode(),
    "webserver_secret_key": secrets.token_urlsafe(32),
    "admin_password":       secrets.token_urlsafe(16),
}
```

The difference is **where** these secrets are stored:

| Approach | Secret Storage | Auto-Deleted? |
|---|---|---|
| EC2 | AWS Secrets Manager | Manually on session end |
| ECS Fargate | AWS Secrets Manager | Manually on session end |
| Kubernetes | Kubernetes Secret in namespace | ✅ Automatically when namespace deleted |

---

## 12. Developer Experience

### 12.1 Team Skill Requirements

| Skill | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Docker / Docker Compose | Required | Required | Required |
| AWS basics (EC2, IAM, VPC) | Required | Required | Required |
| ECS concepts (tasks, clusters, services) | Not needed | Required | Not needed |
| ALB / target groups | Optional | Required | Not needed |
| Kubernetes concepts | Not needed | Not needed | Required |
| Helm | Not needed | Not needed | Required |
| Karpenter / cluster autoscaling | Not needed | Not needed | Required |

### 12.2 Debugging a Broken Session

```bash
# EC2 — simple and familiar
ssh ec2-user@<public-ip>
docker logs airflow-webserver
docker ps -a
cat /var/log/airflow-setup.log

# ECS Fargate — no SSH needed (use execute-command)
aws ecs execute-command \
  --cluster airflow-test-cluster \
  --task <task-arn> \
  --container airflow-webserver \
  --interactive \
  --command "/bin/bash"

aws logs get-log-events \
  --log-group-name /airflow-test/sessions \
  --log-stream-name airflow-webserver/{session-id}

# Kubernetes — most powerful but steepest learning curve
kubectl get pods -n session-{id}
kubectl describe pod airflow-webserver-xxx -n session-{id}
kubectl logs airflow-webserver-xxx -n session-{id}
kubectl exec -it airflow-webserver-xxx -n session-{id} -- /bin/bash
kubectl get events -n session-{id} --sort-by='.lastTimestamp'
```

### 12.3 Deploying an Airflow Version Update

```bash
# EC2 — update User Data script + AMI
# 1. Update Docker image tag in user_data.py
# 2. New sessions pick it up automatically
# (No rolling update — each new session starts fresh)

# ECS Fargate — update task definition
aws ecs register-task-definition \
  --family airflow-test-session \
  --container-definitions '[{"image": "ecr.../airflow-test:2.10.0", ...}]'
# New sessions use new task definition revision automatically

# Kubernetes — update image in manifests + rolling deploy
kubectl set image deployment/airflow-webserver \
  airflow-webserver=ecr.../airflow-test:2.10.0 \
  -n session-{id}
# Or update manifest template — new sessions use new image
```

---

## 13. Risk Analysis

### 13.1 Risk Matrix

| Risk | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| **Session fails to start** | Medium — EC2 boot failures, User Data errors | Low — container startup is reliable | Low — pod scheduling is well-tested |
| **Session not cleaned up (cost leak)** | Medium — watchdog could fail | Low — Lambda + ECS stop is reliable | Low — namespace deletion cascades |
| **AWS quota hit** | **High** — 16 sessions before hitting limit | Medium — 128 sessions (default) | Low — node quota is high |
| **Runaway cost** | High — idle EC2 keeps billing | Medium — idle Fargate task keeps billing | Low — namespace idle = low compute |
| **Security breach (cross-session)** | Low — full VM isolation | Low — microVM boundary | Medium — shared kernel; needs NetworkPolicy |
| **Data leak between sessions** | Low — instance terminated | Low — task stopped, ephemeral storage gone | Low — PVC deleted with namespace |
| **Single region failure** | High — all sessions down | High — all sessions down | Medium — can replicate to multi-region K8s |
| **Team can't operate it** | Low risk | Low-Medium risk | **High risk** — K8s expertise is rare |

### 13.2 Vendor Lock-in

```
EC2:          AWS-only — entire system is AWS-specific
ECS Fargate:  AWS-only — ECS API is proprietary
Kubernetes:   Portable — manifests work on GKE, AKS, self-managed K8s
              (AWS-specific: Karpenter, ECR, ALB — replaceable)
```

---

## 14. Decision Guide

### 14.1 Choose EC2 + Docker Compose if…

- ✅ You are building a **proof of concept** or internal prototype
- ✅ You have **fewer than 20 customers** and low session frequency
- ✅ Your team has no ECS or Kubernetes experience
- ✅ You need the **simplest possible implementation** (ship in hours, not days)
- ✅ Startup time of 3–5 minutes is acceptable to your customers
- ✅ You expect to **migrate to ECS Fargate later** and want to validate the concept first
- ❌ **Avoid if:** You expect growth beyond 20 concurrent sessions

### 14.2 Choose ECS Fargate if…

- ✅ You are building the **first production version** of this feature
- ✅ You expect **20–500 concurrent sessions**
- ✅ Your team knows Docker and basic AWS but not Kubernetes
- ✅ You want **serverless infrastructure** — no nodes, no patching, no cluster management
- ✅ You want to be **running in 1–2 days**, not 2 weeks
- ✅ A 30–60 second startup time is acceptable
- ✅ You are AWS-only and have no multi-cloud requirements
- ❌ **Avoid if:** You need warm pod pools (sub-10-second starts) or expect 500+ concurrent sessions

### 14.3 Choose Kubernetes (EKS) if…

- ✅ You expect **500+ concurrent sessions**
- ✅ You need **sub-10-second session starts** (warm pool)
- ✅ You have or are hiring **Kubernetes expertise** on your team
- ✅ You need **fine-grained scheduling** (spot vs on-demand per customer tier)
- ✅ You need **multi-cloud** portability (plan to offer on GCP or Azure)
- ✅ You are building a **platform product** (not just an internal tool)
- ✅ Long-term: session volume is high enough that bin-packing savings exceed cluster overhead
- ❌ **Avoid if:** You are a small team without K8s expertise — operational risk is high

### 14.4 Quick Decision Flowchart

```
How many concurrent sessions do you expect?
│
├── < 20 sessions
│     └── Is startup time < 5 min acceptable?
│           ├── YES → EC2 + Docker Compose ✅
│           └── NO  → ECS Fargate ✅
│
├── 20–500 sessions
│     └── Do you have Kubernetes expertise?
│           ├── YES → Kubernetes (EKS) ✅
│           └── NO  → ECS Fargate ✅
│
└── 500+ sessions
      └── ECS Fargate can handle it with quota increases,
          but Kubernetes is more cost-efficient and scalable
          └── Kubernetes (EKS) ✅
```

---

## 15. Migration Paths

### 15.1 EC2 → ECS Fargate Migration

**Effort:** 1–2 days | **Risk:** Low

The Session Manager API, DynamoDB schema, and TTL Lambda are nearly identical. The main change is replacing `boto3.run_instances()` with `ecs.run_task()` and adding ALB target group management.

```
EC2 Component              →  ECS Fargate Equivalent
─────────────────────────────────────────────────────
User Data script           →  ECR Docker image (pre-baked)
EC2 Security Group         →  ECS Task Security Group
EC2 instance public DNS    →  ALB path-based rule
TTL watchdog (on VM)       →  EventBridge Lambda (external)
boto3.run_instances()      →  ecs.run_task()
ec2.terminate_instances()  →  ecs.stop_task() + ALB cleanup
```

### 15.2 ECS Fargate → Kubernetes Migration

**Effort:** 2–3 weeks | **Risk:** Medium

The biggest changes are the routing layer (ALB → NGINX Ingress) and resource definitions (task definitions → K8s manifests). The Session Manager API logic is similar but calls the Kubernetes client instead of the ECS API.

```
ECS Fargate Component       →  Kubernetes Equivalent
───────────────────────────────────────────────────────────
ECS Task Definition         →  K8s Deployment + StatefulSet YAML
Fargate task group          →  Kubernetes Namespace
ALB listener rules          →  NGINX Ingress rules
ECS task IAM role           →  Kubernetes IRSA ServiceAccount
Task CPU/memory limits      →  ResourceQuota + Pod resource limits
ECS task security group     →  Kubernetes NetworkPolicy
ecs.run_task()              →  kubectl create namespace + apply manifests
ecs.stop_task()             →  kubectl delete namespace
No warm pool                →  Warm namespace pool (new capability)
```

### 15.3 Recommended Migration Strategy

```
Phase 1 (Month 0–1):    EC2 + Docker Compose
                        → Validate concept, first customers
                        → Learn failure modes cheaply
                        → Build Session Manager API

Phase 2 (Month 2–3):    Migrate to ECS Fargate
                        → Reuse Session Manager API
                        → Replace launch/terminate code only
                        → Handles 20–500 sessions

Phase 3 (Month 6+):     Evaluate Kubernetes
                        → Only if hitting ECS Fargate limits
                        → Or if multi-cloud needed
                        → Or if warm pool startup is required
```

---

## 16. Final Recommendation

### For Most Teams: **ECS Fargate**

ECS Fargate offers the best balance of startup speed, scalability, operational simplicity, and cost for teams building this feature from scratch.

```
✅ Production-ready
✅ 30–60 second startup
✅ Handles hundreds of concurrent sessions
✅ No cluster to manage
✅ Familiar AWS tooling
✅ Running in 1–2 days
✅ $0.11/session
```

### Scale Trigger: Move to Kubernetes when you hit these thresholds

| Threshold | Indicator | Action |
|---|---|---|
| **100+ concurrent sessions** | ECS vCPU quota requests becoming frequent | Evaluate Kubernetes |
| **Startup time complaints** | Customers notice 30–60 sec wait | Add Kubernetes warm pool |
| **$1,500+/month on Fargate** | Kubernetes bin-packing saves money | Evaluate Kubernetes |
| **Multi-cloud requirement** | Customer demands GCP or Azure | Kubernetes only option |
| **ALB rule limit hit** | Approaching 100 concurrent sessions | Kubernetes NGINX Ingress has no limit |

### One-Line Summary Per Approach

> **EC2:** _"Works great until it doesn't — start here to learn, not to scale."_

> **ECS Fargate:** _"The production default — serverless, scalable, and your team will actually be able to operate it."_

> **Kubernetes:** _"The most powerful option — but respect the operational cost. Only graduate here when ECS Fargate becomes the bottleneck."_

---

*This document should be revisited when session volumes exceed 200 concurrent sessions or when AWS introduces significant pricing changes to ECS Fargate or EKS.*
