# Airflow Test Deployment on Kubernetes (EKS) — Design Document
## Single Namespace, Label-Based Session Management

**Version:** 2.0  
**Date:** March 2026  
**Status:** Draft  
**Supersedes:** Version 1.0 (namespace-per-session approach)

---

## Changelog from v1.0

| Change | v1.0 | v2.0 |
|---|---|---|
| Isolation model | One namespace per session | One shared namespace, labels per session |
| Cleanup | `kubectl delete namespace` | `kubectl delete --selector session-id=X` |
| API server load | High (1000s of namespace objects) | Low (pods + deployments only) |
| NetworkPolicy | Per-namespace (automatic cascade) | Per-session pod label selector |
| Resource governance | ResourceQuota per namespace | LimitRange on namespace + pod-level limits |
| Debugging | `-n session-{id}` | `-l session-id={id}` |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [Why Single Namespace](#3-why-single-namespace)
4. [Architecture](#4-architecture)
5. [EKS Cluster Setup](#5-eks-cluster-setup)
6. [Container & Image Design](#6-container--image-design)
7. [Labeling & Naming Convention](#7-labeling--naming-convention)
8. [Kubernetes Resource Manifests](#8-kubernetes-resource-manifests)
9. [Session Lifecycle Management](#9-session-lifecycle-management)
10. [API Layer](#10-api-layer)
11. [Networking & Ingress Routing](#11-networking--ingress-routing)
12. [Isolation Strategy](#12-isolation-strategy)
13. [Security](#13-security)
14. [Cost Optimization](#14-cost-optimization)
15. [Deployment Guide](#15-deployment-guide)
16. [Monitoring & Observability](#16-monitoring--observability)
17. [Appendix: Design Decisions](#17-appendix-design-decisions)

---

## 1. Overview

This document describes the design for an **on-demand, per-session Airflow test deployment system** built on **Amazon EKS**. All sessions share a **single Kubernetes namespace**. Each session's resources — webserver, scheduler, PostgreSQL — are just labeled containers running inside that namespace, identified and managed entirely through Kubernetes label selectors.

### Key Principle

> One session = One set of labeled Pods in a shared namespace

Kubernetes is a container orchestrator at its core. Namespaces are a logical grouping mechanism designed for long-lived, persistent tenants — not for ephemeral short-lived sessions. For test deployments, labels are the right tool. A session is created by applying labeled resources and terminated by deleting everything with that label.

### Mental Model

Think of it exactly like Docker Compose, but on Kubernetes:

```
Docker Compose (local):
  docker compose -p session-abc up    # All containers prefixed with session-abc
  docker compose -p session-abc down  # All containers removed

Kubernetes (single namespace):
  kubectl apply -l session-id=abc     # All pods labeled session-id=abc
  kubectl delete -l session-id=abc    # All pods removed
```

---

## 2. Goals & Non-Goals

### Goals
- Support hundreds of concurrent customer test sessions in a single namespace
- Launch a fully functional Airflow environment in under 30 seconds
- Keep Kubernetes API server load low — no namespace proliferation
- Isolate sessions at the network and resource level using pod-level policies
- Automatically terminate sessions after TTL expiry
- Make debugging simple — query any session with a single label selector

### Non-Goals
- Not a production Airflow environment
- No persistent DAG storage between sessions
- No multi-user access within a single session
- No namespace-level hard isolation (use ECS Fargate if VM-level isolation is required)

---

## 3. Why Single Namespace

### The Problem with Namespace-Per-Session

Namespaces in Kubernetes carry overhead that compounds at scale:

```
100 sessions (namespace-per-session):
  100 Namespace objects
  100 ResourceQuota objects
  100 NetworkPolicy objects
  100 × ~12 pods, deployments, services, secrets
  ─────────────────────────────────────────────
  ~1,400 objects tracked by the API server simultaneously

100 sessions (single namespace):
  ~12 pods, deployments, services, secrets × 100
  ─────────────────────────────────────────────
  ~1,200 objects — same pods, NO namespace overhead
  API server doesn't need to reconcile namespace metadata
```

Beyond object count, namespace proliferation causes:
- `kubectl get namespaces` returns hundreds of rows — operational noise
- etcd grows faster — namespace metadata stored for every object
- RBAC becomes more complex — namespace-scoped roles multiply
- Admission webhooks fire per-namespace configuration, adding latency

### Why Single Namespace Works Here

Sessions are **ephemeral and homogeneous** — they all run the same Airflow stack with the same resource requirements. Namespaces solve problems like:
- Different teams needing different RBAC
- Different environments (dev/staging/prod) needing different policies
- Long-lived tenants with different quota requirements

None of these apply to test sessions. Labels are the right primitive.

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Many Customers                                 │
│                    (Browser / API Clients)                            │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  POST /sessions/start
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Session Manager API                                │
│             (FastAPI — runs in namespace: system)                    │
│                                                                       │
│  - Generates session ID                                               │
│  - Creates labeled K8s resources in airflow-sessions namespace        │
│  - Registers Ingress rule                                             │
│  - Tracks session state in DynamoDB                                   │
│  - Returns Airflow URL                                                │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  Kubernetes Python Client
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       Amazon EKS Cluster                              │
│                                                                       │
│  namespace: airflow-sessions  (ONE namespace for ALL sessions)        │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │                                                             │     │
│  │  session-id=abc123          session-id=def456              │     │
│  │  ┌──────────────────┐       ┌──────────────────┐           │     │
│  │  │ webserver-abc123 │       │ webserver-def456  │   ...    │     │
│  │  │ scheduler-abc123 │       │ scheduler-def456  │          │     │
│  │  │ postgres-abc123  │       │ postgres-def456   │          │     │
│  │  │ (Service, PVC,   │       │ (Service, PVC,    │          │     │
│  │  │  Secret)         │       │  Secret)          │          │     │
│  │  └──────────────────┘       └──────────────────┘           │     │
│  │                                                             │     │
│  │  NetworkPolicy: pods can only talk to same session-id label │     │
│  │  LimitRange: per-pod CPU/memory bounds                      │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                                                                       │
│  namespace: system                                                    │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Session Manager API                                         │    │
│  │  NGINX Ingress Controller                                    │    │
│  │  Karpenter (node autoscaler)                                 │    │
│  │  Prometheus + Grafana                                        │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  Node Group (on-demand + spot, bin-packed)                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │  m5.xlarge   │  │  m5.xlarge   │  │  m5.xlarge   │               │
│  │  session A   │  │  session C   │  │  session E   │               │
│  │  session B   │  │  session D   │  │  session F   │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
└──────────────────────────────────────────────────────────────────────┘
           │                           │
           ▼                           ▼
┌──────────────────┐       ┌─────────────────────────┐
│    DynamoDB      │       │   NGINX Ingress          │
│  Session State   │       │   /session/{id}/*        │
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
  withOIDC: true    # Required for IRSA

managedNodeGroups:

  # System node group — session manager, ingress, monitoring
  - name: system
    instanceType: m5.large
    minSize: 2
    maxSize: 4
    desiredCapacity: 2
    labels:
      role: system
    taints:
      - key: role
        value: system
        effect: NoSchedule

  # Session workload node group — on-demand, bin-packed
  - name: sessions-ondemand
    instanceType: m5.xlarge    # 4 vCPU, 16 GB — fits 2–3 sessions per node
    minSize: 1
    maxSize: 50
    desiredCapacity: 2
    labels:
      role: session
      tier: ondemand

  # Spot node group for cost-saving on free-tier sessions
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
  - name: aws-ebs-csi-driver
```

```bash
# Create cluster
eksctl create cluster -f cluster/cluster.yaml

# Update kubeconfig
aws eks update-kubeconfig --name airflow-test-cluster --region us-east-1

# Create the two core namespaces (that's it — no per-session namespaces ever)
kubectl create namespace airflow-sessions
kubectl create namespace system
```

### 5.2 Karpenter — Node Autoscaler

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

  limits:
    cpu: 500
    memory: 2000Gi

  disruption:
    consolidationPolicy: WhenUnderutilized
    consolidateAfter: 5m    # Bin-pack and remove underutilized nodes

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

### 5.3 Namespace-Level Policies

Apply these once to the `airflow-sessions` namespace — they apply to all pods within it:

```yaml
# cluster/namespace-policies.yaml

# LimitRange — enforces default + max resource bounds on every pod
# Prevents any single session from consuming unbounded resources
apiVersion: v1
kind: LimitRange
metadata:
  name: session-limits
  namespace: airflow-sessions
spec:
  limits:
    - type: Container
      default:              # Applied if pod doesn't specify limits
        cpu:    "500m"
        memory: "1Gi"
      defaultRequest:       # Applied if pod doesn't specify requests
        cpu:    "250m"
        memory: "512Mi"
      max:                  # Hard ceiling per container
        cpu:    "2"
        memory: "4Gi"
      min:                  # Minimum (prevents starvation)
        cpu:    "50m"
        memory: "64Mi"
```

### 5.4 NGINX Ingress Controller

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace system \
  --set controller.replicaCount=2 \
  --set controller.nodeSelector."role"=system \
  --set controller.tolerations[0].key=role \
  --set controller.tolerations[0].value=system \
  --set controller.tolerations[0].effect=NoSchedule \
  --set controller.service.type=LoadBalancer \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/aws-load-balancer-type"=nlb
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

# Pre-install providers — avoids pulling at session start
RUN pip install --no-cache-dir \
    apache-airflow-providers-amazon \
    apache-airflow-providers-postgres \
    apache-airflow-providers-http \
    apache-airflow-providers-slack \
    apache-airflow-providers-google \
    pandas requests

COPY --chown=airflow:root sample_dags/ /opt/airflow/dags/

# Test-session optimized config
ENV AIRFLOW__CORE__EXECUTOR=LocalExecutor \
    AIRFLOW__CORE__LOAD_EXAMPLES=False \
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=True \
    AIRFLOW__SCHEDULER__USE_JOB_SCHEDULE=False \
    AIRFLOW__WEBSERVER__EXPOSE_CONFIG=True \
    AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX=True \
    AIRFLOW__SCHEDULER__MIN_FILE_PROCESS_INTERVAL=10
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

## 7. Labeling & Naming Convention

### 7.1 Standard Labels

Every resource created for a session carries these labels:

```yaml
labels:
  app.kubernetes.io/session-id:  "{SESSION_ID}"     # Primary selector
  app.kubernetes.io/customer-id: "{CUSTOMER_ID}"    # For customer-level queries
  app.kubernetes.io/component:   "{webserver|scheduler|postgres}"
  app.kubernetes.io/managed-by:  "session-manager"
  app.kubernetes.io/version:     "2.9.2"
```

### 7.2 Resource Naming Pattern

Since all sessions share one namespace, resource names must be unique per session:

```
Resource Type      Naming Pattern                  Example
─────────────────────────────────────────────────────────────────
Deployment         webserver-{session-id}          webserver-a1b2c3d4
Deployment         scheduler-{session-id}          scheduler-a1b2c3d4
StatefulSet        postgres-{session-id}           postgres-a1b2c3d4
Service (postgres) postgres-{session-id}           postgres-a1b2c3d4
Service (webserver)webserver-svc-{session-id}      webserver-svc-a1b2c3d4
Secret             airflow-secrets-{session-id}    airflow-secrets-a1b2c3d4
PVC                postgres-pvc-{session-id}       postgres-pvc-a1b2c3d4
Job                airflow-init-{session-id}       airflow-init-a1b2c3d4
Ingress            ingress-{session-id}            ingress-a1b2c3d4
NetworkPolicy      netpol-{session-id}             netpol-a1b2c3d4
```

> **Note:** Session IDs are UUIDs — truncate to 8 characters for resource names to keep them readable while staying under the 63-character Kubernetes name limit.

### 7.3 Internal DNS Between Containers

In a single namespace, each session's postgres service is reachable by name:

```
postgres-{session-id}.airflow-sessions.svc.cluster.local

# Connection string uses service name:
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN:
  postgresql+psycopg2://airflow:pass@postgres-{session-id}/airflow
```

Because all pods are in the same namespace, the short form also works:
```
postgresql+psycopg2://airflow:pass@postgres-a1b2c3d4/airflow
```

---

## 8. Kubernetes Resource Manifests

All manifests are templates with `{SESSION_ID}`, `{CUSTOMER_ID}`, and credential placeholders replaced at session creation time.

### 8.1 Secret (Per-Session Credentials)

```yaml
# manifests/secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: airflow-secrets-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
    app.kubernetes.io/managed-by:  "session-manager"
type: Opaque
stringData:
  db-password:          "{DB_PASSWORD}"
  db-conn-string:       "postgresql+psycopg2://airflow:{DB_PASSWORD}@postgres-{SESSION_ID}/airflow"
  fernet-key:           "{FERNET_KEY}"
  webserver-secret-key: "{WEBSERVER_SECRET_KEY}"
  admin-password:       "{ADMIN_PASSWORD}"
```

### 8.2 PostgreSQL StatefulSet

```yaml
# manifests/postgres.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
    app.kubernetes.io/component:   "postgres"
    app.kubernetes.io/managed-by:  "session-manager"
spec:
  serviceName: postgres-{SESSION_ID}
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/session-id: "{SESSION_ID}"
      app.kubernetes.io/component:  "postgres"
  template:
    metadata:
      labels:
        app.kubernetes.io/session-id:  "{SESSION_ID}"
        app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
        app.kubernetes.io/component:   "postgres"
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
                  name: airflow-secrets-{SESSION_ID}
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
          volumeMounts:
            - name: postgres-data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: postgres-data
        labels:
          app.kubernetes.io/session-id: "{SESSION_ID}"
          app.kubernetes.io/managed-by: "session-manager"
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: gp3
        resources:
          requests:
            storage: 5Gi

---
# Headless service — allows DNS resolution: postgres-{session-id}.airflow-sessions
apiVersion: v1
kind: Service
metadata:
  name: postgres-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/component:   "postgres"
    app.kubernetes.io/managed-by:  "session-manager"
spec:
  clusterIP: None    # Headless — DNS resolves directly to pod IP
  selector:
    app.kubernetes.io/session-id: "{SESSION_ID}"
    app.kubernetes.io/component:  "postgres"
  ports:
    - port: 5432
      targetPort: 5432
```

### 8.3 Airflow Init Job

```yaml
# manifests/airflow-init.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: airflow-init-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
    app.kubernetes.io/component:   "init"
    app.kubernetes.io/managed-by:  "session-manager"
spec:
  ttlSecondsAfterFinished: 120    # Auto-delete job object 2 min after completion
  backoffLimit: 3
  template:
    metadata:
      labels:
        app.kubernetes.io/session-id: "{SESSION_ID}"
        app.kubernetes.io/component:  "init"
    spec:
      restartPolicy: OnFailure
      nodeSelector:
        role: session
      initContainers:
        - name: wait-for-postgres
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              until nc -z postgres-{SESSION_ID} 5432; do
                echo "Waiting for postgres-{SESSION_ID}..."; sleep 2;
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
                --password $(ADMIN_PASSWORD) \
                --firstname Test \
                --lastname User \
                --role Admin \
                --email admin@test.com
              echo "Init complete"
          env:
            - name: AIRFLOW__DATABASE__SQL_ALCHEMY_CONN
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets-{SESSION_ID}
                  key: db-conn-string
            - name: ADMIN_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets-{SESSION_ID}
                  key: admin-password
          resources:
            requests:
              cpu:    "200m"
              memory: "512Mi"
            limits:
              cpu:    "500m"
              memory: "1Gi"
```

### 8.4 Airflow Webserver Deployment

```yaml
# manifests/airflow-webserver.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: webserver-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
    app.kubernetes.io/component:   "webserver"
    app.kubernetes.io/managed-by:  "session-manager"
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/session-id: "{SESSION_ID}"
      app.kubernetes.io/component:  "webserver"
  template:
    metadata:
      labels:
        app.kubernetes.io/session-id:  "{SESSION_ID}"
        app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
        app.kubernetes.io/component:   "webserver"
    spec:
      nodeSelector:
        role: session
      # Wait for init job to finish before starting webserver
      initContainers:
        - name: wait-for-init
          image: bitnami/kubectl:latest
          command:
            - sh
            - -c
            - |
              kubectl wait job/airflow-init-{SESSION_ID} \
                --for=condition=complete \
                --timeout=120s \
                -n airflow-sessions
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
                  name: airflow-secrets-{SESSION_ID}
                  key: db-conn-string
            - name: AIRFLOW__CORE__FERNET_KEY
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets-{SESSION_ID}
                  key: fernet-key
            - name: AIRFLOW__WEBSERVER__SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets-{SESSION_ID}
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
  name: webserver-svc-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/component:   "webserver"
    app.kubernetes.io/managed-by:  "session-manager"
spec:
  selector:
    app.kubernetes.io/session-id: "{SESSION_ID}"
    app.kubernetes.io/component:  "webserver"
  ports:
    - port: 8080
      targetPort: 8080
```

### 8.5 Airflow Scheduler Deployment

```yaml
# manifests/airflow-scheduler.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: scheduler-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
    app.kubernetes.io/component:   "scheduler"
    app.kubernetes.io/managed-by:  "session-manager"
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/session-id: "{SESSION_ID}"
      app.kubernetes.io/component:  "scheduler"
  template:
    metadata:
      labels:
        app.kubernetes.io/session-id:  "{SESSION_ID}"
        app.kubernetes.io/customer-id: "{CUSTOMER_ID}"
        app.kubernetes.io/component:   "scheduler"
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
                  name: airflow-secrets-{SESSION_ID}
                  key: db-conn-string
            - name: AIRFLOW__CORE__FERNET_KEY
              valueFrom:
                secretKeyRef:
                  name: airflow-secrets-{SESSION_ID}
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
                - airflow jobs check --job-type SchedulerJob --hostname $(hostname)
            initialDelaySeconds: 30
            periodSeconds: 30
```

### 8.6 NetworkPolicy (Pod-Level Isolation)

```yaml
# manifests/network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: netpol-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/managed-by:  "session-manager"
spec:
  # Apply ONLY to pods belonging to this session
  podSelector:
    matchLabels:
      app.kubernetes.io/session-id: "{SESSION_ID}"
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # Allow traffic only from pods with the SAME session-id label
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/session-id: "{SESSION_ID}"
    # Allow traffic from NGINX Ingress pods (in system namespace)
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: ingress-nginx
  egress:
    # Allow traffic to pods with the same session-id (webserver → postgres, etc.)
    - to:
        - podSelector:
            matchLabels:
              app.kubernetes.io/session-id: "{SESSION_ID}"
    # Allow DNS
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
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
```

### 8.7 Ingress Rule (Per-Session)

```yaml
# manifests/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ingress-{SESSION_ID}
  namespace: airflow-sessions
  labels:
    app.kubernetes.io/session-id:  "{SESSION_ID}"
    app.kubernetes.io/managed-by:  "session-manager"
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
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
                name: webserver-svc-{SESSION_ID}
                port:
                  number: 8080
```

---

## 9. Session Lifecycle Management

### 9.1 Session States

```
REQUESTED → LAUNCHING → READY → ACTIVE → TERMINATING → TERMINATED
```

| State | Duration | What's Happening |
|---|---|---|
| `REQUESTED` | Seconds | API received request, about to create K8s resources |
| `LAUNCHING` | 20–30 sec | Pods being scheduled, init job running |
| `READY` | — | Webserver healthy, URL returned to customer |
| `ACTIVE` | Up to TTL | Customer using Airflow |
| `TERMINATING` | 10–30 sec | Label-selector delete in progress |
| `TERMINATED` | — | All pods gone, PVC deleted, DynamoDB updated |

### 9.2 Resource Creation Order

The order resources are applied matters — postgres must be healthy before init runs, init must complete before webserver starts:

```
1. Secret                    (credentials available immediately)
2. StatefulSet: postgres     (start DB)
3. Service: postgres         (DNS available)
4. Job: airflow-init         (waits for postgres via initContainer)
5. Deployment: webserver     (waits for init job via initContainer)
6. Deployment: scheduler     (waits for init job via initContainer)
7. Service: webserver-svc    (expose webserver)
8. NetworkPolicy             (isolate session pods)
9. Ingress                   (route external traffic)
```

### 9.3 DynamoDB Session Store

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
        session_id:  str,
        customer_id: str,
        ttl_minutes: int,
    ) -> dict:
        now       = datetime.now(timezone.utc)
        ttl_epoch = int(now.timestamp()) + (ttl_minutes * 60)

        item = {
            "session_id":   session_id,
            "customer_id":  customer_id,
            "namespace":    "airflow-sessions",   # Always the same namespace
            "status":       "REQUESTED",
            "created_at":   now.isoformat(),
            "ttl":          ttl_epoch,
            "ttl_minutes":  ttl_minutes,
            "airflow_url":  None,
        }
        self.table.put_item(Item=item)
        return item

    def update_session(self, session_id: str, updates: dict):
        update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
        expr_names  = {f"#{k}": k for k in updates}
        expr_values = {f":{k}": v for k, v in updates.items()}
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

### 9.4 TTL Cleanup — Lambda + EventBridge

```python
# lambda/ttl_cleanup/handler.py
import boto3
from kubernetes import client, config
from datetime import datetime, timezone

config.load_incluster_config()
k8s_apps   = client.AppsV1Api()
k8s_core   = client.CoreV1Api()
k8s_batch  = client.BatchV1Api()
k8s_net    = client.NetworkingV1Api()
dynamodb   = boto3.resource("dynamodb", region_name="us-east-1")
TABLE      = dynamodb.Table("airflow-test-sessions")
NAMESPACE  = "airflow-sessions"

def delete_session_resources(session_id: str):
    """
    Delete all Kubernetes resources for a session using label selector.
    Single label selector cleans up everything — no namespace delete needed.
    """
    selector = f"app.kubernetes.io/session-id={session_id}"

    # Delete in order to avoid dangling dependencies
    try:
        k8s_net.delete_collection_namespaced_network_policy(
            NAMESPACE, label_selector=selector)
    except Exception as e:
        print(f"NetworkPolicy delete: {e}")

    try:
        k8s_net.delete_collection_namespaced_ingress(
            NAMESPACE, label_selector=selector)
    except Exception as e:
        print(f"Ingress delete: {e}")

    try:
        k8s_apps.delete_collection_namespaced_deployment(
            NAMESPACE, label_selector=selector)
    except Exception as e:
        print(f"Deployment delete: {e}")

    try:
        k8s_batch.delete_collection_namespaced_job(
            NAMESPACE, label_selector=selector,
            body=client.V1DeleteOptions(propagation_policy="Background"))
    except Exception as e:
        print(f"Job delete: {e}")

    try:
        k8s_apps.delete_collection_namespaced_stateful_set(
            NAMESPACE, label_selector=selector)
    except Exception as e:
        print(f"StatefulSet delete: {e}")

    try:
        k8s_core.delete_collection_namespaced_service(
            NAMESPACE, label_selector=selector)
    except Exception as e:
        print(f"Service delete: {e}")

    try:
        k8s_core.delete_collection_namespaced_secret(
            NAMESPACE, label_selector=selector)
    except Exception as e:
        print(f"Secret delete: {e}")

    try:
        # PVCs must be deleted explicitly — not cascade-deleted with StatefulSet
        k8s_core.delete_collection_namespaced_persistent_volume_claim(
            NAMESPACE, label_selector=selector)
    except Exception as e:
        print(f"PVC delete: {e}")

    print(f"Session {session_id} resources deleted")


def handler(event, context):
    """
    Triggered by EventBridge every 5 minutes.
    Finds sessions past TTL and cleans up their K8s resources.
    """
    now_epoch = int(datetime.now(timezone.utc).timestamp())

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
        print(f"Terminating expired session: {session_id}")

        delete_session_resources(session_id)

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

## 10. API Layer

### 10.1 Kubernetes Session Launcher

```python
# k8s_launcher.py
import uuid
import yaml
import secrets as secrets_lib
from pathlib import Path
from kubernetes import client, config
from cryptography.fernet import Fernet

config.load_incluster_config()
k8s_apps   = client.AppsV1Api()
k8s_core   = client.CoreV1Api()
k8s_batch  = client.BatchV1Api()
k8s_net    = client.NetworkingV1Api()

NAMESPACE     = "airflow-sessions"
ECR_IMAGE     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/airflow-test:2.9.2"
BASE_DOMAIN   = "test.yourdomain.com"
MANIFESTS_DIR = Path("/app/manifests")


def generate_session_id() -> str:
    """Short session ID — safe for K8s resource names (8 hex chars)."""
    return uuid.uuid4().hex[:8]


def generate_credentials() -> dict:
    return {
        "db-password":          secrets_lib.token_urlsafe(24),
        "fernet-key":           Fernet.generate_key().decode(),
        "webserver-secret-key": secrets_lib.token_urlsafe(32),
        "admin-password":       secrets_lib.token_urlsafe(16),
    }


def render(template_name: str, replacements: dict) -> dict:
    """Load manifest template and substitute placeholders."""
    content = (MANIFESTS_DIR / template_name).read_text()
    for key, value in replacements.items():
        content = content.replace(f"{{{key}}}", str(value))
    return yaml.safe_load(content)


def render_all(template_name: str, replacements: dict) -> list:
    """Load multi-document YAML manifest (separated by ---)."""
    content = (MANIFESTS_DIR / template_name).read_text()
    for key, value in replacements.items():
        content = content.replace(f"{{{key}}}", str(value))
    return [doc for doc in yaml.safe_load_all(content) if doc]


def launch_session(session_id: str, customer_id: str) -> dict:
    creds = generate_credentials()

    r = {
        "SESSION_ID":          session_id,
        "CUSTOMER_ID":         customer_id,
        "ECR_IMAGE":           ECR_IMAGE,
        "DB_PASSWORD":         creds["db-password"],
        "FERNET_KEY":          creds["fernet-key"],
        "WEBSERVER_SECRET_KEY": creds["webserver-secret-key"],
        "ADMIN_PASSWORD":      creds["admin-password"],
    }

    # 1. Secret
    k8s_core.create_namespaced_secret(NAMESPACE, body=render("secret.yaml", r))

    # 2. Postgres StatefulSet + Service
    for doc in render_all("postgres.yaml", r):
        if doc["kind"] == "StatefulSet":
            k8s_apps.create_namespaced_stateful_set(NAMESPACE, body=doc)
        elif doc["kind"] == "Service":
            k8s_core.create_namespaced_service(NAMESPACE, body=doc)

    # 3. Init Job
    k8s_batch.create_namespaced_job(NAMESPACE, body=render("airflow-init.yaml", r))

    # 4. Webserver Deployment + Service
    for doc in render_all("airflow-webserver.yaml", r):
        if doc["kind"] == "Deployment":
            k8s_apps.create_namespaced_deployment(NAMESPACE, body=doc)
        elif doc["kind"] == "Service":
            k8s_core.create_namespaced_service(NAMESPACE, body=doc)

    # 5. Scheduler Deployment
    k8s_apps.create_namespaced_deployment(
        NAMESPACE, body=render("airflow-scheduler.yaml", r))

    # 6. NetworkPolicy
    k8s_net.create_namespaced_network_policy(
        NAMESPACE, body=render("network-policy.yaml", r))

    # 7. Ingress
    k8s_net.create_namespaced_ingress(NAMESPACE, body=render("ingress.yaml", r))

    return {
        "airflow_url": f"https://{BASE_DOMAIN}/session/{session_id}",
        "credentials": {
            "username": "admin",
            "password": creds["admin-password"],
        },
    }


def terminate_session(session_id: str):
    """Delete all resources for this session via label selector."""
    selector = f"app.kubernetes.io/session-id={session_id}"

    for fn, name in [
        (k8s_net.delete_collection_namespaced_network_policy,   "NetworkPolicy"),
        (k8s_net.delete_collection_namespaced_ingress,          "Ingress"),
        (k8s_apps.delete_collection_namespaced_deployment,      "Deployment"),
        (k8s_batch.delete_collection_namespaced_job,            "Job"),
        (k8s_apps.delete_collection_namespaced_stateful_set,    "StatefulSet"),
        (k8s_core.delete_collection_namespaced_service,         "Service"),
        (k8s_core.delete_collection_namespaced_secret,          "Secret"),
        (k8s_core.delete_collection_namespaced_persistent_volume_claim, "PVC"),
    ]:
        try:
            fn(NAMESPACE, label_selector=selector)
            print(f"Deleted {name} for session {session_id}")
        except Exception as e:
            print(f"Warning deleting {name}: {e}")
```

### 10.2 FastAPI Session Manager

```python
# main.py
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from k8s_launcher import launch_session, terminate_session, generate_session_id
from session_store import SessionStore

app   = FastAPI(title="Airflow Test Session Manager — Kubernetes Single Namespace")
store = SessionStore()

MAX_TTL_MINUTES     = 120
DEFAULT_TTL_MINUTES = 60


class StartSessionRequest(BaseModel):
    customer_id: str
    ttl_minutes: int = DEFAULT_TTL_MINUTES


@app.post("/sessions/start")
async def start_session(request: StartSessionRequest):
    ttl = min(request.ttl_minutes, MAX_TTL_MINUTES)

    # One session per customer at a time
    active = store.list_active_sessions(request.customer_id)
    if active:
        raise HTTPException(status_code=409, detail={
            "error":       "active_session_exists",
            "session_id":  active[0]["session_id"],
            "airflow_url": active[0].get("airflow_url"),
        })

    session_id = generate_session_id()
    store.create_session(session_id, request.customer_id, ttl)
    store.update_session(session_id, {"status": "LAUNCHING"})

    try:
        result = await asyncio.to_thread(
            launch_session, session_id, request.customer_id
        )

        # Poll Airflow health until ready
        await wait_for_airflow(result["airflow_url"], timeout=120)

        store.update_session(session_id, {
            "status":      "READY",
            "airflow_url": result["airflow_url"],
        })

        return {
            "session_id":   session_id,
            "airflow_url":  result["airflow_url"],
            "status":       "READY",
            "ttl_minutes":  ttl,
            "credentials":  result["credentials"],
        }

    except Exception as e:
        store.update_session(session_id, {"status": "FAILED"})
        # Attempt cleanup on failure
        await asyncio.to_thread(terminate_session, session_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/sessions/{session_id}")
async def end_session(session_id: str):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") in ("TERMINATED", "TERMINATING"):
        return {"message": "Already terminated", "session_id": session_id}

    store.update_session(session_id, {"status": "TERMINATING"})
    await asyncio.to_thread(terminate_session, session_id)
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

## 11. Networking & Ingress Routing

### 11.1 Path-Based Routing

Every session is accessible at a unique path on a single shared domain:

```
https://test.yourdomain.com/session/{session-id}/home
https://test.yourdomain.com/session/{session-id}/dags
https://test.yourdomain.com/session/{session-id}/graph?dag_id=...
```

NGINX rewrites the path before forwarding to Airflow:

```
Incoming:  GET /session/a1b2c3d4/dags
           ↓  NGINX strips prefix
Forwarded: GET /dags  (to webserver-svc-a1b2c3d4:8080)
           ↓  X-Forwarded-Prefix: /session/a1b2c3d4
Airflow:   Reconstructs full URLs using BASE_URL config
```

### 11.2 TLS via cert-manager

```bash
# Install cert-manager
helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager \
  --namespace system \
  --set installCRDs=true

# ClusterIssuer — Let's Encrypt
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

---

## 12. Isolation Strategy

### 12.1 How Sessions Are Isolated Without Namespaces

| Isolation Type | Mechanism | Detail |
|---|---|---|
| **Network** | NetworkPolicy (pod label selector) | Pod in session A can't reach pods in session B |
| **Resource** | Pod-level `limits` + namespace `LimitRange` | Session A can't starve session B of CPU/memory |
| **Secrets** | Per-session K8s Secret (`airflow-secrets-{id}`) | Session A's pods can't read session B's credentials |
| **DNS** | Service naming (`postgres-{session-id}`) | Session A's webserver connects to `postgres-{id-A}` not `postgres-{id-B}` |
| **Storage** | Per-session PVC (`postgres-pvc-{id}`) | Each postgres pod has its own EBS volume |

### 12.2 NetworkPolicy Isolation Visualized

```
namespace: airflow-sessions

  session-id=abc123               session-id=def456
  ┌──────────────────────┐        ┌──────────────────────┐
  │ webserver-abc123     │        │ webserver-def456     │
  │ scheduler-abc123     │◄──────►│ scheduler-def456     │
  │ postgres-abc123      │   ✗    │ postgres-def456      │
  └──────────────────────┘        └──────────────────────┘
  (blocked by NetworkPolicy — pods can only talk to same session-id)

  NGINX Ingress Controller
  ┌──────────────────────┐
  │ /session/abc123/* ───┼──────► webserver-svc-abc123:8080  ✅
  │ /session/def456/* ───┼──────► webserver-svc-def456:8080  ✅
  │ /session/abc123/* ───┼──X───► webserver-svc-def456:8080  ✗ (wrong rule)
  └──────────────────────┘
```

---

## 13. Security

### 13.1 IRSA — IAM Roles for Service Accounts

```bash
# Session Manager API gets scoped AWS permissions
eksctl create iamserviceaccount \
  --name session-manager \
  --namespace system \
  --cluster airflow-test-cluster \
  --attach-policy-arn arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess \
  --attach-policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite \
  --approve
```

### 13.2 RBAC

```yaml
# rbac/session-manager-role.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role                    # Role (not ClusterRole) — scoped to airflow-sessions only
metadata:
  name: session-manager
  namespace: airflow-sessions
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "secrets", "persistentvolumeclaims"]
    verbs: ["create", "get", "list", "delete", "patch", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets"]
    verbs: ["create", "get", "list", "delete", "patch"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "delete", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses", "networkpolicies"]
    verbs: ["create", "get", "list", "delete", "patch"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: session-manager
  namespace: airflow-sessions
subjects:
  - kind: ServiceAccount
    name: session-manager
    namespace: system
roleRef:
  kind: Role
  name: session-manager
  apiGroup: rbac.authorization.k8s.io
```

> **Note:** Using `Role` (not `ClusterRole`) scopes the Session Manager to only the `airflow-sessions` namespace. It cannot read or write to any other namespace.

### 13.3 Per-Session Credentials

```python
# secrets.py
import secrets
from cryptography.fernet import Fernet

def generate_session_credentials() -> dict:
    """Fresh credentials for every session — never reused."""
    return {
        "db-password":          secrets.token_urlsafe(24),
        "fernet-key":           Fernet.generate_key().decode(),
        "webserver-secret-key": secrets.token_urlsafe(32),
        "admin-password":       secrets.token_urlsafe(16),
    }
```

Credentials are stored only in the per-session Kubernetes Secret (`airflow-secrets-{session-id}`). When the session is terminated and the Secret is deleted, the credentials are gone — no AWS Secrets Manager needed.

---

## 14. Cost Optimization

### 14.1 Cost Per Session (Bin-Packed)

An `m5.xlarge` (4 vCPU, 16 GB) fits approximately 2 sessions (each using ~1.25 vCPU, ~2.5 GB):

| Resource | Rate | 1-hour Session | Notes |
|---|---|---|---|
| Compute (½ m5.xlarge) | $0.096/hr | ~$0.048 | Bin-packed — 2 sessions per node |
| EBS PVC (5 GB postgres) | ~$0.0001/hr | ~$0.0001 | Deleted after session |
| NLB (shared) | ~$0.002/hr | ~$0.002 | One NLB for all sessions |
| EKS cluster fee | $0.10/hr | ~$0.002 | Amortized across sessions |
| **Total** | | **~$0.05/session** | Per 1-hour session |

### 14.2 Cost Controls

```python
# cost_controls.py

MAX_TTL_MINUTES         = 120
MAX_CONCURRENT_SESSIONS = 500

def get_active_session_count() -> int:
    """Count active sessions by querying running pods in the namespace."""
    k8s_core = client.CoreV1Api()
    pods = k8s_core.list_namespaced_pod(
        "airflow-sessions",
        label_selector="app.kubernetes.io/component=webserver"
    )
    return len([p for p in pods.items if p.status.phase == "Running"])

def cleanup_orphaned_sessions():
    """
    Find pods older than 3 hours in the airflow-sessions namespace
    and delete all resources for that session.
    Runs as a Lambda every 15 minutes as a safety net.
    """
    from datetime import datetime, timezone, timedelta
    k8s_core = client.CoreV1Api()
    pods = k8s_core.list_namespaced_pod(
        "airflow-sessions",
        label_selector="app.kubernetes.io/component=webserver"
    )
    now = datetime.now(timezone.utc)
    for pod in pods.items:
        age = now - pod.metadata.creation_timestamp.replace(tzinfo=timezone.utc)
        if age > timedelta(hours=3):
            session_id = pod.metadata.labels.get("app.kubernetes.io/session-id")
            if session_id:
                print(f"Orphaned session: {session_id} — age: {age}")
                terminate_session(session_id)
```

---

## 15. Deployment Guide

### 15.1 Prerequisites

```bash
# Install tools
brew install eksctl kubectl helm
pip install kubernetes boto3 fastapi uvicorn httpx pydantic cryptography

# Configure AWS
aws configure

# Create EKS cluster
eksctl create cluster -f cluster/cluster.yaml

# Update kubeconfig
aws eks update-kubeconfig --name airflow-test-cluster --region us-east-1
```

### 15.2 Step-by-Step Deployment

```bash
# Step 1: Create namespaces
kubectl create namespace airflow-sessions
kubectl create namespace system

# Step 2: Apply namespace-level policies (once only)
kubectl apply -f cluster/namespace-policies.yaml

# Step 3: Install NGINX Ingress
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace system

# Step 4: Install cert-manager
helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager \
  --namespace system --set installCRDs=true

# Step 5: Install Karpenter
helm install karpenter oci://public.ecr.aws/karpenter/karpenter \
  --namespace system --version v0.33.0
kubectl apply -f cluster/karpenter-nodepool.yaml

# Step 6: Apply RBAC
kubectl apply -f rbac/session-manager-role.yaml

# Step 7: Build and push Airflow image
./scripts/build_and_push.sh

# Step 8: Deploy Session Manager API
kubectl apply -f k8s/session-manager-deployment.yaml

# Step 9: Create DynamoDB table
aws dynamodb create-table \
  --table-name airflow-test-sessions \
  --attribute-definitions \
    AttributeName=session_id,AttributeType=S \
    AttributeName=customer_id,AttributeType=S \
  --key-schema AttributeName=session_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --global-secondary-indexes '[
    {
      "IndexName": "customer_id-index",
      "KeySchema": [{"AttributeName": "customer_id","KeyType":"HASH"}],
      "Projection": {"ProjectionType": "ALL"}
    }
  ]'

aws dynamodb update-time-to-live \
  --table-name airflow-test-sessions \
  --time-to-live-specification Enabled=true,AttributeName=ttl
```

### 15.3 Example API Usage

```bash
# Start a session
curl -X POST https://api.yourdomain.com/sessions/start \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "cust_123", "ttl_minutes": 60}'

# Response:
# {
#   "session_id": "a1b2c3d4",
#   "airflow_url": "https://test.yourdomain.com/session/a1b2c3d4",
#   "status": "READY",
#   "ttl_minutes": 60,
#   "credentials": {"username": "admin", "password": "xK9mP2qR..."}
# }

# Inspect all running sessions (single namespace — clean and simple)
kubectl get pods -n airflow-sessions \
  -L app.kubernetes.io/session-id,app.kubernetes.io/component

# Inspect a specific session
kubectl get all -n airflow-sessions \
  -l app.kubernetes.io/session-id=a1b2c3d4

# Logs for a specific session
kubectl logs -n airflow-sessions \
  -l app.kubernetes.io/session-id=a1b2c3d4,app.kubernetes.io/component=webserver

# End session early
curl -X DELETE https://api.yourdomain.com/sessions/a1b2c3d4
```

### 15.4 Project Structure

```
airflow-k8s/
├── api/
│   ├── main.py                  # FastAPI Session Manager
│   ├── k8s_launcher.py          # K8s resource create/delete
│   ├── session_store.py         # DynamoDB session state
│   ├── cost_controls.py         # Session caps + orphan cleanup
│   └── requirements.txt
├── manifests/
│   ├── secret.yaml
│   ├── postgres.yaml            # StatefulSet + headless Service
│   ├── airflow-init.yaml        # Init Job
│   ├── airflow-webserver.yaml   # Deployment + Service
│   ├── airflow-scheduler.yaml   # Deployment
│   ├── network-policy.yaml      # Pod-level isolation
│   └── ingress.yaml
├── lambda/
│   └── ttl_cleanup/
│       └── handler.py
├── cluster/
│   ├── cluster.yaml             # eksctl definition
│   ├── karpenter-nodepool.yaml
│   └── namespace-policies.yaml  # LimitRange (applied once)
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

## 16. Monitoring & Observability

### 16.1 Useful kubectl Commands for Operations

```bash
# How many sessions are currently active?
kubectl get pods -n airflow-sessions \
  -l app.kubernetes.io/component=webserver \
  --field-selector=status.phase=Running | wc -l

# Which sessions are unhealthy?
kubectl get pods -n airflow-sessions \
  --field-selector=status.phase!=Running \
  -L app.kubernetes.io/session-id

# Resource usage across all sessions
kubectl top pods -n airflow-sessions \
  --sort-by=cpu

# Are any pods in CrashLoopBackOff?
kubectl get pods -n airflow-sessions | grep -v Running | grep -v Completed
```

### 16.2 Prometheus Metrics

```python
# monitoring.py
from prometheus_client import Counter, Histogram, Gauge, start_http_server

sessions_started  = Counter("airflow_test_sessions_started_total",  "Total sessions started")
sessions_failed   = Counter("airflow_test_sessions_failed_total",   "Total sessions failed")
sessions_active   = Gauge(  "airflow_test_active_sessions",         "Currently active sessions")
startup_duration  = Histogram(
    "airflow_test_startup_seconds",
    "Time from request to READY",
    buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120]
)

start_http_server(9090)   # Scraped by Prometheus in system namespace
```

### 16.3 Key Alerts

| Alert | Threshold | Action |
|---|---|---|
| `airflow_test_active_sessions` | > 400 | Scale review |
| `airflow_test_sessions_failed` | > 3 in 5 min | Investigate pod scheduling |
| Pod `CrashLoopBackOff` | Any | Check logs: `kubectl logs -n airflow-sessions -l session-id=X` |
| `airflow_test_startup_seconds` p95 | > 60 sec | Check node availability, image pull times |
| Node CPU | > 80% | Karpenter should auto-provision — check if stuck |

---

## 17. Appendix: Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Single namespace vs namespace-per-session | **Single namespace** | No namespace proliferation; same isolation achievable via labels + NetworkPolicy |
| Session identification | Kubernetes labels (`session-id`) | Standard K8s primitive — works with all kubectl commands, selectors, and policies |
| Cleanup mechanism | `delete_collection` with label selector | One call per resource type removes everything — clean and atomic |
| NetworkPolicy scope | Pod label selector (not namespace) | Achieves same cross-session isolation as namespace boundary |
| RBAC scope | `Role` (not `ClusterRole`) | Scopes Session Manager to `airflow-sessions` namespace only |
| Per-session Secrets | Kubernetes Secret in shared namespace | Deleted with session; no AWS Secrets Manager needed |
| PostgreSQL | StatefulSet sidecar per session | Ephemeral — PVC deleted when session ends; cheaper than RDS per session |
| Node autoscaler | Karpenter | Faster node provisioning (~60 sec) than Cluster Autoscaler; better bin-packing |
| Executor | LocalExecutor | No Redis/Celery overhead for single-user test sessions |
| Scheduler | `USE_JOB_SCHEDULE=false` | Prevents auto-scheduling; test sessions trigger DAGs manually only |
| Cost per session | ~$0.05/session | Bin-packing 2 sessions per m5.xlarge node — cheapest of all three approaches |