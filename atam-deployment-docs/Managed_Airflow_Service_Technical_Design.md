# Managed Apache Airflow Service — Technical Design & Implementation Guide

> **Audience:** Engineering team leads, platform engineers, backend engineers  
> **Purpose:** Deep technical reference for building an Astronomer-like managed Airflow service on AWS  
> **Status:** Draft v1.0 — Pre-implementation  
> **Last Updated:** March 2026

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Deep Dive](#2-architecture-deep-dive)
3. [Tenant Isolation Model](#3-tenant-isolation-model)
4. [AWS Infrastructure Specification](#4-aws-infrastructure-specification)
5. [Kubernetes & Helm Strategy](#5-kubernetes--helm-strategy)
6. [Provisioning Engine](#6-provisioning-engine)
7. [Control Plane Components](#7-control-plane-components)
8. [Airflow Component Configuration](#8-airflow-component-configuration)
9. [Networking & Ingress](#9-networking--ingress)
10. [Secrets & Configuration Management](#10-secrets--configuration-management)
11. [Observability Stack](#11-observability-stack)
12. [Security Architecture](#12-security-architecture)
13. [GitOps & CI/CD Pipeline](#13-gitops--cicd-pipeline)
14. [Database Strategy](#14-database-strategy)
15. [Scaling & Cost Optimization](#15-scaling--cost-optimization)
16. [API Design](#16-api-design)
17. [Data Model](#17-data-model)
18. [Phased Delivery Plan](#18-phased-delivery-plan)
19. [Runbooks & Operational Procedures](#19-runbooks--operational-procedures)
20. [Open Questions & Decision Log](#20-open-questions--decision-log)

---

## 1. System Overview

### What We Are Building

A **multi-tenant, fully managed Apache Airflow service** — customers provision isolated Airflow environments via a web dashboard or API. We manage all infrastructure; they manage their DAGs and pipelines.

This is architecturally equivalent to [Astronomer Astro](https://www.astronomer.io/product/astro/) — a namespace-per-tenant Kubernetes model with a centralized control plane and isolated data planes.

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CONTROL PLANE                             │
│  Web Dashboard → Provisioning API → Deployment Controller    │
│  Auth (Cognito/OIDC) · Billing (Stripe) · Secrets Manager   │
└─────────────────────┬───────────────────────────────────────┘
                      │ Helm install / Kubernetes API
┌─────────────────────▼───────────────────────────────────────┐
│                    DATA PLANE (EKS Cluster)                   │
│                                                               │
│  ┌─────────────────────┐   ┌─────────────────────┐          │
│  │  Namespace: tenant-A │   │  Namespace: tenant-B │  ...    │
│  │  Webserver            │   │  Webserver            │         │
│  │  Scheduler            │   │  Scheduler            │         │
│  │  Workers (0-N)        │   │  Workers (0-N)        │         │
│  │  Triggerer            │   │  Triggerer            │         │
│  │  Git-sync sidecar     │   │  Git-sync sidecar     │         │
│  │  RDS Postgres (own)   │   │  RDS Postgres (own)   │         │
│  └─────────────────────┘   └─────────────────────┘          │
└─────────────────────┬───────────────────────────────────────┘
                      │ scrape / ship logs
┌─────────────────────▼───────────────────────────────────────┐
│                  OBSERVABILITY PLANE                          │
│  Prometheus · Grafana · Fluent Bit → S3 · AlertManager → SNS │
└─────────────────────────────────────────────────────────────┘
```

### Design Principles

| Principle | Implementation |
|---|---|
| **Tenant isolation** | Kubernetes namespace per tenant + NetworkPolicy deny-all + dedicated RDS |
| **Declarative everything** | Helm values in Git, ArgoCD syncs, no manual kubectl |
| **Scale to zero** | Workers scale to 0 when idle via KEDA; environments hibernate on schedule |
| **Defense in depth** | 6 isolation layers from VPC boundary to container Seccomp |
| **Operator-first** | Custom Kubernetes operator owns all lifecycle (create/update/delete/hibernate) |
| **Observable by default** | Every namespace auto-enrolled in Prometheus + Fluent Bit at provisioning time |

---

## 2. Architecture Deep Dive

### Control Plane

The control plane is the **management layer** — it does not run Airflow. It runs in a separate EKS namespace (`platform-system`) or on Fargate for isolation.

**Components:**

| Component | Tech Stack | Responsibility |
|---|---|---|
| Web Dashboard | React 18 + TypeScript + Vite | Customer-facing UI for environment management |
| API Gateway | AWS API Gateway (HTTP API) | TLS termination, rate limiting, auth JWT validation |
| Provisioning API | FastAPI (Python 3.12) or Node.js 20 | REST API — create/read/update/delete environments |
| Deployment Controller | Go 1.22 + controller-runtime | Kubernetes operator; watches `AirflowDeployment` CRD |
| Billing Service | FastAPI + Stripe SDK | Metering, usage collection, invoice generation |
| Auth Service | AWS Cognito + Lambda@Edge | User pools, JWT issuance, SAML federation |

**Control plane deployment topology:**
```
AWS Account
└── VPC (10.0.0.0/16)
    ├── Private subnets (10.0.1.0/24, 10.0.2.0/24, 10.0.3.0/24)
    │   └── EKS Cluster
    │       ├── Namespace: platform-system      ← control plane
    │       ├── Namespace: argocd               ← GitOps
    │       ├── Namespace: monitoring           ← Prometheus, Grafana
    │       ├── Namespace: tenant-abc123        ← customer environment
    │       └── Namespace: tenant-xyz789        ← customer environment
    └── Public subnets (10.0.101.0/24, ...)
        └── Application Load Balancer (internet-facing)
```

### Data Plane

The data plane is **where Airflow runs**. Each tenant gets exactly one Kubernetes namespace. Within that namespace, all Airflow components are deployed via the official Helm chart.

**Per-tenant namespace contents:**
```
Namespace: tenant-{tenantId}
├── Deployments
│   ├── airflow-webserver        (1 replica, always-on)
│   ├── airflow-scheduler        (1 replica, always-on)
│   └── airflow-triggerer        (1 replica, always-on)
├── StatefulSets / Jobs
│   └── airflow-worker           (0-N replicas, KEDA-managed)
├── Services
│   ├── airflow-webserver-svc    (ClusterIP)
│   └── airflow-worker-svc       (Headless, for Celery)
├── Ingress
│   └── airflow-ingress          ({tenantId}.platform.com → webserver)
├── ConfigMaps
│   ├── airflow-config           (airflow.cfg overrides)
│   └── airflow-env              (non-secret env vars)
├── Secrets
│   ├── airflow-fernet-key       (synced from Secrets Manager)
│   ├── airflow-metadata-secret  (RDS connection string)
│   └── airflow-connections      (customer-defined connections)
├── PersistentVolumeClaims
│   └── airflow-logs             (EFS or gp3 for log buffering)
├── ServiceAccount
│   └── airflow-sa               (bound to IRSA IAM role)
├── NetworkPolicy
│   ├── deny-all-ingress
│   ├── deny-all-egress
│   ├── allow-airflow-internal
│   ├── allow-egress-rds
│   └── allow-egress-s3-secretsmanager
└── ResourceQuota
    └── tenant-quota             (CPU/memory/pod limits per plan)
```

---

## 3. Tenant Isolation Model

### Isolation Strategy: Namespace-per-Tenant on Shared EKS

We use the **namespace-per-tenant** model — the same approach Astronomer uses for its "standard cluster" offering. Each customer gets one namespace; multiple customers share the same EKS cluster nodes.

**Comparison of isolation models:**

| Model | Isolation Level | Cost/Tenant | Spin-up Time | We Use? |
|---|---|---|---|---|
| Separate AWS Account per tenant | Maximum | $$$$ | ~30 min | No (too expensive at scale) |
| Separate EKS Cluster per tenant | Very High | $$$ | ~15 min | Phase 3 Enterprise tier only |
| Separate Node Group per tenant | High | $$ | ~5 min | Optional for regulated tenants |
| Namespace per tenant (Calico) | Medium-High | $ | ~2 min | **Yes — default model** |
| Shared namespace | Low | $$ | <30s | Never (no isolation) |

### The Six Isolation Controls

#### Control 1: Kubernetes Namespace

Every tenant is scoped to a single namespace. Kubernetes RBAC, resource quotas, and network policies are all namespace-scoped.

```bash
# Namespace creation — applied at provisioning time
kubectl create namespace tenant-${TENANT_ID}
kubectl label namespace tenant-${TENANT_ID} \
  tenant-id=${TENANT_ID} \
  platform-managed=true \
  environment-tier=${PLAN_TIER}
```

#### Control 2: NetworkPolicy (Calico CNI)

Default deny-all policy applied to every new namespace. Explicit allow rules added for required traffic only.

```yaml
# 1. Deny all ingress
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-ingress
  namespace: tenant-${TENANT_ID}
spec:
  podSelector: {}
  policyTypes: ["Ingress"]

---
# 2. Deny all egress
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-egress
  namespace: tenant-${TENANT_ID}
spec:
  podSelector: {}
  policyTypes: ["Egress"]

---
# 3. Allow intra-namespace traffic (Airflow components talk to each other)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-intra-namespace
  namespace: tenant-${TENANT_ID}
spec:
  podSelector: {}
  ingress:
    - from:
        - podSelector: {}
  egress:
    - to:
        - podSelector: {}

---
# 4. Allow egress to RDS (specific CIDR of RDS subnet)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-egress-rds
  namespace: tenant-${TENANT_ID}
spec:
  podSelector: {}
  egress:
    - to:
        - ipBlock:
            cidr: 10.0.4.0/24   # RDS subnet CIDR
      ports:
        - protocol: TCP
          port: 5432

---
# 5. Allow egress to AWS services (S3, Secrets Manager) via VPC endpoints
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-egress-aws-endpoints
  namespace: tenant-${TENANT_ID}
spec:
  podSelector: {}
  egress:
    - to:
        - ipBlock:
            cidr: 10.0.5.0/24   # VPC endpoint subnet
      ports:
        - protocol: TCP
          port: 443

---
# 6. Allow ingress from ALB only
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress-alb
  namespace: tenant-${TENANT_ID}
spec:
  podSelector:
    matchLabels:
      component: webserver
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: TCP
          port: 8080
```

#### Control 3: ResourceQuota + LimitRange

```yaml
# ResourceQuota — enforced per plan tier
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-quota
  namespace: tenant-${TENANT_ID}
spec:
  hard:
    # Compute
    requests.cpu: "4"
    requests.memory: 8Gi
    limits.cpu: "8"
    limits.memory: 16Gi
    # Objects
    count/pods: "20"
    count/services: "10"
    count/persistentvolumeclaims: "5"
    count/secrets: "20"
    count/configmaps: "10"

---
# LimitRange — default limits for pods that don't specify (prevents unbounded containers)
apiVersion: v1
kind: LimitRange
metadata:
  name: tenant-limits
  namespace: tenant-${TENANT_ID}
spec:
  limits:
    - type: Container
      default:
        cpu: "500m"
        memory: "512Mi"
      defaultRequest:
        cpu: "100m"
        memory: "256Mi"
      max:
        cpu: "4"
        memory: "8Gi"
```

**Plan tier quota mapping:**

| Plan | CPU Request | Memory Request | Max Pods | Max Workers |
|---|---|---|---|---|
| Developer | 2 cores | 4 Gi | 10 | 1 |
| Starter | 4 cores | 8 Gi | 20 | 3 |
| Production | 8 cores | 16 Gi | 40 | 8 |
| Enterprise | 16 cores | 32 Gi | 80 | 20 |

#### Control 4: RBAC + ServiceAccount + IRSA

Each tenant namespace has exactly one `ServiceAccount`. That SA is annotated with an IAM Role ARN that grants **only** permissions to that tenant's resources.

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: airflow-sa
  namespace: tenant-${TENANT_ID}
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT_ID:role/airflow-tenant-${TENANT_ID}
```

The IAM role policy (created at provisioning time via Terraform/CDK):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::platform-tenant-${TENANT_ID}-dags",
        "arn:aws:s3:::platform-tenant-${TENANT_ID}-dags/*",
        "arn:aws:s3:::platform-tenant-${TENANT_ID}-logs/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:platform/tenants/${TENANT_ID}/*"
    },
    {
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:REGION:ACCOUNT:key/${TENANT_KMS_KEY_ID}"
    }
  ]
}
```

#### Control 5: Dedicated RDS Instance

Each tenant gets their own Postgres RDS instance. **We never share a metadata DB.** Airflow stores connections, variables, DAG state, task instances, and XComs in this DB — sharing it would be a catastrophic security boundary violation.

```
tenant-abc123  →  airflow-tenant-abc123.cluster-xxxx.rds.amazonaws.com:5432/airflow
tenant-xyz789  →  airflow-tenant-xyz789.cluster-yyyy.rds.amazonaws.com:5432/airflow
```

See [Section 14](#14-database-strategy) for full RDS configuration.

#### Control 6: Pod Security Standards

Enforced via Kubernetes admission controller (Pod Security Admission or OPA Gatekeeper):

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: tenant-${TENANT_ID}
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: latest
```

This enforces:
- `runAsNonRoot: true`
- `readOnlyRootFilesystem: true`
- `allowPrivilegeEscalation: false`
- `seccompProfile.type: RuntimeDefault`
- All capabilities dropped (`drop: ["ALL"]`)

---

## 4. AWS Infrastructure Specification

### EKS Cluster Configuration

```hcl
# terraform/modules/eks/main.tf

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = "airflow-platform-prod"
  cluster_version = "1.29"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Cluster endpoint — private only (no public API access)
  cluster_endpoint_public_access  = true   # true only for operator access; lock to VPN CIDR
  cluster_endpoint_private_access = true
  cluster_endpoint_public_access_cidrs = ["YOUR_VPN_CIDR/32"]

  # Enable IRSA (required for per-tenant ServiceAccount IAM roles)
  enable_irsa = true

  # Managed node groups for system workloads (control plane, monitoring)
  eks_managed_node_groups = {
    system = {
      name           = "system"
      instance_types = ["m5.large"]
      min_size       = 2
      max_size       = 4
      desired_size   = 2
      labels = {
        role = "system"
      }
      taints = [{
        key    = "CriticalAddonsOnly"
        value  = "true"
        effect = "NO_SCHEDULE"
      }]
    }
  }

  # Karpenter handles tenant workload nodes (see below)
  cluster_addons = {
    coredns                = { most_recent = true }
    kube-proxy             = { most_recent = true }
    vpc-cni                = { most_recent = true }
    aws-ebs-csi-driver     = { most_recent = true }
    aws-efs-csi-driver     = { most_recent = true }
  }
}
```

### Karpenter Configuration

Karpenter replaces Cluster Autoscaler. It provisions EC2 nodes within seconds (vs. minutes) and consolidates underutilized nodes automatically.

```yaml
# karpenter/nodepool-tenant-workloads.yaml
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: tenant-workloads
spec:
  template:
    metadata:
      labels:
        role: tenant-workload
    spec:
      nodeClassRef:
        apiVersion: karpenter.k8s.aws/v1beta1
        kind: EC2NodeClass
        name: tenant-workloads
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["amd64"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["spot", "on-demand"]   # prefer spot, fall back to on-demand
        - key: karpenter.k8s.aws/instance-category
          operator: In
          values: ["c", "m", "r"]
        - key: karpenter.k8s.aws/instance-generation
          operator: Gt
          values: ["2"]
  limits:
    cpu: 1000            # cluster-wide ceiling
    memory: 2000Gi
  disruption:
    consolidationPolicy: WhenUnderutilized
    consolidateAfter: 30s

---
apiVersion: karpenter.k8s.aws/v1beta1
kind: EC2NodeClass
metadata:
  name: tenant-workloads
spec:
  amiFamily: AL2
  role: KarpenterNodeRole-airflow-platform-prod
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: airflow-platform-prod
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: airflow-platform-prod
  blockDeviceMappings:
    - deviceName: /dev/xvda
      ebs:
        volumeSize: 100Gi
        volumeType: gp3
        iops: 3000
        throughput: 125
        encrypted: true
```

### VPC Design

```
VPC: 10.0.0.0/16

Private subnets (EKS nodes, RDS):
  10.0.1.0/24  — AZ a
  10.0.2.0/24  — AZ b
  10.0.3.0/24  — AZ c

Public subnets (ALB, NAT Gateway):
  10.0.101.0/24 — AZ a
  10.0.102.0/24 — AZ b
  10.0.103.0/24 — AZ c

Database subnets (RDS — no route to internet):
  10.0.201.0/24 — AZ a
  10.0.202.0/24 — AZ b
  10.0.203.0/24 — AZ c

VPC Endpoints (avoid NAT Gateway costs for AWS API calls):
  - com.amazonaws.REGION.s3           (Gateway endpoint — free)
  - com.amazonaws.REGION.ecr.api      (Interface endpoint)
  - com.amazonaws.REGION.ecr.dkr      (Interface endpoint)
  - com.amazonaws.REGION.secretsmanager (Interface endpoint)
  - com.amazonaws.REGION.sts          (Interface endpoint — for IRSA)
```

### AWS Services — Complete Specification

#### Amazon RDS

```hcl
resource "aws_db_instance" "tenant" {
  identifier        = "airflow-tenant-${var.tenant_id}"
  engine            = "postgres"
  engine_version    = "15.4"
  instance_class    = var.db_instance_class   # db.t3.small (starter), db.t3.medium (prod)
  allocated_storage = 20
  max_allocated_storage = 100   # auto-scaling up to 100GB

  db_name  = "airflow"
  username = "airflow_admin"
  password = random_password.db_password.result

  vpc_security_group_ids = [aws_security_group.rds_tenant.id]
  db_subnet_group_name   = aws_db_subnet_group.platform.name

  multi_az               = var.plan == "production" || var.plan == "enterprise"
  storage_encrypted      = true
  kms_key_id            = aws_kms_key.tenant.arn

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:04:00-sun:05:00"

  deletion_protection = true
  skip_final_snapshot = false
  final_snapshot_identifier = "airflow-tenant-${var.tenant_id}-final"

  performance_insights_enabled = true

  tags = {
    TenantId = var.tenant_id
    Platform = "airflow-managed"
    Plan     = var.plan
  }
}
```

#### Amazon ECR (per-tenant)

```hcl
resource "aws_ecr_repository" "tenant" {
  name                 = "airflow-platform/tenant-${var.tenant_id}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.tenant.arn
  }
}

resource "aws_ecr_lifecycle_policy" "tenant" {
  repository = aws_ecr_repository.tenant.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
```

---

## 5. Kubernetes & Helm Strategy

### Official Airflow Helm Chart

We use the [official Apache Airflow Helm chart](https://github.com/apache/airflow/tree/main/chart) (`apache-airflow/airflow`, version `1.13.x+`).

**Do not use the Astronomer Helm chart** (it requires an Astronomer license). The official chart is fully featured and production-ready.

### Helm Values Template per Tenant

All values are templated and stored in Git at `gitops/tenants/${TENANT_ID}/values.yaml`. The Deployment Controller renders the template and stores the result; ArgoCD syncs it.

```yaml
# gitops/tenants/template/values.yaml.tpl
# Rendered per-tenant at provisioning time

airflowVersion: "{{ .AirflowVersion }}"          # e.g. "2.9.3"
defaultAirflowRepository: "{{ .ECRRepo }}"
defaultAirflowTag: "latest"

# Executor — KubernetesExecutor for all plans (simpler ops, better isolation)
executor: "KubernetesExecutor"

# Webserver
webserver:
  replicas: 1
  resources:
    requests:
      cpu: "{{ .WebserverCPURequest }}"
      memory: "{{ .WebserverMemoryRequest }}"
    limits:
      cpu: "{{ .WebserverCPULimit }}"
      memory: "{{ .WebserverMemoryLimit }}"
  service:
    type: ClusterIP
  defaultUser:
    enabled: true
    role: Admin
    username: admin
    password: "{{ .DefaultAdminPassword }}"   # rotated on first login
    email: "{{ .TenantAdminEmail }}"

# Scheduler
scheduler:
  replicas: 1
  resources:
    requests:
      cpu: "{{ .SchedulerCPURequest }}"
      memory: "{{ .SchedulerMemoryRequest }}"
    limits:
      cpu: "{{ .SchedulerCPULimit }}"
      memory: "{{ .SchedulerMemoryLimit }}"

# Triggerer (for Deferrable Operators)
triggerer:
  enabled: true
  replicas: 1
  resources:
    requests:
      cpu: "500m"
      memory: "512Mi"

# Workers — KubernetesExecutor launches pods directly (no persistent workers)
workers:
  replicas: 0     # KubernetesExecutor: workers are launched per-task

# Metadata DB
data:
  metadataSecretName: airflow-metadata-secret    # synced from Secrets Manager
  brokerUrlSecretName: ""                         # not needed for KubernetesExecutor

# Fernet key (encryption at rest for connections/variables)
fernetKeySecretName: airflow-fernet-key

# DAG delivery — git-sync sidecar
dags:
  persistence:
    enabled: false
  gitSync:
    enabled: true
    repo: "{{ .GitRepo }}"
    branch: "{{ .GitBranch }}"
    rev: HEAD
    depth: 1
    maxFailures: 3
    subPath: "dags"
    sshKeySecret: airflow-git-ssh-key     # if private repo
    period: 60s                            # sync every 60 seconds

# Logs — ship to S3
logs:
  persistence:
    enabled: false    # don't use PVC; ship to S3
  # S3 remote logging configured via airflow.cfg overrides below

# Airflow config overrides
config:
  core:
    load_examples: "False"
    parallelism: "{{ .MaxParallelTasks }}"
    max_active_runs_per_dag: "16"
    dag_discovery_safe_mode: "True"
  webserver:
    expose_config: "False"
    instance_name: "{{ .EnvironmentName }}"
  logging:
    remote_logging: "True"
    remote_base_log_folder: "s3://platform-tenant-{{ .TenantID }}-logs/airflow-logs"
    remote_log_conn_id: "aws_default"
    encrypt_s3_logs: "True"
  kubernetes_executor:
    namespace: "tenant-{{ .TenantID }}"
    worker_container_repository: "{{ .ECRRepo }}"
    worker_container_tag: "latest"
    delete_worker_pods: "True"
    delete_worker_pods_on_failure: "False"   # keep failed pods for debugging
    worker_pods_creation_batch_size: "10"
    worker_service_account_name: "airflow-sa"
  metrics:
    statsd_on: "True"
    statsd_host: "statsd-exporter"
    statsd_port: "9125"
    statsd_prefix: "airflow"

# ServiceAccount — uses IRSA
serviceAccount:
  create: false
  name: airflow-sa    # pre-created at namespace provisioning

# Pod security context
securityContext:
  runAsUser: 50000
  runAsGroup: 50000
  fsGroup: 50000
  runAsNonRoot: true

# Container security context
containerSecurityContext:
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault

# Extra volumes (for read-only root filesystem)
extraVolumes:
  - name: tmp
    emptyDir: {}
  - name: airflow-home
    emptyDir: {}

extraVolumeMounts:
  - name: tmp
    mountPath: /tmp
  - name: airflow-home
    mountPath: /home/airflow

# StatsD exporter for Prometheus scraping
statsd:
  enabled: true
  resources:
    requests:
      cpu: "50m"
      memory: "64Mi"
```

### GitOps Directory Structure

```
gitops/
├── platform/                        # Shared platform components
│   ├── argocd/
│   ├── prometheus/
│   ├── grafana/
│   └── karpenter/
└── tenants/
    ├── _template/
    │   └── values.yaml.tpl          # Template rendered at provisioning
    ├── tenant-abc123/
    │   ├── values.yaml              # Rendered Helm values (never edit manually)
    │   ├── airflow-deployment.yaml  # AirflowDeployment CRD
    │   └── kustomization.yaml
    └── tenant-xyz789/
        ├── values.yaml
        ├── airflow-deployment.yaml
        └── kustomization.yaml
```

---

## 6. Provisioning Engine

### Custom Resource Definition: AirflowDeployment

The Deployment Controller watches this CRD. Creating an `AirflowDeployment` triggers the full provisioning workflow.

```yaml
apiVersion: platform.airflow.io/v1alpha1
kind: AirflowDeployment
metadata:
  name: tenant-abc123
  namespace: platform-system
spec:
  tenantId: abc123
  environmentName: "Production Pipeline"
  plan: production
  airflowVersion: "2.9.3"
  executorType: KubernetesExecutor

  git:
    repo: "git@github.com:customer-org/airflow-dags.git"
    branch: main
    syncIntervalSeconds: 60

  resources:
    scheduler:
      cpu: "2"
      memory: "4Gi"
    webserver:
      cpu: "1"
      memory: "2Gi"

  scaling:
    maxWorkers: 8
    workerCPU: "2"
    workerMemory: "4Gi"

  hibernation:
    enabled: false
    schedule: ""   # cron schedule e.g. "0 22 * * *" to hibernate at 10pm

  notifications:
    slackWebhookSecret: "platform/tenants/abc123/slack-webhook"
    emailOnFailure: ["admin@customer.com"]

status:
  phase: Running           # Provisioning | Running | Hibernating | Deleting | Failed
  airflowUrl: "https://abc123.platform.com"
  schedulerHeartbeat: "2026-03-21T10:00:00Z"
  workerCount: 2
  conditions:
    - type: DatabaseReady
      status: "True"
    - type: HelmDeploymentReady
      status: "True"
    - type: IngressReady
      status: "True"
```

### Provisioning State Machine

```
                    ┌─────────────┐
                    │  REQUESTED  │  (API call received, validation passed)
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ PROVISIONING│  (~2 min total)
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
         [AWS Setup]   [K8s Setup]  [Helm Deploy]
         RDS create    Namespace    airflow chart
         ECR repo      Quota        git-sync
         S3 buckets    NetPolicy    ingress
         Secrets       ServiceAcct  TLS cert
         KMS key       RBAC         Grafana org
              │            │            │
              └────────────┼────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   RUNNING   │◄──────────────┐
                    └──────┬──────┘               │
                           │                      │
              ┌────────────┼────────────┐         │
              │            │            │         │
              ▼            ▼            ▼         │
          [UPDATE]    [HIBERNATE]    [DELETE]      │
          Helm        Scale to       Helm          │
          upgrade     zero           uninstall     │
          values      workers        Drop RDS      │
                      Suspend        Delete NS     │
                      scheduler      Remove S3     │
                           │         Remove ECR    │
                           │                      │
                           ▼                      │
                    ┌─────────────┐               │
                    │ HIBERNATING │               │
                    └──────┬──────┘               │
                           │  wake up             │
                           └──────────────────────┘
```

### Provisioning API Implementation

```python
# api/routers/environments.py

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, validator
from typing import Optional
import asyncio

router = APIRouter(prefix="/environments", tags=["environments"])

class CreateEnvironmentRequest(BaseModel):
    name: str
    plan: Literal["developer", "starter", "production", "enterprise"]
    airflow_version: str = "2.9.3"
    executor_type: Literal["KubernetesExecutor"] = "KubernetesExecutor"
    git_repo: Optional[str] = None
    git_branch: str = "main"
    region: str = "us-east-1"

    @validator("airflow_version")
    def validate_airflow_version(cls, v):
        supported = ["2.8.4", "2.9.3", "2.10.0"]
        if v not in supported:
            raise ValueError(f"Supported versions: {supported}")
        return v

@router.post("/", status_code=202)
async def create_environment(
    request: CreateEnvironmentRequest,
    background_tasks: BackgroundTasks,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 1. Check plan limits
    existing = await db.count(Environment, tenant_id=current_user.tenant_id)
    plan_limits = PLAN_LIMITS[request.plan]
    if existing >= plan_limits["max_environments"]:
        raise HTTPException(429, "Environment limit reached for your plan")

    # 2. Create DB record
    env = Environment(
        tenant_id=current_user.tenant_id,
        name=request.name,
        plan=request.plan,
        airflow_version=request.airflow_version,
        status="provisioning",
        created_by=current_user.id
    )
    db.add(env)
    await db.commit()

    # 3. Create billing record
    await billing_service.create_environment_meter(env.id, request.plan)

    # 4. Enqueue provisioning job (Celery or AWS SQS + Lambda)
    background_tasks.add_task(
        provision_environment,
        environment_id=env.id,
        config=request.dict()
    )

    return {"environment_id": env.id, "status": "provisioning"}

async def provision_environment(environment_id: str, config: dict):
    """
    Orchestrates the full provisioning sequence.
    Each step is idempotent — safe to retry on failure.
    """
    env = await get_environment(environment_id)
    tenant_id = env.tenant_id

    try:
        # Step 1: AWS resources
        await aws_provisioner.create_rds_instance(tenant_id, config["plan"])
        await aws_provisioner.create_ecr_repo(tenant_id)
        await aws_provisioner.create_s3_buckets(tenant_id)
        await aws_provisioner.create_kms_key(tenant_id)
        await aws_provisioner.create_iam_role(tenant_id)
        await aws_provisioner.store_secrets(tenant_id, config)

        # Step 2: Wait for RDS to be available (usually 2-5 min for new instance)
        await wait_for_rds_available(tenant_id, timeout_seconds=300)

        # Step 3: Kubernetes namespace + isolation controls
        await k8s_provisioner.create_namespace(tenant_id)
        await k8s_provisioner.apply_resource_quota(tenant_id, config["plan"])
        await k8s_provisioner.apply_network_policies(tenant_id)
        await k8s_provisioner.create_service_account(tenant_id)
        await k8s_provisioner.apply_pod_security(tenant_id)

        # Step 4: Sync secrets into namespace
        await k8s_provisioner.create_external_secrets(tenant_id)
        await wait_for_secrets_synced(tenant_id, timeout_seconds=60)

        # Step 5: Helm install
        helm_values = render_values_template(tenant_id, config)
        await helm_provisioner.install(
            release_name=f"airflow-{tenant_id}",
            namespace=f"tenant-{tenant_id}",
            values=helm_values
        )

        # Step 6: Wait for pods ready
        await wait_for_airflow_ready(tenant_id, timeout_seconds=300)

        # Step 7: DNS + TLS
        await dns_provisioner.create_record(tenant_id)
        await tls_provisioner.ensure_cert_ready(tenant_id, timeout_seconds=120)

        # Step 8: Observability enrollment
        await observability_provisioner.create_grafana_org(tenant_id)
        await observability_provisioner.create_alert_rules(tenant_id)

        # Step 9: Commit to GitOps repo
        await gitops.commit_tenant_config(tenant_id, helm_values)

        # Step 10: Update status + notify
        await update_environment_status(environment_id, "running",
            airflow_url=f"https://{tenant_id}.platform.com")
        await notify_environment_ready(env)

    except Exception as e:
        await update_environment_status(environment_id, "failed", error=str(e))
        await notify_environment_failed(env, error=str(e))
        raise
```

---

## 7. Control Plane Components

### Deployment Controller (Kubernetes Operator)

The controller is written in Go using `controller-runtime`. It is the **single source of truth** for all tenant environment lifecycle operations.

```go
// controller/airflowdeployment_controller.go

package controller

import (
    "context"
    "fmt"
    "time"

    platformv1alpha1 "github.com/yourorg/airflow-platform/api/v1alpha1"
    "helm.sh/helm/v3/pkg/action"
    corev1 "k8s.io/api/core/v1"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
)

type AirflowDeploymentReconciler struct {
    client.Client
    HelmClient    *HelmClient
    AWSClient     *AWSClient
    Log           logr.Logger
}

func (r *AirflowDeploymentReconciler) Reconcile(
    ctx context.Context,
    req ctrl.Request,
) (ctrl.Result, error) {
    log := r.Log.WithValues("airflowdeployment", req.NamespacedName)

    // Fetch the AirflowDeployment
    deployment := &platformv1alpha1.AirflowDeployment{}
    if err := r.Get(ctx, req.NamespacedName, deployment); err != nil {
        return ctrl.Result{}, client.IgnoreNotFound(err)
    }

    // Add finalizer for cleanup on deletion
    if !controllerutil.ContainsFinalizer(deployment, "platform.airflow.io/finalizer") {
        controllerutil.AddFinalizer(deployment, "platform.airflow.io/finalizer")
        return ctrl.Result{}, r.Update(ctx, deployment)
    }

    // Handle deletion
    if !deployment.DeletionTimestamp.IsZero() {
        return r.reconcileDelete(ctx, deployment)
    }

    // Reconcile based on current phase
    switch deployment.Status.Phase {
    case "", "Provisioning":
        return r.reconcileProvisioning(ctx, deployment)
    case "Running":
        return r.reconcileRunning(ctx, deployment)
    case "Hibernating":
        return r.reconcileHibernating(ctx, deployment)
    case "Failed":
        return r.reconcileFailed(ctx, deployment)
    }

    return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
}

func (r *AirflowDeploymentReconciler) reconcileRunning(
    ctx context.Context,
    deployment *platformv1alpha1.AirflowDeployment,
) (ctrl.Result, error) {
    // Check if Helm values have drifted from desired state
    currentValues, err := r.HelmClient.GetValues(deployment.Spec.TenantID)
    if err != nil {
        return ctrl.Result{}, err
    }

    desiredValues := r.renderHelmValues(deployment.Spec)

    if !reflect.DeepEqual(currentValues, desiredValues) {
        // Values have changed — apply Helm upgrade
        if err := r.HelmClient.Upgrade(deployment.Spec.TenantID, desiredValues); err != nil {
            return ctrl.Result{}, err
        }
    }

    // Check hibernation schedule
    if deployment.Spec.Hibernation.Enabled {
        if r.shouldHibernate(deployment.Spec.Hibernation.Schedule) {
            return r.beginHibernation(ctx, deployment)
        }
    }

    // Update status with live metrics
    r.updateStatusFromCluster(ctx, deployment)

    return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
}
```

### Web Dashboard API Contract

Key endpoints the frontend consumes:

```yaml
# OpenAPI 3.0 excerpt

paths:
  /environments:
    post:
      summary: Create a new Airflow environment
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateEnvironmentRequest'
      responses:
        202:
          description: Provisioning started
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/EnvironmentSummary'

  /environments/{environmentId}:
    get:
      summary: Get environment details including live status
      responses:
        200:
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/EnvironmentDetail'

  /environments/{environmentId}/status:
    get:
      summary: Lightweight status poll (used by UI during provisioning)
      responses:
        200:
          content:
            application/json:
              example:
                phase: "Provisioning"
                steps_completed: 6
                steps_total: 12
                current_step: "Waiting for pods to be ready"
                airflow_url: null
                estimated_seconds_remaining: 45

  /environments/{environmentId}/hibernate:
    post:
      summary: Hibernate environment (scale to zero, keep namespace + DB)

  /environments/{environmentId}/wake:
    post:
      summary: Wake from hibernation

  /environments/{environmentId}/upgrade:
    post:
      summary: Upgrade Airflow version
      requestBody:
        content:
          application/json:
            schema:
              properties:
                target_version:
                  type: string
                  example: "2.10.0"

  /environments/{environmentId}/connections:
    get:
      summary: List Airflow connections (metadata only, no passwords)
    post:
      summary: Create or update a connection

  /environments/{environmentId}/variables:
    get:
      summary: List Airflow variables
    post:
      summary: Set a variable
```

---

## 8. Airflow Component Configuration

### KubernetesExecutor vs CeleryExecutor Decision

We use **KubernetesExecutor** exclusively (at least through Phase 2):

| Factor | KubernetesExecutor | CeleryExecutor |
|---|---|---|
| Worker isolation | Each task = separate pod | Shared worker pool |
| Resource efficiency | Pay per task | Workers idle-charging |
| Complexity | Lower (no Redis/RabbitMQ) | Higher (broker + result backend) |
| Task startup latency | ~15-30s (pod schedule) | ~2-5s |
| Burst scaling | Excellent (Karpenter) | Limited by worker count |
| Good for | Batch, heavy tasks | High-frequency, low-latency |

For Phase 3 we will offer Celery as an option for Enterprise customers with very high task frequency.

### Airflow Scheduler Tuning

```cfg
# airflow.cfg overrides passed via Helm values -> config section

[scheduler]
# How often scheduler loops (lower = more responsive, higher = less CPU)
scheduler_heartbeat_sec = 5

# Number of scheduler processes (1 is usually fine; increase for >500 DAGs)
num_runs = -1

# DAG file processor (how often it scans the DAG folder)
dag_dir_list_interval = 30
min_file_process_interval = 30

# Parsing parallelism
parsing_processes = 2    # increase for large DAG count

# Max DAG runs created per scheduler loop
max_dagruns_to_create_per_loop = 10

[core]
# Total concurrent task slots across all DAGs
parallelism = 32   # Production plan

# Max active DAG runs per DAG (across all instances)
max_active_runs_per_dag = 16

# Catch-up: important for backfilling behaviour
catchup_by_default = False
```

### KEDA ScaledObject for Worker Auto-scaling

Although KubernetesExecutor doesn't use persistent workers, you may want KEDA for Celery-based deployments in Phase 3. For now, KEDA is used for scaling the **Triggerer** based on deferred task count:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: airflow-triggerer-scaler
  namespace: tenant-${TENANT_ID}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: airflow-triggerer
  minReplicaCount: 1
  maxReplicaCount: 3
  triggers:
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring:9090
        metricName: airflow_triggerer_deferred_tasks
        query: |
          sum(airflow_triggerer_deferred_tasks{namespace="tenant-${TENANT_ID}"})
        threshold: "100"   # scale up when >100 deferred tasks per triggerer
```

---

## 9. Networking & Ingress

### ALB Ingress Controller

We use the AWS Load Balancer Controller to provision an ALB per Ingress resource (or share one ALB across tenants using rules).

**Recommended: Single ALB with host-based routing** (cost-efficient):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: airflow-ingress
  namespace: tenant-${TENANT_ID}
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/ssl-redirect: "443"
    # Shared ALB across all tenants via IngressGroup
    alb.ingress.kubernetes.io/group.name: airflow-platform
    alb.ingress.kubernetes.io/group.order: "100"
    # ACM cert (wildcard *.platform.com)
    alb.ingress.kubernetes.io/certificate-arn: arn:aws:acm:REGION:ACCOUNT:certificate/CERT_ID
    # WAF (optional but recommended for public-facing Airflow)
    alb.ingress.kubernetes.io/wafv2-acl-arn: arn:aws:wafv2:REGION:ACCOUNT:regional/webacl/WEBACL_ID
spec:
  rules:
    - host: "${TENANT_ID}.platform.com"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: airflow-webserver-svc
                port:
                  number: 8080
```

### ExternalDNS

ExternalDNS automatically creates Route 53 records when Ingresses are created:

```yaml
# ExternalDNS installed in kube-system namespace
# Configured to watch all namespaces with annotation:
# external-dns.alpha.kubernetes.io/hostname: ${TENANT_ID}.platform.com
```

---

## 10. Secrets & Configuration Management

### ExternalSecrets Operator

We use the [External Secrets Operator](https://external-secrets.io/) to sync secrets from AWS Secrets Manager into Kubernetes Secrets automatically.

```yaml
# Secret layout in AWS Secrets Manager:
# platform/tenants/{tenantId}/metadata-db      → DB connection string
# platform/tenants/{tenantId}/fernet-key       → Airflow fernet key
# platform/tenants/{tenantId}/webserver-secret → Flask secret key
# platform/tenants/{tenantId}/git-ssh-key      → Private key for DAG repo

---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: airflow-metadata-secret
  namespace: tenant-${TENANT_ID}
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: airflow-metadata-secret
    creationPolicy: Owner
    template:
      type: Opaque
      data:
        connection: "postgresql://airflow_admin:{{ .password }}@{{ .host }}:5432/airflow"
  data:
    - secretKey: password
      remoteRef:
        key: platform/tenants/${TENANT_ID}/metadata-db
        property: password
    - secretKey: host
      remoteRef:
        key: platform/tenants/${TENANT_ID}/metadata-db
        property: host

---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: airflow-fernet-key
  namespace: tenant-${TENANT_ID}
spec:
  refreshInterval: 24h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: airflow-fernet-key
  data:
    - secretKey: fernet-key
      remoteRef:
        key: platform/tenants/${TENANT_ID}/fernet-key
```

### Fernet Key Rotation

Airflow's fernet key encrypts stored connections and variables. Rotation procedure:

```python
# scripts/rotate_fernet_key.py
# Run this BEFORE updating the secret in Secrets Manager

from cryptography.fernet import Fernet
import subprocess

# Generate new key
new_key = Fernet.generate_key().decode()

# Get old key from Secrets Manager
old_key = get_secret(f"platform/tenants/{tenant_id}/fernet-key")["fernet-key"]

# Set AIRFLOW__CORE__FERNET_KEY to "new_key,old_key" (Airflow supports key rotation)
# Airflow will decrypt with old_key and re-encrypt with new_key on next access
update_secret(f"platform/tenants/{tenant_id}/fernet-key", {
    "fernet-key": f"{new_key},{old_key}"
})

# After confirming all connections/variables are migrated (check logs), remove old key
# update_secret(..., {"fernet-key": new_key})
```

---

## 11. Observability Stack

### Prometheus Configuration

Prometheus is deployed once in the `monitoring` namespace and scrapes all tenant namespaces via `PodMonitor` and `ServiceMonitor` resources.

```yaml
# monitoring/prometheus/prometheus.yaml
apiVersion: monitoring.coreos.com/v1
kind: Prometheus
metadata:
  name: platform-prometheus
  namespace: monitoring
spec:
  replicas: 2
  retention: 30d
  retentionSize: 50GB

  # RBAC to scrape all namespaces
  serviceAccountName: prometheus
  podMonitorNamespaceSelector: {}    # all namespaces
  serviceMonitorNamespaceSelector: {}

  storage:
    volumeClaimTemplate:
      spec:
        storageClassName: gp3
        resources:
          requests:
            storage: 100Gi

---
# ServiceMonitor for Airflow StatsD exporter (deployed per tenant namespace)
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: airflow-statsd
  namespace: tenant-${TENANT_ID}
  labels:
    prometheus: platform    # matches Prometheus selector
spec:
  selector:
    matchLabels:
      component: statsd
  endpoints:
    - port: statsd-scrape
      interval: 30s
      relabelings:
        - sourceLabels: [__meta_kubernetes_namespace]
          targetLabel: tenant_id
          regex: tenant-(.+)
          replacement: $1
```

### Key Airflow Metrics to Alert On

```yaml
# monitoring/alertmanager/rules/airflow.yaml
groups:
  - name: airflow.rules
    rules:
      # Scheduler health
      - alert: AirflowSchedulerUnhealthy
        expr: |
          (time() - airflow_scheduler_heartbeat{}) > 30
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Scheduler not heartbeating for tenant {{ $labels.tenant_id }}"

      # Task failures
      - alert: AirflowHighTaskFailureRate
        expr: |
          rate(airflow_ti_failures_total{}[15m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High task failure rate for tenant {{ $labels.tenant_id }}"

      # DAG processing time
      - alert: AirflowSlowDAGParsing
        expr: |
          airflow_dag_processing_total_parse_time{} > 30
        for: 5m
        labels:
          severity: warning

      # Queue depth (tasks waiting to run)
      - alert: AirflowTaskQueueBacklog
        expr: |
          airflow_executor_queued_tasks{} > 50
        for: 10m
        labels:
          severity: warning
```

### Grafana Multi-Tenancy

Each tenant gets their own Grafana Organization. The provisioning flow calls the Grafana API:

```python
# provisioning/observability.py

async def create_grafana_org(tenant_id: str, tenant_name: str):
    """Create isolated Grafana organization for tenant."""
    grafana = GrafanaClient(GRAFANA_URL, GRAFANA_ADMIN_TOKEN)

    # Create org
    org = await grafana.create_org({"name": f"tenant-{tenant_id}"})

    # Add Prometheus datasource scoped to this tenant
    await grafana.add_datasource(org_id=org["orgId"], datasource={
        "name": "Prometheus",
        "type": "prometheus",
        "url": "http://prometheus.monitoring:9090",
        "jsonData": {
            "httpMethod": "POST",
            # Label filter scopes all queries to this tenant's metrics
            "exemplarTraceIdDestinations": [],
            "prometheusQueryOverlapWindow": "10m",
        },
        "secureJsonData": {},
        # Grafana 10+: use label-based data scoping
        "readOnly": False
    })

    # Import pre-built Airflow dashboard template
    await grafana.import_dashboard(org_id=org["orgId"],
        dashboard=AIRFLOW_DASHBOARD_TEMPLATE,
        overwrite=True,
        variables={"tenant_id": tenant_id}
    )
```

### Log Pipeline

```
Airflow pods
    │ stdout/stderr
    ▼
Fluent Bit DaemonSet (one per node)
    │ tail /var/log/containers/airflow-*.log
    │ parse JSON structured logs
    │ add tenant_id label from namespace
    ▼
CloudWatch Logs
    /airflow-platform/tenant-{tenantId}/scheduler
    /airflow-platform/tenant-{tenantId}/webserver
    /airflow-platform/tenant-{tenantId}/worker
    │
    │ Kinesis Firehose (async)
    ▼
S3: s3://platform-logs/airflow/{tenantId}/year=YYYY/month=MM/day=DD/
    │
    ▼
Athena (ad-hoc querying for support/debugging)
    CREATE EXTERNAL TABLE airflow_logs ...
    PARTITIONED BY (tenant_id, year, month, day)
```

---

## 12. Security Architecture

### Threat Model

| Threat | Mitigation |
|---|---|
| Tenant A reads Tenant B's DAG data | NetworkPolicy deny-all between namespaces; separate RDS instances |
| Tenant A exhausts cluster resources | ResourceQuota + LimitRange per namespace; Karpenter per-tenant node labels |
| Compromised worker pod escapes namespace | Pod Security Standards (restricted); Seccomp; no privileged containers |
| Leaked DB credentials | Secrets Manager + IRSA; never in environment variables directly; ExternalSecrets rotation |
| Airflow connection passwords exposed | Fernet key encryption; API returns masked values; audit logging on access |
| Malicious DAG executes arbitrary AWS calls | IRSA scopes IAM permissions to tenant's resources only; deny all other actions |
| Privilege escalation via K8s API | Workers cannot access K8s API (no default token mounting); RBAC deny |

### OPA/Gatekeeper Policies

```rego
# policies/require-resource-limits.rego
# All pods must specify resource limits

package kubernetes.admission

deny[msg] {
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    not container.resources.limits.cpu
    msg := sprintf("Container '%v' must specify CPU limits", [container.name])
}

deny[msg] {
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    not container.resources.limits.memory
    msg := sprintf("Container '%v' must specify memory limits", [container.name])
}
```

```rego
# policies/require-non-root.rego
package kubernetes.admission

deny[msg] {
    input.request.kind.kind == "Pod"
    container := input.request.object.spec.containers[_]
    not container.securityContext.runAsNonRoot
    msg := sprintf("Container '%v' must set runAsNonRoot: true", [container.name])
}
```

### Audit Logging

```python
# All provisioning API calls are audit-logged to CloudWatch Logs Insights

{
  "timestamp": "2026-03-21T10:00:00Z",
  "event_type": "environment.created",
  "actor_id": "user-abc123",
  "actor_email": "admin@customer.com",
  "tenant_id": "tenant-xyz789",
  "resource_type": "AirflowEnvironment",
  "resource_id": "env-00123",
  "ip_address": "203.0.113.42",
  "user_agent": "Mozilla/5.0...",
  "request_id": "req-7f8a2b1c",
  "outcome": "success",
  "details": {
    "plan": "production",
    "airflow_version": "2.9.3"
  }
}
```

---

## 13. GitOps & CI/CD Pipeline

### ArgoCD Application per Tenant

```yaml
# gitops/tenants/tenant-abc123/argocd-application.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: airflow-tenant-abc123
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: tenant-workloads

  source:
    repoURL: https://github.com/yourorg/airflow-platform-gitops
    targetRevision: main
    path: tenants/tenant-abc123
    helm:
      valueFiles:
        - values.yaml
      # Chart source: official Apache Airflow chart
      chart: airflow
      repoURL: https://airflow.apache.org
      targetRevision: "1.13.1"

  destination:
    server: https://kubernetes.default.svc
    namespace: tenant-abc123

  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false    # we create namespace separately
      - PrunePropagationPolicy=foreground
      - RespectIgnoreDifferences=true
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m

  ignoreDifferences:
    # Ignore auto-scaled replica counts
    - group: apps
      kind: Deployment
      jsonPointers:
        - /spec/replicas
```

### Platform Upgrade Process (Zero-Downtime Airflow Version Bumps)

```python
# scripts/rolling_upgrade.py

async def rolling_upgrade(target_airflow_version: str, target_chart_version: str):
    """
    Canary-style rollout: upgrade 10% → 50% → 100% of tenants.
    Automatic rollback if error rate spikes.
    """
    tenants = await get_all_running_tenants()

    # Wave 1: 10% (internal + opted-in beta tenants)
    wave1 = [t for t in tenants if t.beta_opt_in][:max(1, len(tenants)//10)]
    await upgrade_wave(wave1, target_airflow_version, target_chart_version)
    await monitor_for_errors(duration_minutes=30, error_threshold=0.05)

    # Wave 2: 50%
    wave2 = tenants[:len(tenants)//2]
    await upgrade_wave(wave2, target_airflow_version, target_chart_version)
    await monitor_for_errors(duration_minutes=60, error_threshold=0.02)

    # Wave 3: remaining
    remaining = [t for t in tenants if t not in wave1 + wave2]
    await upgrade_wave(remaining, target_airflow_version, target_chart_version)

async def upgrade_wave(tenants, airflow_version, chart_version):
    for tenant in tenants:
        # Update GitOps values file
        values = load_tenant_values(tenant.id)
        values["airflowVersion"] = airflow_version
        commit_tenant_values(tenant.id, values, f"Upgrade to Airflow {airflow_version}")
        # ArgoCD auto-syncs within 3 minutes
        # Helm rolling update applies: new scheduler → verify → new webserver → verify
```

---

## 14. Database Strategy

### RDS Configuration by Plan

```hcl
locals {
  db_config = {
    developer = {
      instance_class       = "db.t3.micro"
      allocated_storage    = 20
      multi_az             = false
      backup_retention     = 1
      performance_insights = false
    }
    starter = {
      instance_class       = "db.t3.small"
      allocated_storage    = 20
      multi_az             = false
      backup_retention     = 7
      performance_insights = false
    }
    production = {
      instance_class       = "db.t3.medium"
      allocated_storage    = 50
      multi_az             = true
      backup_retention     = 14
      performance_insights = true
    }
    enterprise = {
      instance_class       = "db.r6g.large"
      allocated_storage    = 100
      multi_az             = true
      backup_retention     = 30
      performance_insights = true
    }
  }
}
```

### RDS Proxy (Production + Enterprise Plans)

RDS Proxy reduces connection overhead and provides connection pooling — critical for KubernetesExecutor where each worker pod opens its own DB connection.

```hcl
resource "aws_db_proxy" "tenant" {
  count = var.plan == "production" || var.plan == "enterprise" ? 1 : 0

  name                   = "airflow-proxy-${var.tenant_id}"
  debug_logging          = false
  engine_family          = "POSTGRESQL"
  idle_client_timeout    = 1800
  require_tls            = true
  role_arn               = aws_iam_role.rds_proxy.arn
  vpc_security_group_ids = [aws_security_group.rds_proxy.id]
  vpc_subnet_ids         = var.database_subnet_ids

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "REQUIRED"
    secret_arn  = aws_secretsmanager_secret.tenant_db.arn
  }

  target_group {
    connection_pool_config {
      connection_borrow_timeout    = 120
      max_connections_percent      = 100
      max_idle_connections_percent = 50
    }
  }
}
```

### DB Maintenance & Airflow Schema

Airflow runs `airflow db migrate` on startup. In a managed service, we need to control this:

```yaml
# Helm values: use initContainers for DB migration
# This runs BEFORE scheduler/webserver start
airflow:
  createUserJob:
    useHelmHooks: false
  migrateDatabaseJob:
    useHelmHooks: false
    jobAnnotations:
      "helm.sh/hook": post-install,post-upgrade
      "helm.sh/hook-weight": "-1"
```

---

## 15. Scaling & Cost Optimization

### Node Bin-Packing Strategy

With Karpenter, we want to pack tenant workloads efficiently. System workloads (Prometheus, ArgoCD, control plane) run on dedicated `system` node group with taints.

Tenant workloads get scheduled on on-demand or spot nodes provisioned by Karpenter:

```yaml
# Node selector on all tenant Airflow pods (added via Helm extraPodLabels)
nodeSelector:
  role: tenant-workload

tolerations:
  - key: "tenant-workload"
    operator: "Exists"
    effect: "NoSchedule"
```

### Spot Instance Strategy for Workers

```yaml
# Karpenter NodePool for spot-preferred workers
spec:
  template:
    spec:
      requirements:
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["spot", "on-demand"]   # spot first, on-demand fallback
        # Use multiple instance families to maximize spot availability
        - key: karpenter.k8s.aws/instance-family
          operator: In
          values: ["m5", "m5a", "m5d", "m4", "m6i", "c5", "c5a", "c6i"]
```

**Cost impact:** Spot instances are 60-80% cheaper than on-demand. For KubernetesExecutor tasks, interrupted pods are automatically retried by Airflow's retry mechanism.

### Environment Hibernation

```python
# When a tenant hibernates an environment:
# 1. Scale scheduler replicas to 0 (no more DAG scheduling)
# 2. Scale webserver replicas to 0 (no UI access)
# 3. Scale triggerer replicas to 0
# 4. Workers: KubernetesExecutor — no persistent workers to scale
# 5. Keep namespace, secrets, ConfigMaps, RDS (pause RDS compute for dev plans)

async def hibernate_environment(tenant_id: str):
    k8s = get_k8s_client()
    namespace = f"tenant-{tenant_id}"

    for deployment in ["airflow-scheduler", "airflow-webserver", "airflow-triggerer"]:
        await k8s.scale_deployment(namespace, deployment, replicas=0)

    # For developer plan: pause RDS instance (saves ~$3/day)
    if tenant.plan == "developer":
        await rds.stop_db_instance(f"airflow-tenant-{tenant_id}")

    await update_environment_status(tenant_id, "hibernating")
```

**Hibernation savings:** A hibernated Starter environment costs ~$2/day (RDS storage only) vs ~$5/day running. Customers who work 9-5 Mon-Fri can save 60% on their bill with scheduled hibernation.

---

## 16. API Design

### REST API Conventions

- **Base URL:** `https://api.platform.com/v1`
- **Auth:** `Authorization: Bearer <JWT>` (Cognito JWT)
- **Async operations:** Return `202 Accepted` with `Location: /operations/{operationId}`
- **Rate limiting:** 100 req/min per tenant (API Gateway)
- **Pagination:** Cursor-based (`?after=<cursor>&limit=50`)

### Key Data Flows

**Creating an environment (async):**
```
POST /environments
→ 202 { "operation_id": "op-abc", "environment_id": "env-xyz" }

GET /operations/op-abc   (poll every 3 seconds)
→ 200 {
    "status": "in_progress",
    "phase": "Provisioning",
    "step": "Waiting for database to be ready",
    "steps_completed": 4,
    "steps_total": 12,
    "percent_complete": 33
  }

GET /operations/op-abc   (after ~2 minutes)
→ 200 {
    "status": "completed",
    "result": {
      "environment_id": "env-xyz",
      "airflow_url": "https://env-xyz.platform.com",
      "phase": "Running"
    }
  }
```

### WebSocket for Real-time Status

The dashboard uses WebSocket for live provisioning feedback:

```javascript
// Frontend: dashboard/src/hooks/useEnvironmentStatus.ts

const ws = new WebSocket(`wss://api.platform.com/v1/environments/${envId}/stream`);

ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  // update: { phase, step, steps_completed, steps_total, logs: ["..."] }
  setProvisioningState(update);
};
```

```python
# Backend WebSocket handler
@router.websocket("/environments/{env_id}/stream")
async def stream_environment_status(websocket: WebSocket, env_id: str):
    await websocket.accept()
    while True:
        status = await get_environment_status(env_id)
        await websocket.send_json(status)
        if status["phase"] in ["Running", "Failed"]:
            break
        await asyncio.sleep(3)
```

---

## 17. Data Model

### Core Control Plane Tables (Postgres)

```sql
-- Organizations (billing entity)
CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    slug        VARCHAR(100) UNIQUE NOT NULL,
    plan        VARCHAR(50) NOT NULL DEFAULT 'starter',
    stripe_customer_id VARCHAR(100),
    created_at  TIMESTAMP DEFAULT now(),
    updated_at  TIMESTAMP DEFAULT now()
);

-- Users
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID REFERENCES organizations(id),
    email           VARCHAR(255) UNIQUE NOT NULL,
    cognito_sub     VARCHAR(255) UNIQUE,
    role            VARCHAR(50) NOT NULL DEFAULT 'member',  -- owner, admin, member, viewer
    created_at      TIMESTAMP DEFAULT now()
);

-- Environments
CREATE TABLE environments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID REFERENCES organizations(id) NOT NULL,
    tenant_id       VARCHAR(50) UNIQUE NOT NULL,  -- k8s namespace suffix
    name            VARCHAR(255) NOT NULL,
    plan            VARCHAR(50) NOT NULL,
    airflow_version VARCHAR(20) NOT NULL,
    executor_type   VARCHAR(50) NOT NULL DEFAULT 'KubernetesExecutor',
    status          VARCHAR(50) NOT NULL DEFAULT 'provisioning',
    airflow_url     VARCHAR(255),
    aws_region      VARCHAR(50) NOT NULL DEFAULT 'us-east-1',
    git_repo        VARCHAR(500),
    git_branch      VARCHAR(100) DEFAULT 'main',
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMP DEFAULT now(),
    updated_at      TIMESTAMP DEFAULT now(),
    deleted_at      TIMESTAMP   -- soft delete
);

-- Provisioning operations (audit + status tracking)
CREATE TABLE operations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_id  UUID REFERENCES environments(id),
    operation_type  VARCHAR(50) NOT NULL,  -- create, delete, upgrade, hibernate, wake
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    started_at      TIMESTAMP DEFAULT now(),
    completed_at    TIMESTAMP,
    error           TEXT,
    steps_total     INT DEFAULT 12,
    steps_completed INT DEFAULT 0,
    current_step    VARCHAR(255),
    initiated_by    UUID REFERENCES users(id)
);

-- Usage metering (for billing)
CREATE TABLE usage_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID REFERENCES organizations(id),
    environment_id  UUID REFERENCES environments(id),
    event_type      VARCHAR(100) NOT NULL,  -- task_run, worker_hour, storage_gb
    quantity        DECIMAL(10,4) NOT NULL,
    unit            VARCHAR(50) NOT NULL,
    recorded_at     TIMESTAMP DEFAULT now(),
    stripe_meter_event_id VARCHAR(255)   -- idempotency
);

-- Indexes
CREATE INDEX idx_environments_org_id ON environments(org_id);
CREATE INDEX idx_environments_tenant_id ON environments(tenant_id);
CREATE INDEX idx_operations_environment_id ON operations(environment_id);
CREATE INDEX idx_usage_events_org_id_recorded ON usage_events(org_id, recorded_at);
```

---

## 18. Phased Delivery Plan

### Phase 1: Foundation MVP (Month 1–3)

**Goal:** First customer environment provisioned and running.

**Sprint 1–2 (Month 1):**
- [ ] EKS cluster provisioned (Terraform) — VPC, subnets, node groups, Karpenter
- [ ] Base Helm chart validated in sandbox — deploy Airflow in a test namespace manually
- [ ] Provisioning API scaffold — FastAPI project, DB schema, basic `/environments` endpoint
- [ ] Secrets Manager + ExternalSecrets Operator integration
- [ ] RDS provisioning automation (Terraform module)

**Sprint 3–4 (Month 2):**
- [ ] Namespace provisioning automation — K8s namespace, ResourceQuota, NetworkPolicy, RBAC, IRSA
- [ ] Helm template rendering + values generation per tenant
- [ ] End-to-end provisioning: API call → RDS → namespace → Helm install → environment running
- [ ] Basic web dashboard: login, create environment, view status
- [ ] Cognito User Pool setup + JWT auth middleware

**Sprint 5–6 (Month 3):**
- [ ] Git-sync DAG delivery working
- [ ] Ingress + wildcard TLS cert + ExternalDNS
- [ ] Environment delete / teardown flow
- [ ] Internal beta: 3 internal test environments running
- [ ] Prometheus + basic Grafana dashboards
- [ ] Runbooks written for common operational scenarios

**Phase 1 Exit Criteria:**
- Create an Airflow environment via API in under 3 minutes
- DAGs deployed via Git and running on schedule
- Environment accessible at `{tenantId}.platform.com`
- Delete environment and confirm full cleanup

---

### Phase 2: General Availability (Month 4–6)

**Goal:** Production-ready, paying customers.

**Month 4:**
- [ ] Environment hibernation + wake
- [ ] Stripe billing integration + usage metering
- [ ] Airflow version selector (support 2.8.x, 2.9.x, 2.10.x)
- [ ] GitOps via ArgoCD — all tenant configs in Git
- [ ] Environment upgrade (Airflow version bump) — zero-downtime rolling

**Month 5:**
- [ ] Full observability: per-tenant Grafana orgs, Fluent Bit log pipeline to S3
- [ ] SNS alerting: scheduler unhealthy, high failure rate, SLA miss
- [ ] Connections & Variables management UI
- [ ] Multi-AZ RDS for Production plan
- [ ] Karpenter spot instance strategy for workers

**Month 6:**
- [ ] Load testing: 50 concurrent tenant environments
- [ ] SLA monitoring and reporting
- [ ] GA launch: public-facing, paid plans available
- [ ] Support runbook for all tier-1 incidents

---

### Phase 3: Enterprise (Month 7–9)

**Goal:** Enterprise accounts, compliance, advanced features.

**Month 7–8:**
- [ ] SAML 2.0 / OIDC SSO (Okta, Azure AD, Google Workspace)
- [ ] Team/Workspace model — multiple environments per org, sub-team access
- [ ] Fine-grained RBAC: Editor, Viewer, Ops roles
- [ ] Audit log API (SOC 2 Type II requirement)
- [ ] Terraform provider for platform as code

**Month 9:**
- [ ] Dedicated cluster option (Enterprise tier)
- [ ] OpenLineage integration for data lineage tracking
- [ ] Airflow 3.0 / asset-based scheduling support
- [ ] Custom domain mapping (`airflow.customerdomain.com`)
- [ ] SOC 2 Type II audit preparation

---

## 19. Runbooks & Operational Procedures

### Runbook: Scheduler Not Heartbeating

**Alert:** `AirflowSchedulerUnhealthy` fires for tenant `{TENANT_ID}`

```bash
# 1. Check scheduler pod status
kubectl get pods -n tenant-${TENANT_ID} -l component=scheduler

# 2. Check scheduler logs (last 100 lines)
kubectl logs -n tenant-${TENANT_ID} -l component=scheduler --tail=100

# 3. Check RDS connectivity from scheduler pod
kubectl exec -n tenant-${TENANT_ID} deploy/airflow-scheduler -- \
  python -c "import psycopg2; psycopg2.connect('$(kubectl get secret airflow-metadata-secret -n tenant-${TENANT_ID} -o jsonpath={.data.connection} | base64 -d)')"

# 4. Check resource quota not exhausted
kubectl describe resourcequota -n tenant-${TENANT_ID}

# 5. Restart scheduler if no root cause found
kubectl rollout restart deploy/airflow-scheduler -n tenant-${TENANT_ID}
```

### Runbook: Failed Environment Provisioning

```bash
# 1. Check operation status
SELECT * FROM operations WHERE environment_id = '${ENV_ID}' ORDER BY started_at DESC LIMIT 1;

# 2. Check which step failed (from current_step column)
# 3. Check CloudWatch logs for the provisioning job
aws logs filter-log-events \
  --log-group-name /airflow-platform/provisioning \
  --filter-pattern '"tenant_id": "${TENANT_ID}"' \
  --start-time $(date -d '1 hour ago' +%s000)

# 4. Check if RDS instance exists
aws rds describe-db-instances --db-instance-identifier airflow-tenant-${TENANT_ID}

# 5. Check if namespace exists and has correct labels
kubectl get namespace tenant-${TENANT_ID} -o yaml

# 6. Retry provisioning (idempotent — safe to re-run)
curl -X POST https://api.platform.com/v1/environments/${ENV_ID}/retry \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"
```

### Runbook: RDS Instance Failover (Multi-AZ)

```bash
# Multi-AZ failover is automatic. Airflow will reconnect automatically via RDS Proxy.
# Monitor during failover:
aws rds describe-events \
  --source-identifier airflow-tenant-${TENANT_ID} \
  --source-type db-instance \
  --duration 60

# Check Airflow scheduler reconnected after failover:
kubectl logs -n tenant-${TENANT_ID} deploy/airflow-scheduler --since=5m | grep -i "database\|connection"
```

---

## 20. Open Questions & Decision Log

### Open Questions

| # | Question | Owner | Due | Options |
|---|---|---|---|---|
| 1 | Use CeleryExecutor for Phase 1 or KubernetesExecutor only? | Eng Lead | M1 Sprint 1 | K8s (simpler, chosen), Celery (Phase 3 option) |
| 2 | Shared ALB with IngressGroup vs per-tenant ALB? | Platform Eng | M1 Sprint 2 | Shared (chosen for cost), per-tenant (needed if customer wants custom cert) |
| 3 | PostgreSQL version for RDS? | Platform Eng | M1 Sprint 1 | Postgres 15.x (chosen), 16.x (newer, less tested with Airflow) |
| 4 | Log retention policy defaults | Product | M2 | 30 days CloudWatch, 1 year S3 (proposed) |
| 5 | Max environments per org on Starter plan | Product | M2 | 1 (conservative), 3 (proposed) |
| 6 | Support Windows EC2 nodes? | Platform | M3 | No — Linux only |
| 7 | Multi-region support (Phase 2 or 3)? | Leadership | M4 | Phase 3 — one region per deployment initially |

### Decision Log

| Date | Decision | Rationale | Decided By |
|---|---|---|---|
| 2026-03 | KubernetesExecutor as default | Simpler ops, no broker, better pod isolation per task | Eng Lead |
| 2026-03 | Namespace-per-tenant (not cluster-per-tenant) | Cost: cluster-per-tenant is 10x more expensive at <50 customers | Arch Review |
| 2026-03 | Official Apache Airflow Helm chart | Astronomer chart requires license; official chart is feature-equivalent | Eng Lead |
| 2026-03 | ArgoCD for GitOps | Industry standard, good multi-tenant support, active community | Platform Eng |
| 2026-03 | Karpenter over Cluster Autoscaler | 10x faster node provisioning; consolidation built-in | Platform Eng |
| 2026-03 | Dedicated RDS per tenant | Metadata DB contains connection secrets; sharing is a hard security violation | Security |
| 2026-03 | FastAPI for Provisioning API | Async-native, fast, good Pydantic validation, Python aligns with Airflow expertise | Eng Lead |

---

## Appendix A: Technology Stack Summary

| Category | Technology | Version | Notes |
|---|---|---|---|
| Container orchestration | Amazon EKS | K8s 1.29 | |
| Node provisioning | Karpenter | v0.37 | Replaces Cluster Autoscaler |
| Airflow | Apache Airflow | 2.9.x | Official Helm chart |
| GitOps | ArgoCD | v2.10 | Per-tenant Application |
| Secrets sync | External Secrets Operator | v0.9 | AWS Secrets Manager backend |
| Ingress | AWS Load Balancer Controller | v2.7 | ALB IngressGroup |
| DNS automation | ExternalDNS | v0.14 | Route 53 |
| Service mesh | None (Phase 1) | — | Evaluate Cilium in Phase 2 |
| Policy enforcement | OPA Gatekeeper | v3.15 | Pod security + resource requirements |
| Autoscaling | KEDA | v2.13 | For Triggerer scaling |
| Metrics | Prometheus Operator | v0.71 | kube-prometheus-stack Helm chart |
| Dashboards | Grafana | v10 | Multi-org for tenant isolation |
| Logging | Fluent Bit | v3.0 | DaemonSet per node |
| IaC | Terraform | v1.7 | AWS provider ~5.x |
| Control plane API | FastAPI | 0.110 | Python 3.12 |
| K8s Operator | Go + controller-runtime | Go 1.22 | |
| Frontend | React + TypeScript | React 18 | Vite, TanStack Query |
| Auth | AWS Cognito | — | User Pools + Identity Pools |
| Billing | Stripe | — | Metered billing |
| Database (control plane) | Amazon RDS Postgres | 15.4 | |
| Database (per tenant) | Amazon RDS Postgres | 15.4 | One instance per tenant |
| Object storage | Amazon S3 | — | DAGs, logs, state |
| Container registry | Amazon ECR | — | One repo per tenant |

## Appendix B: Useful Commands

```bash
# List all tenant namespaces
kubectl get namespaces -l platform-managed=true

# Check resource usage across all tenant namespaces
kubectl top pods -A -l platform-managed=true

# Get all AirflowDeployment CRDs
kubectl get airflowdeployments -n platform-system

# Tail scheduler logs for a tenant
kubectl logs -f -n tenant-${TENANT_ID} deploy/airflow-scheduler

# Force ArgoCD sync for a tenant
argocd app sync airflow-tenant-${TENANT_ID} --force

# Check Karpenter node provisioning events
kubectl get events -n kube-system --field-selector reason=ProvisioningNodeClaim

# Run Airflow DB check from within scheduler pod
kubectl exec -n tenant-${TENANT_ID} deploy/airflow-scheduler -- airflow db check

# Manually trigger a DAG run (for testing)
kubectl exec -n tenant-${TENANT_ID} deploy/airflow-scheduler -- \
  airflow dags trigger ${DAG_ID}
```

---

*Document maintained by Platform Engineering. For questions, open an issue in `airflow-platform/platform-design`.*
