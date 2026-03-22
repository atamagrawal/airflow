# Airflow Test Deployment on Kubernetes (EKS) — Design Document

**Version:** 1.0  
**Date:** March 2026  
**Status:** Draft

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [Why Kubernetes Over ECS Fargate](#3-why-kubernetes-over-ecs-fargate)
4. [Architecture](#4-architecture)
5. [EKS Cluster Setup](#5-eks-cluster-setup)
6. [Container & Image Design](#6-container--image-design)
7. [Kubernetes Resource Manifests](#7-kubernetes-resource-manifests)
8. [Session Lifecycle Management](#8-session-lifecycle-management)
9. [API Layer](#9-api-layer)
10. [Networking & Ingress Routing](#10-networking--ingress-routing)
11. [Namespace Isolation Strategy](#11-namespace-isolation-strategy)
12. [Security](#12-security)
13. [Cost Optimization](#13-cost-optimization)
14. [Deployment Guide](#14-deployment-guide)
15. [Monitoring & Observability](#15-monitoring--observability)
16. [Appendix: Design Decisions](#16-appendix-design-decisions)

---

## 1. Overview

This document describes the design for an **on-demand, per-session Airflow test deployment system** built on **Amazon EKS (Elastic Kubernetes Service)**. It is the most scalable and customizable of the three deployment approaches (EC2, ECS Fargate, Kubernetes), designed for platforms expecting **hundreds of concurrent sessions** with sub-30-second startup times, warm pod pools, and fine-grained resource governance.

### Key Principle

> One session = One Kubernetes Namespace = One isolated Airflow stack

Each customer session is deployed into a dedicated Kubernetes namespace. All Airflow components — webserver, scheduler, PostgreSQL — run as Pods within that namespace, fully isolated from every other session at the network and resource level.

### Comparison Across All Three Approaches

| Dimension | EC2 | ECS Fargate | Kubernetes (EKS) |
|---|---|---|---|
| Startup time | 3–5 min | 30–60 sec | **10–30 sec (warm pool)** |
| Concurrent sessions | ~16 (quota) | Hundreds | **Thousands** |
| Infrastructure control | Low | Medium | **Full** |
| Warm pod pools | ❌ | ❌ | **✅** |
| Multi-cloud portability | ❌ | ❌ | **✅** |
| Custom scheduling | ❌ | ❌ | **✅** |
| Operational complexity | Low | Medium | High |
| Cluster baseline cost | $0 | $0 | ~$150–300/month |
| Best fit | Small scale | Medium scale | **Large scale** |

---

## 2. Goals & Non-Goals

### Goals
- Support hundreds of concurrent customer test sessions
- Launch a fully functional Airflow environment in under 30 seconds using warm pod pools
- Provide complete namespace-level isolation between sessions
- Enable fine-grained resource quotas per session
- Support multi-region and multi-cloud deployments
- Auto-scale cluster nodes based on session load
- Automatically terminate sessions after TTL expiry

### Non-Goals
- Not a production Airflow environment
- No persistent DAG storage between sessions
- No Kubernetes expertise required from customers (they only interact with the API)
- No multi-tenancy within a single session

---

## 3. Why Kubernetes Over ECS Fargate

### Where Kubernetes Wins

**1. Warm Pod Pools (Virtual Cluster Pre-warming)**

ECS Fargate always starts containers cold. Kubernetes lets you maintain a pool of pre-warmed, pre-pulled pods that are reassigned to new sessions instantly:

```
ECS Fargate session start:
  Task provision + image pull + Airflow init = 30–60 sec every time

Kubernetes with warm pool:
  Assign pre-warmed namespace = 5–10 sec
```

**2. Namespace-Level Isolation**

Kubernetes namespaces provide a richer isolation model than ECS task boundaries:
- Independent RBAC per namespace
- Network policies blocking cross-session traffic
- Resource quotas enforced at namespace level
- Independent service discovery (DNS is namespace-scoped)

**3. Node Autoscaling (Karpenter)**

Kubernetes with Karpenter can provision new EC2 nodes in ~60 seconds and bin-pack sessions efficiently — at 500 concurrent sessions, Karpenter will pack them onto nodes far more efficiently than ECS Fargate's per-task model.

**4. Custom Scheduling**

You can enforce policies like:
- "Pin GPU-heavy sessions to GPU nodes"
- "Run free-tier sessions on spot nodes, paid sessions on on-demand nodes"
- "Spread sessions across AZs for resilience"

None of this is possible with ECS Fargate.

**5. Multi-Cloud**

If you ever need to offer this on GCP (GKE) or Azure (AKS), the same Kubernetes manifests work unchanged. ECS Fargate is AWS-only.

---

## 4. Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                        Many Customers                                  │
│                    (Browser / API Clients)                             │
└───────────────────────────┬───────────────────────────────────────────┘
                            │  POST /sessions/start
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│                    Session Manager API                                 │
│             (FastAPI — deployed as EKS Deployment)                    │
│                                                                       │
│  - Validates customer token                                           │
│  - Creates Kubernetes Namespace per session                           │
│  - Applies Airflow Helm chart or raw manifests                        │
│  - Registers Ingress rule for routing                                 │
│  - Tracks session state in DynamoDB                                   │
└───────────────────────────┬───────────────────────────────────────────┘
                            │  Kubernetes API (kubectl / client-python)
                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│                        Amazon EKS Cluster                              │
│                                                                       │
│  ┌──────────────────────┐   ┌──────────────────────┐                 │
│  │ ns: session-abc-123  │   │ ns: session-def-456  │  ... N sessions │
│  │                      │   │                      │                 │
│  │ Deployment: webserver│   │ Deployment: webserver│                 │
│  │ Deployment: scheduler│   │ Deployment: scheduler│                 │
│  │ StatefulSet: postgres│   │ StatefulSet: postgres│                 │
│  │ Service: webserver   │   │ Service: webserver   │                 │
│  │ NetworkPolicy: deny  │   │ NetworkPolicy: deny  │                 │
│  │ ResourceQuota        │   │ ResourceQuota        │                 │
│  └──────────────────────┘   └──────────────────────┘                 │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │              System Namespaces                               │    │
│  │  ns: session-manager  — Session Manager API                  │    │
│  │  ns: ingress-nginx    — NGINX Ingress Controller             │    │
│  │  ns: karpenter        — Node autoscaler                      │    │
│  │  ns: monitoring       — Prometheus + Grafana                 │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  Node Group A (on-demand)      Node Group B (spot)                   │
│  ┌──────────┐ ┌──────────┐    ┌──────────┐ ┌──────────┐             │
│  │ m5.xlarge│ │ m5.xlarge│    │ m5.large │ │ m5.large │             │
│  └──────────┘ └──────────┘    └──────────┘ └──────────┘             │
└───────────────────────────────────────────────────────────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────┐       ┌─────────────────────────┐
│    DynamoDB      │       │   NGINX Ingress          │
│  Session State   │       │   Path-based routing     │
│  TTL tracking    │       │   /session/{id}/*        │
└──────────────────┘       └─────────────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────┐       ┌─────────────────────────┐
│  EventBridge     │       │   Amazon ECR             │
│  TTL → Lambda    │       │   Pre-pulled image cache │
└──────────────────┘       └─────────────────────────┘
```

---

## 5. EKS Cluster Setup

### 5.1 eksctl Cluster Definition

```yaml
# cluster/cluster.yaml
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: airflow-test-cluster
  region: us-east-1
  version: "1.29"

iam:
  withOIDC: true   # Required for IRSA (IAM Roles for Service Accounts)

managedNodeGroups:

  # On-demand node group for session-manager and system components
  - name: system
    instanceType: m5.large
    minSize: 2
    maxSize: 5
    desiredCapacity: 2
    labels:
      role: system
    taints:
      - key: role
        value: system
        effect: NoSchedule

  # On-demand node group for paid / production customer sessions
  - name: sessions-ondemand
    instanceType: m5.xlarge    # 4 vCPU, 16 GB — fits ~4 sessions per node
    minSize: 1
    maxSize: 50
    desiredCapacity: 2
    labels:
      role: session
      tier: ondemand
    spot: false

  # Spot node group for free-tier / trial customer sessions
  - name: sessions-spot
    instanceTypes:
      - m5.xlarge
      - m5a.xlarge
      - m4.xlarge
    minSize: 0
    maxSize: 100
    desiredCapacity: 0
    labels:
      role: session
      tier: spot
    spot: true

addons:
  - name: vpc-cni
  - name: coredns
  - name: kube-proxy
  - name: aws-ebs-csi-driver    # For persistent volumes if needed
```

```bash
# Create the cluster
eksctl create cluster -f cluster/cluster.yaml

# Verify
kubectl get nodes -L role,tier
```

### 5.2 Karpenter — Node Autoscaler

Karpenter provisions new nodes automatically when sessions can't be scheduled:

```yaml
# cluster/karpenter-nodepool.yaml
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: session-nodepool
spec:
  template:
    metadata:
      labels:
        role: session
    spec:
      nodeClassRef:
        name: session-nodeclass
      requirements:
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand", "spot"]
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["m5.xlarge", "m5a.xlarge", "m5.2xlarge"]
        - key: topology.kubernetes.io/zone
          operator: In
          values: ["us-east-1a", "us-east-1b", "us-east-1c"]
      kubelet:
        maxPods: 58    # m5.xlarge supports up to 58 pods

  limits:
    cpu: 500           # Hard cap: max 500 vCPUs across all Karpenter nodes
    memory: 2000Gi

  disruption:
    consolidationPolicy: WhenUnderutilized
    consolidateAfter: 5m    # Remove nodes with no sessions after 5 min idle

---
apiVersion: karpenter.k8s.aws/v1beta1
kind: EC2NodeClass
metadata:
  name: session-nodeclass
spec:
  amiFamily: AL2
  role: "KarpenterNodeRole-airflow-test-cluster"
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: airflow-test-cluster
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: airflow-test-cluster
  blockDeviceMappings:
    - deviceName: /dev/xvda
      ebs:
        volumeSize: 50Gi
        volumeType: gp3
        encrypted: true
```

### 5.3 NGINX Ingress Controller

```bash
# Install NGINX Ingress Controller via Helm
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.replicaCount=2 \
  --set controller.nodeSelector."role"=system \
  --set controller.tolerations[0].key=role \
  --set controller.tolerations[0].value=system \
  --set controller.tolerations[0].effect=NoSchedule \
  --set controller.service.type=LoadBalancer \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-type"=nlb
```

### 5.4 Warm Pod Pool (Pre-warming for Fast Session Start)

The warm pool maintains pre-initialized Airflow namespaces ready to be assigned to sessions. This eliminates image pull time:

```python
# warm_pool/pool_manager.py
"""
Maintains N pre-warmed Airflow namespaces ready for assignment.
When a session is requested, a warm namespace is claimed instantly
instead of waiting for cold pod startup.
"""

WARM_POOL_SIZE = 10    # Keep 10 sessions always ready
WARM_POOL_LABEL = "airflow-test/pool-status"

import kubernetes
from kubernetes import client, config

config.load_incluster_config()   # Runs inside the cluster
k8s_core   = client.CoreV1Api()
k8s_apps   = client.AppsV1Api()
k8s_net    = client.NetworkingV1Api()

def get_available_warm_namespace() -> str | None:
    """Return a pre-warmed namespace ready for assignment."""
    namespaces = k8s_core.list_namespace(
        label_selector=f"{WARM_POOL_LABEL}=ready"
    )
    if namespaces.items:
        return namespaces.items[0].metadata.name
    return None

def claim_warm_namespace(namespace: str, session_id: str, customer_id: str):
    """Claim a warm namespace for a session — mark it as assigned."""
    k8s_core.patch_namespace(
        namespace,
        body={
            "metadata": {
                "labels": {
                    WARM_POOL_LABEL:            "assigned",
                    "airflow-test/session-id":  session_id,
                    "airflow-test/customer-id": customer_id,
                }
            }
        }
    )
    # Replenish the pool asynchronously
    replenish_warm_pool()

def replenish_warm_pool():
    """Ensure the warm pool is always at WARM_POOL_SIZE."""
    ready = k8s_core.list_namespace(
        label_selector=f"{WARM_POOL_LABEL}=ready"
    ).items
    deficit = WARM_POOL_SIZE - len(ready)
    for _ in range(deficit):
        create_warm_namespace()

def create_warm_namespace():
    """Create and pre-warm a new Airflow namespace for the pool."""
    import uuid
    ns_name = f"session-warm-{uuid.uuid4().hex[:8]}"
    deploy_airflow_to_namespace(
        namespace=ns_name,
        session_id="warm-pool",
        customer_id="warm-pool",
        labels={WARM_POOL_LABEL: "initializing"},
    )
    # Mark as ready once Airflow webserver is healthy
    # (checked by a background reconciliation loop)
```

---

## 6. Container & Image Design

### 6.1 Custom Airflow Docker Image

```dockerfile
# docker/Dockerfile
FROM apache/airflow:2.9.2

USER root

RUN apt-get update && apt-get install -y \
    curl netcat-openbsd \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

USER airflow

# Pre-install common providers
RUN pip install --no-cache-dir \
    apache-airflow-providers-amazon \
    apache-airflow-providers-postgres \
    apache-airflow-providers-http \
    apache-airflow-providers-slack \
    apache-airflow-providers-google \
    pandas requests

# Copy sample DAGs
COPY --chown=airflow:root sample_dags/ /opt/airflow/dags/

# Healthcheck script
COPY --chown=airflow:root scripts/healthcheck.sh /healthcheck.sh
RUN chmod +x /healthcheck.sh

# Optimized config for test sessions
ENV AIRFLOW__CORE__EXECUTOR=LocalExecutor \
    AIRFLOW__CORE__LOAD_EXAMPLES=False \
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=True \
    AIRFLOW__SCHEDULER__USE_JOB_SCHEDULE=False \
    AIRFLOW__WEBSERVER__EXPOSE_CONFIG=True \
    AIRFLOW__SCHEDULER__MIN_FILE_PROCESS_INTERVAL=10 \
    AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX=True
```

```bash
# Build and push to ECR
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/airflow-test"

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_URI

docker build -t airflow-test:2.9.2 docker/
docker tag airflow-test:2.9.2 ${ECR_URI}:2.9.2
docker push ${ECR_URI}:2.9.2
```

---

## 7. Kubernetes Resource Manifests

All resources for a session are created inside a dedicated namespace. The Session Manager applies these manifests dynamically via the Kubernetes Python client.

### 7.1 Namespace

```yaml
# manifests/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: session-{SESSION_ID}
  labels:
    airflow-test/session-id:  "{SESSION_ID}"
    airflow-test/customer-id: "{CUSTOMER_ID}"
    airflow-test/pool-status: "assigned"
    airflow-test/created-at:  "{CREATED_AT}"
```

### 7.2 ResourceQuota (Per-Session Resource Cap)

```yaml
# manifests/resource-quota.yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: session-quota
  namespace: session-{SESSION_ID}
spec:
  hard:
    requests.cpu:    "1500m"
    requests.memory: "3Gi"
    limits.cpu:      "2"
    limits.memory:   "4Gi"
    pods:            "10"
```

### 7.3 NetworkPolicy (Block Cross-Session Traffic)

```yaml
# manifests/network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-cross-session
  namespace: session-{SESSION_ID}
spec:
  podSelector: {}    # Applies to ALL pods in this namespace
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # Only allow traffic from within the same namespace
    - from:
        - podSelector: {}
    # Allow traffic from NGINX Ingress Controller
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
  egress:
    # Allow traffic within namespace (scheduler → postgres, webserver → postgres)
    - to:
        - podSelector: {}
    # Allow DNS resolution
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - port: 53
          protocol: UDP
    # Allow outbound internet for DAG dependencies
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8      # Block access to internal VPC
              - 172.16.0.0/12
              - 192.168.0.0/16
```

### 7.4 PostgreSQL StatefulSet

```yaml
# manifests/postgres.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: session-{SESSION_ID}
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      nodeSelector:
        role: session
      containers:
        - name: postgres
          image: postgres:15
          env:
            - name: POSTGRES_USER
              value: airflow
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: db-password
            - name: POSTGRES_DB
              value: airflow
          ports:
            - containerPort: 5432
          resources:
            requests:
              cpu:    "250m"
              memory: "512Mi"
            limits:
              cpu:    "500m"
              memory: "1Gi"
          livenessProbe:
            exec:
              command: ["pg_isready", "-U", "airflow"]
            initialDelaySeconds: 10
            periodSeconds: 10
          # Ephemeral storage — data gone when pod is deleted
          volumeMounts:
            - name: postgres-data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: postgres-data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: gp3
        resources:
          requests:
            storage: 5Gi

---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: session-{SESSION_ID}
spec:
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432
  clusterIP: None    # Headless service for StatefulSet DNS
```

### 7.5 Airflow Init Job

```yaml
# manifests/airflow-init.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: airflow-init
  namespace: session-{SESSION_ID}
spec:
  ttlSecondsAfterFinished: 300    # Auto-delete job after 5 min
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      nodeSelector:
        role: session
      initContainers:
        # Wait for postgres to be ready before init
        - name: wait-for-postgres
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              until nc -z postgres 5432; do
                echo "Waiting for postgres..."; sleep 2;
              done
              echo "Postgres is ready"

      containers:
        - name: airflow-init
          image: {ECR_IMAGE}
          command:
            - bash
            - -c
            - |
              airflow db migrate
              airflow users create \
                --username admin \
                --password $(AIRFLOW_ADMIN_PASSWORD) \
                --firstname Test \
                --lastname User \
                --role Admin \
                --email admin@test.com
          env:
            - name: AIRFLOW__DATABASE__SQL_ALCHEMY_CONN
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: db-conn-string
            - name: AIRFLOW_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: admin-password
          resources:
            requests:
              cpu:    "200m"
              memory: "512Mi"
            limits:
              cpu:    "500m"
              memory: "1Gi"
```

### 7.6 Airflow Webserver Deployment

```yaml
# manifests/airflow-webserver.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: airflow-webserver
  namespace: session-{SESSION_ID}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: airflow-webserver
  template:
    metadata:
      labels:
        app: airflow-webserver
    spec:
      nodeSelector:
        role: session
      # Wait for init job to complete
      initContainers:
        - name: wait-for-init
          image: bitnami/kubectl:latest
          command:
            - sh
            - -c
            - |
              kubectl wait --for=condition=complete \
                job/airflow-init \
                --timeout=120s \
                -n $(POD_NAMESPACE)
          env:
            - name: POD_NAMESPACE
              valueFrom:
                fieldRef:
                  fieldPath: metadata.namespace

      containers:
        - name: airflow-webserver
          image: {ECR_IMAGE}
          command: ["airflow", "webserver"]
          ports:
            - containerPort: 8080
          env:
            - name: AIRFLOW__DATABASE__SQL_ALCHEMY_CONN
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: db-conn-string
            - name: AIRFLOW__CORE__FERNET_KEY
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: fernet-key
            - name: AIRFLOW__WEBSERVER__SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: webserver-secret-key
            - name: AIRFLOW__WEBSERVER__BASE_URL
              value: "https://test.yourdomain.com/session/{SESSION_ID}"
          resources:
            requests:
              cpu:    "500m"
              memory: "1Gi"
            limits:
              cpu:    "1"
              memory: "2Gi"
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 15
            failureThreshold: 5
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 20
            periodSeconds: 10

---
apiVersion: v1
kind: Service
metadata:
  name: airflow-webserver
  namespace: session-{SESSION_ID}
spec:
  selector:
    app: airflow-webserver
  ports:
    - port: 8080
      targetPort: 8080
```

### 7.7 Airflow Scheduler Deployment

```yaml
# manifests/airflow-scheduler.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: airflow-scheduler
  namespace: session-{SESSION_ID}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: airflow-scheduler
  template:
    metadata:
      labels:
        app: airflow-scheduler
    spec:
      nodeSelector:
        role: session
      containers:
        - name: airflow-scheduler
          image: {ECR_IMAGE}
          command: ["airflow", "scheduler"]
          env:
            - name: AIRFLOW__DATABASE__SQL_ALCHEMY_CONN
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: db-conn-string
            - name: AIRFLOW__CORE__FERNET_KEY
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets
                  key: fernet-key
          resources:
            requests:
              cpu:    "500m"
              memory: "1Gi"
            limits:
              cpu:    "1"
              memory: "2Gi"
          livenessProbe:
            exec:
              command:
                - sh
                - -c
                - |
                  airflow jobs check \
                    --job-type SchedulerJob \
                    --hostname $(hostname)
            initialDelaySeconds: 30
            periodSeconds: 30
```

### 7.8 Ingress Rule (Per-Session)

```yaml
# manifests/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: airflow-ingress
  namespace: session-{SESSION_ID}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "60"
    nginx.ingress.kubernetes.io/proxy-read-timeout:    "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout:    "3600"
    # Strip session prefix before forwarding to Airflow
    nginx.ingress.kubernetes.io/configuration-snippet: |
      proxy_set_header X-Forwarded-Prefix /session/{SESSION_ID};
spec:
  ingressClassName: nginx
  rules:
    - host: test.yourdomain.com
      http:
        paths:
          - path: /session/{SESSION_ID}(/|$)(.*)
            pathType: ImplementationSpecific
            backend:
              service:
                name: airflow-webserver
                port:
                  number: 8080
```

### 7.9 Per-Session Secrets

```yaml
# manifests/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: airflow-secrets
  namespace: session-{SESSION_ID}
type: Opaque
stringData:
  db-password:          "{DB_PASSWORD}"
  db-conn-string:       "postgresql+psycopg2://airflow:{DB_PASSWORD}@postgres/airflow"
  fernet-key:           "{FERNET_KEY}"
  webserver-secret-key: "{WEBSERVER_SECRET_KEY}"
  admin-password:       "{ADMIN_PASSWORD}"
```

---

## 8. Session Lifecycle Management

### 8.1 Session States

```
REQUESTED → LAUNCHING → READY → ACTIVE → TERMINATING → TERMINATED
                ↑                   ↑
         Warm pool hit          TTL reset on
         (skip to READY)        customer activity
```

| State | Duration | Description |
|---|---|---|
| `REQUESTED` | Seconds | API received request, namespace creation initiated |
| `LAUNCHING` | 10–30 sec (cold) / 5 sec (warm) | Pods starting, init job running |
| `READY` | — | Airflow UI accessible, URL delivered to customer |
| `ACTIVE` | Up to TTL | Customer is actively using the environment |
| `TERMINATING` | Seconds | Namespace deletion in progress |
| `TERMINATED` | — | All Kubernetes resources deleted, warm pool replenished |

### 8.2 DynamoDB Session Store

```python
# session_store.py
import boto3
from datetime import datetime, timezone
from typing import Optional

TABLE_NAME = "airflow-test-sessions"

class SessionStore:
    def __init__(self):
        self.dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        self.table    = self.dynamodb.Table(TABLE_NAME)

    def create_session(
        self,
        session_id:   str,
        customer_id:  str,
        namespace:    str,
        ttl_minutes:  int,
    ) -> dict:
        now        = datetime.now(timezone.utc)
        ttl_epoch  = int(now.timestamp()) + (ttl_minutes * 60)

        item = {
            "session_id":   session_id,
            "customer_id":  customer_id,
            "namespace":    namespace,
            "status":       "REQUESTED",
            "created_at":   now.isoformat(),
            "ttl":          ttl_epoch,
            "ttl_minutes":  ttl_minutes,
            "airflow_url":  None,
            "warm_pool":    False,
        }
        self.table.put_item(Item=item)
        return item

    def update_session(self, session_id: str, updates: dict):
        update_expr  = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
        expr_names   = {f"#{k}": k for k in updates}
        expr_values  = {f":{k}": v for k, v in updates.items()}
        self.table.update_item(
            Key={"session_id": session_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )

    def get_session(self, session_id: str) -> Optional[dict]:
        response = self.table.get_item(Key={"session_id": session_id})
        return response.get("Item")

    def list_active_sessions(self, customer_id: str) -> list:
        response = self.table.query(
            IndexName="customer_id-index",
            KeyConditionExpression="customer_id = :cid",
            FilterExpression="#status IN (:s1, :s2, :s3)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":cid": customer_id,
                ":s1":  "LAUNCHING",
                ":s2":  "READY",
                ":s3":  "ACTIVE",
            },
        )
        return response.get("Items", [])
```

### 8.3 TTL Cleanup — Lambda + EventBridge

```python
# lambda/ttl_cleanup/handler.py
import boto3
from kubernetes import client, config
from datetime import datetime, timezone

config.load_incluster_config()
k8s_core  = client.CoreV1Api()
dynamodb  = boto3.resource("dynamodb", region_name="us-east-1")
TABLE     = dynamodb.Table("airflow-test-sessions")

def handler(event, context):
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    # Find sessions past TTL
    response = TABLE.scan(
        FilterExpression=(
            "#status IN (:s1, :s2) AND #ttl < :now"
        ),
        ExpressionAttributeNames={
            "#status": "status",
            "#ttl":    "ttl",
        },
        ExpressionAttributeValues={
            ":s1":  "READY",
            ":s2":  "ACTIVE",
            ":now": now_epoch,
        },
    )

    terminated = []
    for session in response.get("Items", []):
        session_id = session["session_id"]
        namespace  = session.get("namespace")

        print(f"Terminating expired session: {session_id} / namespace: {namespace}")

        # Delete the entire Kubernetes namespace
        # This cascades to all resources: Pods, Services, Ingress, Secrets, PVCs
        if namespace:
            try:
                k8s_core.delete_namespace(
                    name=namespace,
                    body=client.V1DeleteOptions(
                        propagation_policy="Foreground",
                        grace_period_seconds=30,
                    )
                )
                print(f"Namespace {namespace} deletion initiated")
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    print(f"Namespace {namespace} already gone")
                else:
                    print(f"Error deleting namespace: {e}")

        TABLE.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET #status = :s",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":s": "TERMINATED"},
        )
        terminated.append(session_id)

    print(f"Terminated {len(terminated)} sessions")
    return {"terminated": terminated}
```

---

## 9. API Layer

### 9.1 Kubernetes Session Launcher

```python
# k8s_launcher.py
import uuid
import yaml
from pathlib import Path
from kubernetes import client, config
from cryptography.fernet import Fernet
import secrets as secrets_lib

config.load_incluster_config()
k8s_core   = client.CoreV1Api()
k8s_apps   = client.AppsV1Api()
k8s_batch  = client.BatchV1Api()
k8s_net    = client.NetworkingV1Api()

ECR_IMAGE     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/airflow-test:2.9.2"
MANIFESTS_DIR = Path("/app/manifests")
BASE_DOMAIN   = "test.yourdomain.com"


def generate_session_secrets() -> dict:
    return {
        "db-password":          secrets_lib.token_urlsafe(24),
        "fernet-key":           Fernet.generate_key().decode(),
        "webserver-secret-key": secrets_lib.token_urlsafe(32),
        "admin-password":       secrets_lib.token_urlsafe(16),
    }


def render_manifest(template_path: str, replacements: dict) -> dict:
    """Load a YAML manifest template and replace placeholders."""
    content = (MANIFESTS_DIR / template_path).read_text()
    for key, value in replacements.items():
        content = content.replace(f"{{{key}}}", str(value))
    return yaml.safe_load(content)


def launch_session(session_id: str, customer_id: str) -> dict:
    """
    Create a Kubernetes namespace and deploy Airflow into it.
    Returns airflow_url and namespace name.
    """
    namespace = f"session-{session_id[:16]}"
    creds     = generate_session_secrets()

    replacements = {
        "SESSION_ID":          session_id,
        "CUSTOMER_ID":         customer_id,
        "ECR_IMAGE":           ECR_IMAGE,
        "DB_PASSWORD":         creds["db-password"],
        "FERNET_KEY":          creds["fernet-key"],
        "WEBSERVER_SECRET_KEY": creds["webserver-secret-key"],
        "ADMIN_PASSWORD":      creds["admin-password"],
    }

    # 1. Create namespace
    ns_manifest = render_manifest("namespace.yaml", replacements)
    k8s_core.create_namespace(body=ns_manifest)

    # 2. Apply resource quota
    quota = render_manifest("resource-quota.yaml", replacements)
    k8s_core.create_namespaced_resource_quota(namespace, body=quota)

    # 3. Apply network policy
    netpol = render_manifest("network-policy.yaml", replacements)
    k8s_net.create_namespaced_network_policy(namespace, body=netpol)

    # 4. Create secrets
    secret = render_manifest("secrets.yaml", replacements)
    k8s_core.create_namespaced_secret(namespace, body=secret)

    # 5. Deploy PostgreSQL
    pg_sts = render_manifest("postgres.yaml", replacements)
    k8s_apps.create_namespaced_stateful_set(namespace, body=pg_sts)
    pg_svc = yaml.safe_load_all((MANIFESTS_DIR / "postgres.yaml").read_text())
    for doc in pg_svc:
        if doc and doc.get("kind") == "Service":
            k8s_core.create_namespaced_service(namespace, body=doc)

    # 6. Run airflow init job
    init_job = render_manifest("airflow-init.yaml", replacements)
    k8s_batch.create_namespaced_job(namespace, body=init_job)

    # 7. Deploy webserver + scheduler
    ws_deploy = render_manifest("airflow-webserver.yaml", replacements)
    k8s_apps.create_namespaced_deployment(namespace, body=ws_deploy)
    ws_svc = yaml.safe_load_all((MANIFESTS_DIR / "airflow-webserver.yaml").read_text())
    for doc in ws_svc:
        if doc and doc.get("kind") == "Service":
            k8s_core.create_namespaced_service(namespace, body=doc)

    sched_deploy = render_manifest("airflow-scheduler.yaml", replacements)
    k8s_apps.create_namespaced_deployment(namespace, body=sched_deploy)

    # 8. Create ingress rule
    ingress = render_manifest("ingress.yaml", replacements)
    k8s_net.create_namespaced_ingress(namespace, body=ingress)

    airflow_url = f"https://{BASE_DOMAIN}/session/{session_id}"
    return {
        "namespace":   namespace,
        "airflow_url": airflow_url,
        "credentials": {
            "username": "admin",
            "password": creds["admin-password"],
        },
    }


def terminate_session(namespace: str):
    """Delete the entire Kubernetes namespace — cascades to all resources."""
    try:
        k8s_core.delete_namespace(
            name=namespace,
            body=client.V1DeleteOptions(
                propagation_policy="Foreground",
                grace_period_seconds=30,
            )
        )
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise
```

### 9.2 FastAPI Session Manager

```python
# main.py
import uuid
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from k8s_launcher import launch_session, terminate_session
from warm_pool.pool_manager import get_available_warm_namespace, claim_warm_namespace
from session_store import SessionStore

app   = FastAPI(title="Airflow Test Session Manager — Kubernetes")
store = SessionStore()

MAX_TTL_MINUTES     = 120
DEFAULT_TTL_MINUTES = 60


class StartSessionRequest(BaseModel):
    customer_id: str
    ttl_minutes: int = DEFAULT_TTL_MINUTES


@app.post("/sessions/start")
async def start_session(request: StartSessionRequest):
    ttl = min(request.ttl_minutes, MAX_TTL_MINUTES)

    # Enforce one active session per customer
    active = store.list_active_sessions(request.customer_id)
    if active:
        raise HTTPException(status_code=409, detail={
            "error":       "active_session_exists",
            "session_id":  active[0]["session_id"],
            "airflow_url": active[0].get("airflow_url"),
        })

    session_id = str(uuid.uuid4())

    # Try warm pool first (instant start)
    warm_ns    = get_available_warm_namespace()
    used_warm  = warm_ns is not None

    if used_warm:
        claim_warm_namespace(warm_ns, session_id, request.customer_id)
        namespace   = warm_ns
        airflow_url = f"https://test.yourdomain.com/session/{session_id}"
        credentials = {"username": "admin", "password": "admin"}
    else:
        # Cold start — create new namespace
        store.create_session(session_id, request.customer_id, "pending", ttl)
        store.update_session(session_id, {"status": "LAUNCHING"})

        result      = await asyncio.to_thread(launch_session, session_id, request.customer_id)
        namespace   = result["namespace"]
        airflow_url = result["airflow_url"]
        credentials = result["credentials"]

        # Poll until Airflow is healthy (cold: ~30 sec)
        await wait_for_airflow(airflow_url, timeout=120)

    store.create_session(session_id, request.customer_id, namespace, ttl)
    store.update_session(session_id, {
        "status":      "READY",
        "airflow_url": airflow_url,
        "namespace":   namespace,
        "warm_pool":   used_warm,
    })

    return {
        "session_id":   session_id,
        "airflow_url":  airflow_url,
        "status":       "READY",
        "ttl_minutes":  ttl,
        "credentials":  credentials,
        "warm_pool_hit": used_warm,
    }


@app.delete("/sessions/{session_id}")
async def end_session(session_id: str):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") in ("TERMINATED", "TERMINATING"):
        return {"message": "Already terminated", "session_id": session_id}

    store.update_session(session_id, {"status": "TERMINATING"})
    await asyncio.to_thread(terminate_session, session["namespace"])
    store.update_session(session_id, {"status": "TERMINATED"})

    return {"message": "Session terminated", "session_id": session_id}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/sessions/{session_id}/status")
async def poll_status(session_id: str):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id":  session_id,
        "status":      session["status"],
        "airflow_url": session.get("airflow_url"),
    }


async def wait_for_airflow(url: str, timeout: int = 120):
    import httpx
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{url}/health", timeout=5)
                if r.status_code == 200:
                    return
        except Exception:
            pass
        await asyncio.sleep(5)
    raise TimeoutError(f"Airflow did not become healthy within {timeout}s")
```

---

## 10. Networking & Ingress Routing

### 10.1 Path-Based Routing via NGINX Ingress

Each session gets a unique path on a shared NGINX ingress:

```
https://test.yourdomain.com/session/{session-id}/home
https://test.yourdomain.com/session/{session-id}/dags
https://test.yourdomain.com/session/{session-id}/graph?dag_id=...
```

NGINX rewrites the path before forwarding to Airflow:
- Incoming: `/session/abc-123/dags`
- Forwarded to Airflow: `/dags`
- With header: `X-Forwarded-Prefix: /session/abc-123`

### 10.2 TLS Termination via cert-manager

```bash
# Install cert-manager for automatic TLS certificates
helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set installCRDs=true

# Create ClusterIssuer for Let's Encrypt
kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ops@yourdomain.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
EOF
```

Add to Ingress manifest:
```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts:
        - test.yourdomain.com
      secretName: airflow-test-tls
```

---

## 11. Namespace Isolation Strategy

### 11.1 What Each Namespace Contains

```
namespace: session-{session-id}
├── ResourceQuota          — CPU/memory/pod limits
├── NetworkPolicy          — deny cross-session traffic
├── Secret: airflow-secrets — per-session credentials
├── StatefulSet: postgres   — isolated metadata DB
├── Deployment: airflow-webserver
├── Deployment: airflow-scheduler
├── Service: postgres (headless)
├── Service: airflow-webserver
├── Job: airflow-init (runs once, then deleted)
└── Ingress: airflow-ingress — path /session/{id}/*
```

### 11.2 Isolation Guarantees

| Isolation Type | Mechanism | Guarantee |
|---|---|---|
| Network | NetworkPolicy | No pod in session-A can reach session-B |
| Resource | ResourceQuota | Session-A can't starve session-B of CPU/memory |
| Secrets | Per-namespace Kubernetes Secret | Session-A cannot read session-B's DB password |
| DNS | Kubernetes namespace-scoped DNS | `postgres.session-A` ≠ `postgres.session-B` |
| Storage | Per-session PVC | Each postgres gets its own EBS volume |

---

## 12. Security

### 12.1 IRSA — IAM Roles for Service Accounts

```bash
# Associate IAM role with Kubernetes service account
# Session Manager API gets permission to call DynamoDB and Secrets Manager
eksctl create iamserviceaccount \
  --name session-manager \
  --namespace session-manager \
  --cluster airflow-test-cluster \
  --attach-policy-arn arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess \
  --attach-policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite \
  --approve
```

### 12.2 RBAC — Session Manager Permissions

```yaml
# rbac/session-manager-role.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: session-manager
rules:
  - apiGroups: [""]
    resources: ["namespaces", "services", "secrets",
                "resourcequotas", "pods"]
    verbs: ["create", "get", "list", "delete", "patch", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets"]
    verbs: ["create", "get", "list", "delete", "patch"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "delete"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses", "networkpolicies"]
    verbs: ["create", "get", "list", "delete", "patch"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: session-manager
subjects:
  - kind: ServiceAccount
    name: session-manager
    namespace: session-manager
roleRef:
  kind: ClusterRole
  name: session-manager
  apiGroup: rbac.authorization.k8s.io
```

### 12.3 Pod Security Standards

```yaml
# Enforce restricted pod security on all session namespaces
# Applied automatically via namespace label
apiVersion: v1
kind: Namespace
metadata:
  name: session-{SESSION_ID}
  labels:
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/audit:   restricted
    pod-security.kubernetes.io/warn:    restricted
```

---

## 13. Cost Optimization

### 13.1 Cost Per Session

| Resource | Rate | 1-hour Session | Notes |
|---|---|---|---|
| m5.xlarge node (shared) | $0.192/hr | ~$0.048 | 4 sessions per node |
| EBS (5 GB postgres PVC) | $0.0001/hr | ~$0.0001 | gp3, deleted after session |
| ALB/NLB (shared) | $0.008/hr | ~$0.002 | One NLB for all sessions |
| EKS cluster fee | $0.10/hr | ~$0.002 | Amortized across sessions |
| **Total** | | **~$0.05/session** | Per 1-hour session |

> Kubernetes is the most cost-efficient at scale because sessions are bin-packed onto shared nodes rather than having a dedicated Fargate task per session.

### 13.2 Cost Controls

```python
# cost_controls.py

# Spot node group for non-critical (free-tier) sessions
SPOT_NODE_SELECTOR = {"role": "session", "tier": "spot"}
ONDEMAND_NODE_SELECTOR = {"role": "session", "tier": "ondemand"}

MAX_TTL_MINUTES         = 120
MAX_CONCURRENT_SESSIONS = 500
WARM_POOL_SIZE          = 10

def get_node_selector(customer_tier: str) -> dict:
    """Route free-tier customers to spot nodes, paid to on-demand."""
    if customer_tier == "free":
        return SPOT_NODE_SELECTOR
    return ONDEMAND_NODE_SELECTOR

def get_active_session_count() -> int:
    """Count running session namespaces."""
    k8s_core = client.CoreV1Api()
    nss = k8s_core.list_namespace(
        label_selector="airflow-test/pool-status=assigned"
    )
    return len(nss.items)

# Orphan cleanup Lambda — also runs every 5 min
def cleanup_orphaned_namespaces():
    """Delete session namespaces older than 3 hours regardless of state."""
    from datetime import datetime, timezone, timedelta
    k8s_core = client.CoreV1Api()
    nss = k8s_core.list_namespace(
        label_selector="airflow-test/pool-status=assigned"
    )
    now = datetime.now(timezone.utc)
    for ns in nss.items:
        created = ns.metadata.creation_timestamp
        if created and (now - created.replace(tzinfo=timezone.utc)) > timedelta(hours=3):
            print(f"Orphaned namespace: {ns.metadata.name}")
            k8s_core.delete_namespace(ns.metadata.name)
```

---

## 14. Deployment Guide

### 14.1 Prerequisites

```bash
# Install tools
brew install eksctl kubectl helm
pip install kubernetes boto3 fastapi uvicorn httpx pydantic cryptography

# Configure AWS credentials
aws configure

# Install eksctl cluster
eksctl create cluster -f cluster/cluster.yaml

# Update kubeconfig
aws eks update-kubeconfig \
  --name airflow-test-cluster \
  --region us-east-1
```

### 14.2 Step-by-Step Deployment

```bash
# Step 1: Install cluster addons
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set installCRDs=true

# Step 2: Install Karpenter
helm install karpenter oci://public.ecr.aws/karpenter/karpenter \
  --namespace karpenter --create-namespace \
  --version v0.33.0

kubectl apply -f cluster/karpenter-nodepool.yaml

# Step 3: Build and push Airflow image
./scripts/build_and_push.sh

# Step 4: Create system namespaces and RBAC
kubectl create namespace session-manager
kubectl apply -f rbac/session-manager-role.yaml

# Step 5: Deploy Session Manager API
kubectl apply -f k8s/session-manager-deployment.yaml

# Step 6: Bootstrap warm pool
kubectl exec -n session-manager \
  deployment/session-manager -- \
  python -c "from warm_pool.pool_manager import replenish_warm_pool; replenish_warm_pool()"

# Step 7: Deploy TTL cleanup Lambda
cd lambda/ttl_cleanup && ./deploy.sh
```

### 14.3 Example API Usage

```bash
# Start a session (warm pool hit — instant)
curl -X POST https://api.yourdomain.com/sessions/start \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "cust_123", "ttl_minutes": 60}'

# Response:
# {
#   "session_id": "a1b2c3d4-e5f6-...",
#   "airflow_url": "https://test.yourdomain.com/session/a1b2c3d4-e5f6-...",
#   "status": "READY",
#   "ttl_minutes": 60,
#   "credentials": {"username": "admin", "password": "xK9mP2qR..."},
#   "warm_pool_hit": true
# }

# Check pool status
kubectl get namespaces -l airflow-test/pool-status=ready

# Check all active sessions
kubectl get namespaces -l airflow-test/pool-status=assigned

# End session early
curl -X DELETE https://api.yourdomain.com/sessions/a1b2c3d4-e5f6-...
```

### 14.4 Project Structure

```
airflow-k8s/
├── api/
│   ├── main.py                  # FastAPI Session Manager
│   ├── k8s_launcher.py          # Kubernetes namespace + resource creation
│   ├── session_store.py         # DynamoDB session state
│   ├── cost_controls.py         # Session caps + orphan cleanup
│   └── requirements.txt
├── warm_pool/
│   └── pool_manager.py          # Pre-warmed namespace pool
├── manifests/
│   ├── namespace.yaml
│   ├── resource-quota.yaml
│   ├── network-policy.yaml
│   ├── secrets.yaml
│   ├── postgres.yaml
│   ├── airflow-init.yaml
│   ├── airflow-webserver.yaml
│   ├── airflow-scheduler.yaml
│   └── ingress.yaml
├── lambda/
│   └── ttl_cleanup/
│       └── handler.py
├── cluster/
│   ├── cluster.yaml             # eksctl cluster definition
│   └── karpenter-nodepool.yaml
├── rbac/
│   └── session-manager-role.yaml
├── k8s/
│   └── session-manager-deployment.yaml
├── docker/
│   ├── Dockerfile
│   └── sample_dags/
└── scripts/
    └── build_and_push.sh
```

---

## 15. Monitoring & Observability

### 15.1 Prometheus + Grafana Stack

```bash
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts

helm install kube-prometheus-stack \
  prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.enabled=true \
  --set prometheus.prometheusSpec.retention=7d
```

### 15.2 Key Metrics to Monitor

| Metric | Source | Alert Threshold |
|---|---|---|
| `airflow_test_active_sessions` | Custom / DynamoDB | > 400 |
| `airflow_test_warm_pool_size` | Custom | < 3 (refill immediately) |
| `airflow_test_startup_seconds` | Custom | > 30 sec |
| `kube_namespace_status_phase` | kube-state-metrics | Any session namespace stuck in Terminating > 5 min |
| `container_cpu_usage_seconds_total` | cAdvisor | > 90% of quota |
| `karpenter_nodes_total` | Karpenter | Unexpected spike > 50 nodes |
| `nginx_ingress_controller_requests` | NGINX | Error rate > 1% |
| `airflow_test_sessions_failed` | Custom | > 3 in 5 min |

### 15.3 Custom Metrics Publisher

```python
# monitoring.py
from prometheus_client import Counter, Histogram, Gauge, start_http_server

sessions_started   = Counter("airflow_test_sessions_started_total",   "Total sessions started")
sessions_failed    = Counter("airflow_test_sessions_failed_total",     "Total sessions failed")
sessions_active    = Gauge("airflow_test_active_sessions",             "Currently active sessions")
warm_pool_size     = Gauge("airflow_test_warm_pool_size",              "Warm namespaces available")
startup_duration   = Histogram(
    "airflow_test_startup_seconds",
    "Time from request to READY",
    buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120]
)

# Start metrics server on :9090 (scraped by Prometheus)
start_http_server(9090)
```

---

## 16. Appendix: Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| EKS vs self-managed K8s | Amazon EKS | AWS manages control plane; reduces operational burden while keeping full K8s flexibility |
| Namespace-per-session isolation | Kubernetes Namespace | Richer isolation than ECS task boundaries — RBAC, NetworkPolicy, ResourceQuota all namespace-scoped |
| Warm pod pool | Yes (10 default) | Eliminates cold start for common case; critical for responsive UX at scale |
| Node autoscaler | Karpenter (not Cluster Autoscaler) | Karpenter provisions nodes in ~60 sec vs ~3 min for CA; bin-packs sessions more efficiently |
| PostgreSQL as StatefulSet sidecar | StatefulSet with ephemeral PVC | Cheaper than RDS per session; PVC deleted with namespace guarantees clean state |
| LocalExecutor vs KubernetesExecutor | LocalExecutor | No task Pod overhead for single-user test sessions; KubernetesExecutor is for production scale |
| TTL cleanup | EventBridge Lambda (external) | External cleanup survives API restarts and namespace failures; more reliable than in-cluster CronJob |
| Ingress | NGINX (not AWS ALB Ingress Controller) | NGINX supports path rewriting + `X-Forwarded-Prefix` header natively; simpler for Airflow URL routing |
| TLS | cert-manager (Let's Encrypt) | Free, auto-renewed certificates; no manual certificate management |
| Node pricing | Mix of on-demand + spot | Free-tier sessions on spot (70% cheaper); paid sessions on on-demand (reliable) |
| Cost per session at scale | ~$0.05/session | Most efficient of three approaches due to bin-packing multiple sessions per node |
| IRSA for AWS access | IAM Roles for Service Accounts | Least-privilege: Session Manager Pod gets only DynamoDB + Secrets Manager access, no node-level AWS credentials |
