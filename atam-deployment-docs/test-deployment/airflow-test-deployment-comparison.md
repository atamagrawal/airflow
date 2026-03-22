# Airflow Test Deployment — Infrastructure Comparison
## EC2 vs ECS Fargate vs Kubernetes (EKS)

**Version:** 2.0
**Date:** March 2026
**Purpose:** Decision guide for choosing the right infrastructure approach for on-demand Airflow test deployments

---

## Changelog from v1.0

| Change | Why |
|---|---|
| Removed AWS vCPU quota comparisons | Soft limits — trivially increased on request, not a real differentiator |
| Updated K8s model to single namespace | Namespace-per-session adds overhead with no benefit for ephemeral sessions |
| Updated isolation section for K8s | NetworkPolicy at pod label level, not namespace level |
| Updated cleanup section for K8s | Label selector delete, not namespace delete |
| Updated risk matrix | Removed quota risk rows |
| Updated migration paths | Reflects single namespace K8s approach |

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
| **Best for** | PoC / low volume | Production default | High scale / platform |
| **Startup time** | 3–5 min | 30–60 sec | 20–30 sec (cold) / 5 sec (warm) |
| **Cost / session** | ~$0.09 | ~$0.11 | ~$0.05 |
| **Operational complexity** | ⭐ Low | ⭐⭐ Medium | ⭐⭐⭐ High |
| **Recommended** | Prototype / PoC | **Production default** | Hyper-scale |

> **TL;DR:** All three approaches can scale to handle any number of sessions — AWS quota limits are soft and trivially increased on request. The real differentiators are **startup time**, **cost efficiency**, and **operational complexity**. Start with **ECS Fargate** for production. Move to **Kubernetes** when you need bin-packing efficiency, warm pools, or multi-cloud.

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

**Core mechanism:** `ecs.run_task()` → Containers start from pre-pulled ECR image → EventBridge Lambda terminates after TTL

---

### 2.3 Kubernetes (EKS) — Single Namespace

All sessions share **one Kubernetes namespace** (`airflow-sessions`). Each session's pods — webserver, scheduler, PostgreSQL — are labeled with the session ID. Sessions are created by applying labeled resources and terminated by deleting everything with that label.

```
Customer Request
      │
      ▼
  namespace: airflow-sessions  (one namespace, created once at cluster setup)
  ┌──────────────────────────────────────────────┐
  │  webserver-{session-id}   [label: id=abc123] │
  │  scheduler-{session-id}   [label: id=abc123] │
  │  postgres-{session-id}    [label: id=abc123] │
  └──────────────────────────────────────────────┘
        ▲
        │ NGINX Ingress: /session/{id}/*
  kubectl delete --selector session-id=abc123  (after TTL)
```

**Core mechanism:** `kubernetes-client` creates labeled resources in shared namespace → EventBridge Lambda deletes by label selector after TTL

> **Why single namespace?** Namespaces are designed for long-lived tenants with different RBAC or policy requirements. For ephemeral, identical test sessions, labels are the right primitive. Think of it like Docker Compose: `docker compose -p session-abc up` and `docker compose -p session-abc down` — same idea, just on Kubernetes.

---

## 3. Detailed Comparison Matrix

| Dimension | EC2 + Docker Compose | ECS Fargate | Kubernetes (EKS) |
|---|---|---|---|
| **Startup time (cold)** | 3–5 min | 30–60 sec | 20–30 sec |
| **Startup time (warm pool)** | ❌ N/A | ❌ N/A | ✅ 5–10 sec |
| **Session scalability** | Unlimited (soft quota) | Unlimited (soft quota) | Unlimited (Karpenter) |
| **Cost per session (1 hr)** | ~$0.09 | ~$0.11 | ~$0.05 |
| **Cluster baseline cost** | $0 | $0 | ~$200/month |
| **Infrastructure management** | Full VM ownership | AWS manages everything | AWS manages control plane |
| **Session isolation unit** | Full VM (hypervisor) | Firecracker microVM | Pod labels + NetworkPolicy |
| **Network isolation** | Security Group per EC2 | Security Group per task | NetworkPolicy per session (pod label) |
| **Resource governance** | VM-level (fully dedicated) | Task CPU/memory limits | LimitRange + pod resource limits |
| **Bin-packing (shared nodes)** | ❌ One VM per session | ❌ Dedicated Fargate vCPU per session | ✅ Multiple sessions per node |
| **Warm pool** | ❌ | ❌ | ✅ |
| **Multi-cloud portability** | ❌ | ❌ AWS only | ✅ GKE, AKS, self-managed |
| **Custom scheduling** | ❌ | ❌ | ✅ Spot vs on-demand per tier |
| **Spot instance support** | ✅ (risky mid-session) | ❌ | ✅ Safe via dedicated node groups |
| **Operational expertise** | Docker | Docker + ECS | Kubernetes + EKS |
| **Time to first deployment** | Hours | 1–2 days | 1–2 weeks |
| **Debugging** | SSH + `docker logs` | `aws ecs execute-command` | `kubectl logs -l session-id=X` |
| **TTL cleanup** | Watchdog on VM + Lambda | EventBridge Lambda | EventBridge Lambda (label selector) |
| **Cleanup operation** | `ec2.terminate_instances()` | `ecs.stop_task()` + ALB rule delete | `kubectl delete --selector session-id=X` |
| **TLS termination** | ACM via ALB | ACM via ALB | cert-manager (auto-renew) |
| **URL routing** | ALB path rules | ALB path rules | NGINX Ingress (no rule limit) |

---

## 4. Startup Time

Startup time is the most customer-visible metric — it's how long from "I clicked Start Session" to "I can access Airflow."

### 4.1 Cold Start Breakdown

```
EC2 + Docker Compose
  ├── EC2 instance boot            ~60 sec
  ├── apt-get + Docker install     ~90 sec
  ├── Docker image pull            ~60 sec
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
  ├── Pod scheduling               ~5 sec
  ├── Image pull (node cache)      ~5 sec
  ├── Airflow init job             ~15 sec
  └── Webserver ready              ~10 sec
  ─────────────────────────────────────────
  Total:                       20–30 seconds

Kubernetes (EKS) — Warm Pool Hit
  ├── Claim pre-warmed pods        ~1 sec
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

### 4.3 Why EC2 Is Always Slow

EC2's startup time is structural — every session boots a full OS, installs Docker, pulls images, and runs Airflow init from scratch. There is no way to pre-warm this. ECS and Kubernetes both benefit from pre-pulled images cached on infrastructure.

> **Verdict:** If startup time matters to customers, EC2 is ruled out. ECS Fargate is acceptable. Kubernetes with a warm pool is the best possible experience.

---

## 5. Scalability

### 5.1 AWS Quota Limits Are Not a Real Differentiator

All three approaches have default AWS service quota limits. These are **soft limits** — billing safeguards for new accounts, not architectural ceilings. Any of them can be increased on request within hours:

```bash
# Example: increase any quota
aws service-quotas request-service-quota-increase \
  --service-code ec2 \
  --quota-code L-1216C47A \
  --desired-value 2000
```

Whether you're using EC2, ECS Fargate, or Kubernetes, you can run as many concurrent sessions as you need once the appropriate quota is raised. This is not a meaningful basis for choosing between approaches.

### 5.2 What Actually Differentiates Scalability

The real scalability differences are about **cost efficiency and infrastructure behavior at scale**, not raw session count.

#### EC2 — Linear Cost, No Sharing

Each session gets a dedicated VM regardless of actual utilisation:

```
t3.large per session:   2 vCPU,  8 GB RAM  (dedicated)
Session actually uses:  1.25 vCPU, 2.5 GB RAM
─────────────────────────────────────────────────
Wasted per session:     ~38% CPU,  ~70% RAM
```

At 500 sessions that waste compounds to ~275 idle vCPUs and ~2.7 TB of unused RAM being paid for.

#### ECS Fargate — Dedicated vCPU Per Task, No Sharing

Fargate allocates dedicated CPU and memory per task. There is no bin-packing. Every session pays for 2 vCPUs whether the webserver is busy or idle.

#### Kubernetes — Bin-Packed, Shared Nodes

Multiple sessions share nodes efficiently. An `m5.xlarge` (4 vCPU, 16 GB) fits 2–3 sessions:

```
m5.xlarge node (4 vCPU, 16 GB)
  ├── session-abc:  ~1.25 vCPU,  2.5 GB
  ├── session-def:  ~1.25 vCPU,  2.5 GB
  └── node OS/kubelet: ~0.5 vCPU, 1 GB
  ──────────────────────────────────────
  Node utilisation: ~75% CPU, ~37% RAM  ✅ Efficient
```

This is why Kubernetes cost per session (~$0.05) is roughly half of ECS Fargate (~$0.11) at scale.

### 5.3 Scale Behaviour Summary

| Scenario | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| 10 sessions | ✅ Simple, fine | ✅ Simple, fine | ✅ Fine (cluster overhead dominates cost) |
| 100 sessions | ✅ Works | ✅ Works well | ✅ Efficient |
| 500 sessions | ✅ Works, expensive | ✅ Works, expensive | ✅ Most cost-efficient |
| 1,000+ sessions | ✅ Works, very wasteful | ✅ Works, high cost | ✅ Designed for this |

---

## 6. Isolation Model

### 6.1 Isolation Per Approach

#### EC2 — Full VM Isolation (Strongest)

```
Customer A          Customer B
    │                   │
    ▼                   ▼
EC2 Instance A      EC2 Instance B
Separate kernel     Separate kernel
Separate network    Separate network
Separate storage    Separate storage
```

Strongest possible isolation — separate Linux kernel per session. One session physically cannot interfere with another.

#### ECS Fargate — Firecracker MicroVM

```
Customer A Task              Customer B Task
┌──────────────────────┐     ┌──────────────────────┐
│ webserver            │     │ webserver            │
│ scheduler            │     │ scheduler            │
│ postgres             │     │ postgres             │
│ [Firecracker microVM]│     │ [Firecracker microVM]│
└──────────────────────┘     └──────────────────────┘
```

AWS Fargate uses Firecracker microVMs — each task gets its own kernel. Stronger than regular Docker, effectively equivalent to VM-level isolation for practical purposes.

#### Kubernetes — Pod Label + NetworkPolicy

```
namespace: airflow-sessions  (all sessions share this one namespace)

session-id=abc                     session-id=def
┌───────────────────────┐          ┌───────────────────────┐
│ webserver-abc         │          │ webserver-def         │
│ scheduler-abc         │◄───✗────►│ scheduler-def         │
│ postgres-abc          │          │ postgres-def          │
└───────────────────────┘          └───────────────────────┘
  NetworkPolicy: pods can only communicate with
  pods sharing the same session-id label
```

Sessions share a Linux kernel (same node). Isolation is enforced through:
- **NetworkPolicy** — only pods with the same `session-id` label can communicate
- **Pod resource limits** — explicit CPU/memory limits per pod prevent resource starvation
- **Per-session Secrets** — `airflow-secrets-{session-id}` is only mountable by pods of that session
- **Per-session Services** — `postgres-{session-id}` DNS resolves only to that session's postgres pod

### 6.2 Isolation Strength vs Practicality

```
Strongest isolation                              Most practical at scale

EC2 (separate VM)    ECS Fargate (microVM)    K8s (pod labels + NetworkPolicy)
       │                     │                              │
  Max isolation          Strong isolation           Good isolation
  Max cost               Medium cost                Min cost
  Slowest startup        Medium startup             Fastest startup
```

For Airflow test sessions, Kubernetes pod-level isolation is sufficient. The attack surface (a kernel exploit across sessions) is not a realistic threat in this context.

---

## 7. Cost Analysis

### 7.1 Cost Per Session

| Cost Component | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Compute | $0.083/hr (t3.large, dedicated) | $0.040/hr (2 vCPU Fargate) | ~$0.048/hr (½ m5.xlarge, bin-packed) |
| Memory | included | $0.018/hr (4 GB) | included |
| Storage | $0.002/hr (20 GB EBS) | Ephemeral (free) | $0.0001/hr (5 GB PVC) |
| Load balancer | $0.003/hr (ALB shared) | $0.002/hr (ALB shared) | $0.002/hr (NLB shared) |
| Cluster overhead | $0 | $0 | ~$0.002/hr (EKS amortised) |
| **Total / session** | **~$0.09** | **~$0.11** | **~$0.05** |

### 7.2 Monthly Cost at Different Volumes

Assuming average session = 1 hour:

| Sessions/Day | EC2 Monthly | ECS Fargate Monthly | Kubernetes Monthly |
|---|---|---|---|
| 10 | ~$27 | ~$33 | ~$215 (cluster baseline dominates) |
| 50 | ~$135 | ~$165 | ~$290 |
| 100 | ~$270 | ~$330 | ~$350 |
| 200 | ~$540 | ~$660 | ~$500 (K8s becomes cheaper) |
| 500 | ~$1,350 | ~$1,650 | ~$950 |
| 1,000 | ~$2,700 | ~$3,300 | ~$1,700 |

### 7.3 Break-Even Point

Kubernetes has a ~$200/month cluster baseline (EKS control plane + system nodes). Bin-packing savings pay off this overhead at:

```
ECS Fargate at 200 sessions/day:   $0.11 × 200 × 30 = $660/month
Kubernetes at 200 sessions/day:    $0.05 × 200 × 30 + $200 = $500/month

Break-even: ~150–200 sessions/day
Below this: ECS Fargate is cheaper total
Above this: Kubernetes is cheaper total
```

### 7.4 Hidden Costs

| Hidden Cost | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Engineering setup time | 1 day | 2 days | 1–2 weeks |
| Ongoing ops / patching | High (VMs to patch) | None | Medium (K8s upgrades) |
| Incident debugging | Medium | Low | High |
| Engineer training | Low | Low-Medium | High (K8s is a full discipline) |

---

## 8. Operational Complexity

### 8.1 Day-1 Setup Effort

```
EC2 + Docker Compose
  ├── Write User Data bootstrap script       Medium
  ├── Configure Security Groups              Easy
  ├── Set up DynamoDB + TTL Lambda           Easy
  └── Deploy FastAPI Session Manager         Easy
  ────────────────────────────────────────────────
  Time:       4–8 hours
  Expertise:  Python + Docker + AWS basics

ECS Fargate
  ├── Create ECS cluster + task definition   Medium
  ├── Build + push ECR image                 Easy
  ├── Configure ALB + target groups          Medium
  ├── Set up DynamoDB + EventBridge Lambda   Easy
  └── Deploy FastAPI Session Manager         Easy
  ────────────────────────────────────────────────
  Time:       1–2 days
  Expertise:  Docker + ECS + ALB + Lambda

Kubernetes (EKS)
  ├── Create EKS cluster (eksctl)            Medium
  ├── Install Karpenter                      Hard
  ├── Install NGINX Ingress Controller       Medium
  ├── Install cert-manager                   Medium
  ├── Write K8s manifest templates           Medium
  ├── Configure RBAC + IRSA                  Hard
  ├── Set up Prometheus + Grafana            Medium
  └── Deploy Session Manager API             Medium
  ────────────────────────────────────────────────
  Time:       1–2 weeks
  Expertise:  Kubernetes + EKS + Karpenter + Helm
```

### 8.2 Day-2 Operations

| Operation | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Debug failed session | SSH + `docker logs` | `aws ecs execute-command` | `kubectl logs -l session-id=X` |
| Upgrade Airflow version | Update image tag in User Data | Update image tag in task definition | Update image tag in manifest templates |
| Scale up capacity | Automatic (new EC2 per session) | Automatic (Fargate serverless) | Karpenter auto-provisions nodes |
| Rotate TLS certificate | ACM auto-renews | ACM auto-renews | cert-manager auto-renews |
| Investigate resource usage | EC2 CloudWatch metrics | ECS task CloudWatch metrics | `kubectl top pods -l session-id=X` |

### 8.3 Failure Modes

| Failure | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Session fails to start | User Data error → CloudWatch logs | Task fails → ECS events | Pod `CrashLoopBackOff` → `kubectl describe` |
| Session not cleaned up | Instance keeps running → cost leak | Task keeps running → cost leak | Pods keep running → Lambda fallback cleanup |
| Airflow unhealthy mid-session | Container exits, no restart | ECS restarts container automatically | K8s liveness probe restarts pod |
| Node/infra failure | Session lost | Task rescheduled by Fargate | Pod rescheduled on healthy node |

---

## 9. Session Lifecycle Management

### 9.1 Session Creation Flow

```
EC2:
  POST /sessions/start
    → boto3.run_instances()           ~2 sec
    → Wait: EC2 running               ~60 sec
    → User Data bootstrap             ~3–4 min
    → Poll /health                    ~30 sec
    → Return URL              Total:  ~4–5 min

ECS Fargate:
  POST /sessions/start
    → ecs.run_task()                  ~1 sec
    → Wait: task running              ~10 sec
    → Create ALB target group + rule  ~5 sec
    → Poll /health                    ~30–45 sec
    → Return URL              Total:  ~45–60 sec

Kubernetes (cold):
  POST /sessions/start
    → kubectl apply labeled manifests ~3 sec
    → Wait: pods running              ~15 sec
    → Poll /health                    ~10 sec
    → Return URL              Total:  ~20–30 sec

Kubernetes (warm pool):
  POST /sessions/start
    → Claim pre-warmed pods           ~1 sec
    → Update Ingress rule             ~2 sec
    → Return URL              Total:  ~5 sec
```

### 9.2 Session Termination Flow

```
EC2:
  DELETE /sessions/{id}
    → ec2.terminate_instances()       Instant API call
    → Instance + EBS volume deleted   ~30 sec
    → Update DynamoDB: TERMINATED     ✅ Cost stops immediately

ECS Fargate:
  DELETE /sessions/{id}
    → ecs.stop_task()                 Instant API call
    → Delete ALB listener rule        ~2 sec
    → Delete ALB target group         ~2 sec
    → Update DynamoDB: TERMINATED     ✅ Cost stops immediately

Kubernetes:
  DELETE /sessions/{id}
    → kubectl delete                  Instant API call
      --selector session-id={id}
    → Pods terminate gracefully       ~30 sec
    → PVC (postgres) deleted          ~60 sec
    → Update DynamoDB: TERMINATED     ✅ Cost stops after ~2 min
```

### 9.3 TTL Cleanup — Defense in Depth

| Layer | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| **Primary** | Bash watchdog on VM (`shutdown -h now`) | EventBridge Lambda every 5 min | EventBridge Lambda every 5 min |
| **Secondary** | Lambda: terminate EC2s older than 3 hrs | Lambda: stop tasks older than 3 hrs | Lambda: delete pods by label older than 3 hrs |

---

## 10. Networking & Routing

### 10.1 URL Strategy

All three approaches use path-based routing so customers get a clean, consistent URL regardless of which infrastructure runs underneath:

```
https://test.yourdomain.com/session/{session-id}/
```

### 10.2 Routing Mechanism Comparison

| | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Load balancer | ALB | ALB | NLB + NGINX Ingress |
| Rule per session | ALB listener rule | ALB listener rule | NGINX Ingress rule |
| Rule creation time | ~5 sec | ~5 sec | ~2 sec |
| **Default rule limit** | **100 per listener** | **100 per listener** | **No limit** |
| TLS | ACM (auto-renew) | ACM (auto-renew) | cert-manager (auto-renew) |
| Path rewriting | ❌ | ❌ | ✅ NGINX rewrites + `X-Forwarded-Prefix` |

### 10.3 ALB Rule Limit — The One Real Routing Constraint

The ALB listener rule limit (100 per listener by default) is a meaningful constraint for EC2 and ECS Fargate because it causes visible production failures when hit:

```
At 100 concurrent sessions → all ALB rules used
New session requests        → return 404 immediately
Customer experience         → "Session failed to start"
```

Workarounds exist (multiple listeners, AWS Support request) but both require architectural changes or escalation. Kubernetes NGINX Ingress has no equivalent limit — rules are stored in Kubernetes config, not in AWS resources.

> This is worth noting as a real operational boundary, unlike vCPU quotas which are silently increased with a single API call.

---

## 11. Security

### 11.1 Security Comparison

| Dimension | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| **Compute boundary** | Hypervisor VM | Firecracker microVM | Shared kernel + NetworkPolicy |
| **Network isolation** | Security Group per instance | Security Group per task | NetworkPolicy per session (pod labels) |
| **Credential storage** | AWS Secrets Manager | AWS Secrets Manager | Kubernetes Secret (auto-deleted with session) |
| **IAM granularity** | Instance profile (coarse) | Task IAM role (fine-grained) | IRSA per service account (finest) |
| **TLS** | ACM (auto-renew) | ACM (auto-renew) | cert-manager (auto-renew) |
| **Audit logging** | CloudTrail | CloudTrail + ECS events | CloudTrail + Kubernetes audit log |

### 11.2 Per-Session Credential Rotation

All three approaches generate fresh credentials for every session — never reused:

```python
{
    "db_password":          secrets.token_urlsafe(24),
    "fernet_key":           Fernet.generate_key().decode(),
    "webserver_secret_key": secrets.token_urlsafe(32),
    "admin_password":       secrets.token_urlsafe(16),
}
```

Where they live and how they're cleaned up:

| Approach | Storage | Auto-Cleaned? |
|---|---|---|
| EC2 | AWS Secrets Manager | Manually deleted on session end |
| ECS Fargate | AWS Secrets Manager | Manually deleted on session end |
| Kubernetes | K8s Secret in `airflow-sessions` | ✅ Deleted automatically via label selector |

---

## 12. Developer Experience

### 12.1 Team Skill Requirements

| Skill | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| Docker | Required | Required | Required |
| AWS basics (EC2, IAM, VPC) | Required | Required | Required |
| ECS (tasks, clusters) | Not needed | Required | Not needed |
| ALB / target group management | Optional | Required | Not needed |
| Kubernetes concepts | Not needed | Not needed | Required |
| Helm | Not needed | Not needed | Required |
| Karpenter / node autoscaling | Not needed | Not needed | Required |

### 12.2 Debugging a Broken Session

```bash
# EC2 — familiar and direct
ssh ec2-user@<public-ip>
docker logs airflow-webserver
cat /var/log/airflow-setup.log

# ECS Fargate — no SSH required
aws ecs execute-command \
  --cluster airflow-test-cluster \
  --task <task-arn> \
  --container airflow-webserver \
  --interactive --command "/bin/bash"
aws logs get-log-events \
  --log-group-name /airflow-test/sessions \
  --log-stream-name airflow-webserver/{session-id}

# Kubernetes — powerful, label-based queries across one clean namespace
kubectl get pods -n airflow-sessions \
  -l app.kubernetes.io/session-id=a1b2c3d4
kubectl logs -n airflow-sessions \
  -l app.kubernetes.io/session-id=a1b2c3d4,app.kubernetes.io/component=webserver
kubectl describe pod webserver-a1b2c3d4 -n airflow-sessions
kubectl get events -n airflow-sessions \
  --field-selector involvedObject.name=webserver-a1b2c3d4
```

### 12.3 Viewing All Active Sessions

```bash
# EC2
aws ec2 describe-instances \
  --filters "Name=tag:Purpose,Values=airflow-test" \
            "Name=instance-state-name,Values=running"

# ECS Fargate
aws ecs list-tasks \
  --cluster airflow-test-cluster \
  --family airflow-test-session \
  --desired-status RUNNING

# Kubernetes — single namespace makes this clean and fast
kubectl get pods -n airflow-sessions \
  -l app.kubernetes.io/component=webserver \
  -L app.kubernetes.io/session-id,app.kubernetes.io/customer-id
```

---

## 13. Risk Analysis

### 13.1 Risk Matrix

| Risk | EC2 | ECS Fargate | Kubernetes |
|---|---|---|---|
| **Session fails to start** | Medium — User Data errors are opaque | Low — container startup is reliable | Low — pod events are descriptive |
| **Session not cleaned up (cost leak)** | Medium — VM watchdog could fail | Low — Lambda + ECS stop is reliable | Low — label delete + Lambda fallback |
| **Runaway cost** | High — idle EC2 bills at full VM rate | Medium — idle Fargate task keeps billing | Low — idle pods consume minimal CPU |
| **Cross-session interference** | Low — full VM isolation | Low — Firecracker microVM boundary | Low — NetworkPolicy blocks cross-session traffic |
| **Data leak between sessions** | Low — instance terminated cleanly | Low — ephemeral task storage destroyed | Low — PVC + Secret deleted with session |
| **Routing failure at scale** | Medium — ALB rule limit at ~100 concurrent | Medium — ALB rule limit at ~100 concurrent | Low — NGINX Ingress has no rule limit |
| **Single region failure** | High — all sessions down | High — all sessions down | Medium — can run multi-region K8s |
| **Team can't operate it** | Low — Docker skills are common | Low-Medium — ECS is learnable | **High — K8s expertise is a real investment** |

### 13.2 Vendor Lock-in

```
EC2:         AWS-only — boto3, User Data, Security Groups, ALB all AWS-specific
ECS Fargate: AWS-only — ECS API is AWS-proprietary, no equivalent on GCP/Azure
Kubernetes:  Portable — manifests run unchanged on GKE, AKS, or self-managed K8s
             Some AWS-specific: Karpenter (replaceable), ECR (replaceable)
```

---

## 14. Decision Guide

### 14.1 Choose EC2 + Docker Compose if…

- ✅ You are building a **proof of concept** or validating the idea with first customers
- ✅ Your team has no ECS or Kubernetes experience
- ✅ You need the **simplest possible implementation** — ship in hours, not days
- ✅ A 3–5 minute startup time is acceptable to your customers
- ✅ You plan to **migrate to ECS Fargate later** and just need to validate now
- ❌ **Avoid if:** Startup time matters or you expect consistent daily session volume

### 14.2 Choose ECS Fargate if…

- ✅ You are building the **first production version** of this feature
- ✅ Your team knows Docker and basic AWS but not Kubernetes
- ✅ You want **zero infrastructure to manage** — no nodes, no patching, no cluster
- ✅ You want to be running in **1–2 days**, not 2 weeks
- ✅ A 30–60 second startup time is acceptable
- ✅ You are AWS-only with no multi-cloud requirements
- ✅ Daily session volume is under ~150 sessions/day (below Kubernetes break-even)
- ❌ **Avoid if:** You need sub-10 second starts, multi-cloud, or cost efficiency at very high volume

### 14.3 Choose Kubernetes (EKS) if…

- ✅ You need **sub-10-second session starts** (warm pool)
- ✅ You have or are hiring **Kubernetes expertise** on your team
- ✅ Daily session volume exceeds **~150 sessions/day** (bin-packing becomes cheaper)
- ✅ You want **spot instance support** for cost savings on free-tier customers
- ✅ You need **multi-cloud** portability (GCP, Azure)
- ✅ You are building a **platform product** expected to grow significantly
- ❌ **Avoid if:** Your team has no K8s experience — the operational investment is real

### 14.4 Quick Decision Flowchart

```
Is this a proof of concept / prototype?
│
├── YES → EC2 + Docker Compose ✅
│         Ship fast, learn, migrate when ready
│
└── NO (production)
      │
      ├── Does startup time need to be < 10 seconds?
      │     └── YES → Kubernetes (warm pool) ✅
      │
      ├── Is daily session volume > 150 sessions/day?
      │     └── YES → Kubernetes (bin-packing cost savings) ✅
      │
      ├── Do you need multi-cloud (GCP / Azure)?
      │     └── YES → Kubernetes ✅
      │
      └── None of the above
            └── ECS Fargate ✅
                Serverless, simple, production-ready
```

---

## 15. Migration Paths

### 15.1 EC2 → ECS Fargate

**Effort:** 1–2 days | **Risk:** Low

The Session Manager API, DynamoDB schema, and TTL Lambda are nearly identical. The main change is swapping the launch and terminate implementation.

```
EC2 Component               →  ECS Fargate Equivalent
──────────────────────────────────────────────────────
User Data script            →  Pre-baked ECR Docker image
EC2 Security Group          →  ECS Task Security Group
EC2 public DNS per session  →  ALB path rule per session
VM watchdog (shutdown -h)   →  EventBridge Lambda (external)
boto3.run_instances()       →  ecs.run_task()
ec2.terminate_instances()   →  ecs.stop_task() + ALB rule delete
```

### 15.2 ECS Fargate → Kubernetes

**Effort:** 2–3 weeks | **Risk:** Medium

The main changes are routing (ALB → NGINX Ingress) and resource management (ECS task API → Kubernetes client with label selectors). The single namespace model means there is no namespace-level cleanup — everything is label-selector based.

```
ECS Fargate Component          →  Kubernetes Equivalent
────────────────────────────────────────────────────────────────────
ECS Task Definition            →  Labeled K8s Deployment + StatefulSet
One Fargate task per session   →  Labeled pods in shared namespace
ALB listener rule per session  →  NGINX Ingress rule per session
ECS task IAM role              →  IRSA service account
Task CPU/memory limits         →  Pod resource limits + LimitRange
Task security group            →  NetworkPolicy (pod label selector)
ecs.run_task()                 →  kubectl apply labeled manifests
ecs.stop_task() + rule delete  →  kubectl delete --selector session-id=X
No warm pool capability        →  Pre-warmed pod pool (new capability)
AWS Secrets Manager secrets    →  K8s Secrets (auto-deleted with session)
```

### 15.3 Recommended Progression

```
Phase 1 (Month 0–1):    EC2 + Docker Compose
                        → Ship fast, validate the concept
                        → Learn real failure modes cheaply
                        → Build the Session Manager API foundation

Phase 2 (Month 2–3):    Migrate to ECS Fargate
                        → Reuse Session Manager API — swap launch code only
                        → Faster startup, fully serverless
                        → Right long-term choice for most teams

Phase 3 (Month 6+):     Evaluate Kubernetes
                        → Only if volume exceeds ~150 sessions/day
                        → Or if startup time complaints arise
                        → Or if multi-cloud requirement emerges
```

---

## 16. Final Recommendation

### For Most Teams: **ECS Fargate**

ECS Fargate is the right production default. Serverless, fast enough, and operationally simple — your team can own it without Kubernetes expertise.

```
✅ No infrastructure to manage
✅ 30–60 second startup
✅ Scales to any volume (soft quotas are trivially increased)
✅ Familiar AWS tooling
✅ Running in 1–2 days
✅ ~$0.11/session
```

### When to Move to Kubernetes

| Signal | Threshold | Action |
|---|---|---|
| **Cost** | > 150 sessions/day | K8s bin-packing saves money beyond this point |
| **Startup time** | Customer complaints about wait time | K8s warm pool reduces to 5 sec |
| **ALB rule limit** | > 80 concurrent sessions regularly | K8s NGINX Ingress has no rule limit |
| **Multi-cloud** | Any GCP or Azure requirement | K8s manifests are portable; ECS is not |
| **Spot instances** | Want 60–70% cost reduction for free-tier sessions | K8s supports safe spot node groups |

### One-Line Summary Per Approach

> **EC2:** *"The fastest way to build a working prototype — not the right foundation for production."*

> **ECS Fargate:** *"The right production default — serverless, scalable, and your team will actually be able to operate it."*

> **Kubernetes:** *"The most powerful and cost-efficient at scale — but only worth the operational investment when you genuinely need it."*

---

*Revisit this document when daily session volume exceeds 150 sessions, when AWS introduces significant pricing changes to ECS Fargate or EKS, or when multi-cloud requirements emerge.*