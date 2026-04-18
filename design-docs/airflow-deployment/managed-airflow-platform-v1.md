# Managed Airflow Platform — Astronomer-Style Deployment Blueprint

> **Goal:** Build an Astronomer-equivalent managed Airflow platform on AWS with multi-tenant support, starting with the deployment model (dev / pre-prod / prod environments).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Two-Plane Model](#2-two-plane-model)
3. [Deployment Environment Types](#3-deployment-environment-types)
4. [EKS Namespace Convention](#4-eks-namespace-convention)
5. [Helm Values Per Environment](#5-helm-values-per-environment)
6. [Hibernation for Dev Environments](#6-hibernation-for-dev-environments)
7. [Ephemeral Deployments](#7-ephemeral-deployments)
8. [Secrets & Environment Variable Isolation](#8-secrets--environment-variable-isolation)
9. [Build Order for V1](#9-build-order-for-v1)

---

## 1. Architecture Overview

You are building a two-plane platform that replicates Astronomer's Astro product on AWS:

- **Control plane** — your centrally hosted AWS account running the API server, provisioner, web portal, and tenant registry
- **Data plane** — per-tenant namespaces on a shared EKS cluster, each running an isolated Airflow instance

Each tenant gets three deployment environments: **Development**, **Pre-production**, and **Production**. These map to separate Kubernetes namespaces with different resource profiles, scaling behavior, and HA requirements.

---

## 2. Two-Plane Model

### Control plane components (your AWS account)

| Component | Technology | Responsibility |
|---|---|---|
| API server | FastAPI / Node.js on ECS Fargate | REST endpoints for tenant management, DAG deploy, secrets |
| Provisioner | AWS Step Functions + Terraform | Creates/destroys namespaces, Helm releases, IAM roles |
| Web portal | React on S3 + CloudFront | Tenant-facing UI for deployments, DAGs, users, secrets |
| Tenant registry | RDS PostgreSQL or DynamoDB | Stores tenant records, environment configs, status |
| Auth | Amazon Cognito + OIDC | User pools, JWT issuance, SSO |
| Secrets store | AWS Secrets Manager | Per-tenant Fernet keys, DB credentials, Airflow connections |

### Data plane components (per-tenant, on EKS)

| Component | Technology | Responsibility |
|---|---|---|
| Airflow scheduler | Helm chart (apache-airflow) | DAG scheduling, heartbeat |
| Airflow webserver | Helm chart | UI, proxied through tenant subdomain |
| Workers | KubernetesExecutor pods | One pod per task, auto-created and destroyed |
| Metadata DB | Aurora Serverless v2 | Airflow task state, XCom, connections |
| DAG storage | S3 bucket (per tenant) | Source of truth for DAG files, synced via git-sync |
| Ingress | ALB + Route 53 | `{tenant-slug}.airflow.yourplatform.com` |

---

## 3. Deployment Environment Types

Astronomer offers three deployment templates: **Development**, **Pre-Production**, and **Production**. Each maps to a different resource profile and behavior.

### Development

- 1 scheduler (small CPU/memory)
- 1–2 workers minimum
- **Hibernation supported** — scales to zero on a schedule (e.g., nights and weekends), costing $0 during downtime
- **Ephemeral variant** — auto-teardown after a defined TTL (used for CI/CD preview environments)
- No HA requirement
- In-cluster PostgreSQL (cheap, acceptable for dev)
- DAGs synced from `dev` Git branch

### Pre-production

- 1–2 schedulers (medium size)
- 3–5 workers, always on
- Production-like configuration (same secrets structure, same executor)
- DAGs synced from a release/staging branch
- Used for integration testing before promoting to prod

### Production

- 2 schedulers (Airflow 2.x HA mode with leader election)
- 5–10+ workers with Kubernetes autoscaling
- Multi-AZ Aurora for metadata DB
- `main` branch only, gated by CI/CD approval
- SLA monitoring enforced

---

## 4. EKS Namespace Convention

Each tenant gets three namespaces on the shared EKS cluster — one per environment:

```
airflow-{tenant-id}-dev
airflow-{tenant-id}-preprod
airflow-{tenant-id}-prod
```

**Example for tenant `acme`:**
```
airflow-acme-dev
airflow-acme-preprod
airflow-acme-prod
```

### Namespace isolation

Each namespace is isolated by:

- **Kubernetes RBAC** — tenant service accounts can only access their own namespaces
- **NetworkPolicy** — pods in `airflow-acme-dev` cannot reach pods in `airflow-acme-prod` or any other tenant's namespace
- **Resource Quotas** — CPU and memory caps per namespace enforce tier limits
- **IAM Roles for Service Accounts (IRSA)** — each Airflow pod assumes an IAM role scoped only to its own S3 bucket and Secrets Manager paths

### Create a namespace (example)

```bash
kubectl create namespace airflow-acme-dev

# Apply resource quota for dev tier
kubectl apply -f - <<EOF
apiVersion: v1
kind: ResourceQuota
metadata:
  name: dev-quota
  namespace: airflow-acme-dev
spec:
  hard:
    requests.cpu: "4"
    requests.memory: 8Gi
    limits.cpu: "8"
    limits.memory: 16Gi
EOF
```

---

## 5. Helm Values Per Environment

Use the official `apache-airflow` Helm chart. Maintain one values file per tier.

### Install the chart

```bash
helm repo add apache-airflow https://airflow.apache.org
helm repo update
```

### `values-dev.yaml` — Development profile

```yaml
executor: KubernetesExecutor

scheduler:
  replicas: 1
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: "1"
      memory: 2Gi

workers:
  replicas: 1
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: "1"
      memory: 2Gi

webserver:
  replicas: 1
  resources:
    requests:
      cpu: 250m
      memory: 512Mi

# Use in-cluster PostgreSQL for dev (no Aurora cost)
postgresql:
  enabled: true

# Sync DAGs from the dev branch
dags:
  gitSync:
    enabled: true
    repo: git@github.com:your-org/tenant-dags.git
    branch: dev
    subPath: dags/
    period: 30s

# Airflow config overrides
config:
  core:
    load_examples: "False"
  logging:
    remote_logging: "True"
    remote_base_log_folder: "s3://platform-logs-{tenant-id}/dev"
```

**Deploy dev environment:**

```bash
helm upgrade --install airflow-acme-dev apache-airflow/airflow \
  --namespace airflow-acme-dev \
  --values values-dev.yaml \
  --set webserver.defaultUser.password=changeme \
  --wait
```

---

### `values-preprod.yaml` — Pre-production profile

```yaml
executor: KubernetesExecutor

scheduler:
  replicas: 2
  resources:
    requests:
      cpu: "1"
      memory: 2Gi
    limits:
      cpu: "2"
      memory: 4Gi

workers:
  replicas: 3
  resources:
    requests:
      cpu: "1"
      memory: 2Gi

webserver:
  replicas: 1

postgresql:
  enabled: false

data:
  metadataConnection:
    protocol: postgresql
    host: "{preprod-aurora-cluster-endpoint}"
    port: 5432
    db: airflow_acme_preprod
    user: airflow
    passwordSecretName: airflow-acme-preprod-db-secret
    passwordSecretKey: password

dags:
  gitSync:
    enabled: true
    repo: git@github.com:your-org/tenant-dags.git
    branch: staging
    subPath: dags/
    period: 30s

config:
  core:
    load_examples: "False"
  logging:
    remote_logging: "True"
    remote_base_log_folder: "s3://platform-logs-{tenant-id}/preprod"
```

---

### `values-prod.yaml` — Production profile

```yaml
executor: KubernetesExecutor

scheduler:
  replicas: 2                  # HA mode — Airflow 2.x leader election
  resources:
    requests:
      cpu: "2"
      memory: 4Gi
    limits:
      cpu: "4"
      memory: 8Gi

workers:
  replicas: 5
  resources:
    requests:
      cpu: "2"
      memory: 4Gi
  autoscaling:
    enabled: true
    minReplicas: 5
    maxReplicas: 20
    targetCPUUtilization: 70

webserver:
  replicas: 2
  resources:
    requests:
      cpu: "1"
      memory: 2Gi

postgresql:
  enabled: false

data:
  metadataConnection:
    protocol: postgresql
    host: "{prod-aurora-cluster-endpoint}"
    port: 5432
    db: airflow_acme_prod
    user: airflow
    passwordSecretName: airflow-acme-prod-db-secret
    passwordSecretKey: password

dags:
  gitSync:
    enabled: true
    repo: git@github.com:your-org/tenant-dags.git
    branch: main
    subPath: dags/
    period: 30s

config:
  core:
    load_examples: "False"
  scheduler:
    scheduler_heartbeat_sec: "5"
  logging:
    remote_logging: "True"
    remote_base_log_folder: "s3://platform-logs-{tenant-id}/prod"
```

**Deploy production environment:**

```bash
helm upgrade --install airflow-acme-prod apache-airflow/airflow \
  --namespace airflow-acme-prod \
  --values values-prod.yaml \
  --wait
```

---

## 6. Hibernation for Dev Environments

Astronomer's **Development Mode** allows environments to scale to zero on a schedule, costing $0 during hibernation. Replicate this using Kubernetes CronJobs.

### Hibernate CronJob (scales to zero at 7 PM on weekdays)

```yaml
# hibernate-dev.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: hibernate-dev
  namespace: airflow-{tenant-id}-dev
spec:
  schedule: "0 19 * * 1-5"      # 7 PM Mon–Fri (UTC)
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: namespace-scaler
          restartPolicy: OnFailure
          containers:
          - name: scaler
            image: bitnami/kubectl:latest
            command:
            - /bin/sh
            - -c
            - |
              echo "Hibernating dev environment..."
              kubectl scale deployment \
                airflow-{tenant-id}-dev-scheduler \
                airflow-{tenant-id}-dev-webserver \
                --replicas=0 \
                -n airflow-{tenant-id}-dev
              echo "Hibernation complete."
```

### Wake CronJob (restores at 8 AM on weekdays)

```yaml
# wake-dev.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: wake-dev
  namespace: airflow-{tenant-id}-dev
spec:
  schedule: "0 8 * * 1-5"       # 8 AM Mon–Fri (UTC)
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: namespace-scaler
          restartPolicy: OnFailure
          containers:
          - name: scaler
            image: bitnami/kubectl:latest
            command:
            - /bin/sh
            - -c
            - |
              echo "Waking dev environment..."
              kubectl scale deployment \
                airflow-{tenant-id}-dev-scheduler \
                --replicas=1 \
                -n airflow-{tenant-id}-dev
              kubectl scale deployment \
                airflow-{tenant-id}-dev-webserver \
                --replicas=1 \
                -n airflow-{tenant-id}-dev
              echo "Wake complete."
```

### RBAC for the scaler service account

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: namespace-scaler
  namespace: airflow-{tenant-id}-dev
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: deployment-scaler
  namespace: airflow-{tenant-id}-dev
rules:
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "patch", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: deployment-scaler-binding
  namespace: airflow-{tenant-id}-dev
subjects:
- kind: ServiceAccount
  name: namespace-scaler
roleRef:
  kind: Role
  name: deployment-scaler
  apiGroup: rbac.authorization.k8s.io
```

### Control plane API for hibernation schedule

When a tenant creates or updates a dev deployment, your API accepts a `hibernation_schedule` parameter and generates these CronJob manifests dynamically:

```json
POST /api/v1/tenants/acme/deployments/dev/hibernation
{
  "enabled": true,
  "timezone": "America/New_York",
  "sleep_cron": "0 19 * * 1-5",
  "wake_cron": "0 8 * * 1-5"
}
```

The provisioner renders the manifests with the correct namespace and applies them via `kubectl apply`.

---

## 7. Ephemeral Deployments

Astronomer's **Ephemeral Test Deployments** launch an isolated Airflow environment for a feature branch, run DAGs with real dependencies, then tear down automatically. This is the mechanism behind CI/CD PR preview environments.

### API design

```
POST /api/v1/tenants/{tenant-id}/ephemeral-deployments
```

**Request body:**
```json
{
  "ttl_minutes": 60,
  "dag_branch": "feature/new-etl-pipeline",
  "base_profile": "dev",
  "notify_webhook": "https://your-ci-system/webhook/result"
}
```

**Response:**
```json
{
  "ephemeral_id": "eph-a3f9c2",
  "namespace": "airflow-acme-ephemeral-a3f9c2",
  "webserver_url": "https://acme-eph-a3f9c2.airflow.yourplatform.com",
  "expires_at": "2024-01-15T15:00:00Z",
  "status": "PROVISIONING"
}
```

### Provisioner steps

```bash
# 1. Generate a unique ID
EPHEMERAL_ID="eph-$(openssl rand -hex 3)"
NAMESPACE="airflow-acme-ephemeral-${EPHEMERAL_ID}"

# 2. Create namespace
kubectl create namespace ${NAMESPACE}

# 3. Deploy Airflow using dev values, pointing at the feature branch
helm upgrade --install airflow-${EPHEMERAL_ID} apache-airflow/airflow \
  --namespace ${NAMESPACE} \
  --values values-dev.yaml \
  --set dags.gitSync.branch=feature/new-etl-pipeline \
  --wait --timeout 5m

# 4. Apply TTL cleanup job
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: teardown-${EPHEMERAL_ID}
  namespace: ${NAMESPACE}
spec:
  ttlSecondsAfterFinished: 60
  template:
    spec:
      restartPolicy: OnFailure
      serviceAccountName: namespace-teardown
      containers:
      - name: teardown
        image: bitnami/kubectl:latest
        command:
        - /bin/sh
        - -c
        - |
          sleep $((60 * ${TTL_MINUTES}))
          helm uninstall airflow-${EPHEMERAL_ID} -n ${NAMESPACE}
          kubectl delete namespace ${NAMESPACE}
EOF
```

### CI/CD integration (GitHub Actions example)

```yaml
# .github/workflows/airflow-preview.yml
name: Airflow preview environment

on:
  pull_request:
    paths:
      - 'dags/**'

jobs:
  preview:
    runs-on: ubuntu-latest
    steps:
    - name: Create ephemeral deployment
      run: |
        RESPONSE=$(curl -s -X POST \
          https://api.yourplatform.com/api/v1/tenants/${{ vars.TENANT_ID }}/ephemeral-deployments \
          -H "Authorization: Bearer ${{ secrets.PLATFORM_API_KEY }}" \
          -H "Content-Type: application/json" \
          -d '{
            "ttl_minutes": 120,
            "dag_branch": "${{ github.head_ref }}",
            "base_profile": "dev"
          }')
        echo "Preview URL: $(echo $RESPONSE | jq -r .webserver_url)"
        echo "preview_url=$(echo $RESPONSE | jq -r .webserver_url)" >> $GITHUB_OUTPUT

    - name: Post preview URL to PR
      uses: actions/github-script@v7
      with:
        script: |
          github.rest.issues.createComment({
            issue_number: context.issue.number,
            owner: context.repo.owner,
            repo: context.repo.repo,
            body: '🚀 Airflow preview ready: ${{ steps.preview.outputs.preview_url }}'
          })
```

---

## 8. Secrets & Environment Variable Isolation

Each environment's secrets are namespaced in AWS Secrets Manager and synced into Kubernetes Secrets by your provisioner.

### Secrets Manager naming convention

```
/tenants/{tenant-id}/dev/AIRFLOW__CORE__FERNET_KEY
/tenants/{tenant-id}/dev/DB_PASSWORD
/tenants/{tenant-id}/dev/AIRFLOW_CONN_MY_DATABASE

/tenants/{tenant-id}/prod/AIRFLOW__CORE__FERNET_KEY
/tenants/{tenant-id}/prod/DB_PASSWORD
/tenants/{tenant-id}/prod/AIRFLOW_CONN_MY_DATABASE
```

### Sync secrets to Kubernetes on deploy

```bash
# Pull secrets from Secrets Manager and create Kubernetes Secret
aws secretsmanager get-secret-value \
  --secret-id /tenants/acme/dev/AIRFLOW__CORE__FERNET_KEY \
  --query SecretString --output text | \
kubectl create secret generic airflow-acme-dev-secrets \
  --namespace airflow-acme-dev \
  --from-literal=fernet-key=-

# Reference in Helm values
fernetKeySecretName: airflow-acme-dev-secrets
fernetKeySecretKey: fernet-key
```

### IAM role scoping (IRSA)

Each Airflow deployment assumes an IAM role via IRSA that can only access its own paths:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::platform-dags-acme-dev",
        "arn:aws:s3:::platform-dags-acme-dev/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:*:*:secret:/tenants/acme/dev/*"
    }
  ]
}
```

### Control plane API for secrets management

Tenants manage secrets through your portal, not directly in AWS:

```
# Set a connection
POST /api/v1/tenants/acme/deployments/dev/secrets
{
  "key": "AIRFLOW_CONN_MY_POSTGRES",
  "value": "postgresql://user:pass@host:5432/db"
}
```

The API writes to Secrets Manager and patches the Kubernetes Secret in the correct namespace, triggering a rolling restart of Airflow pods.

---

## 9. Build Order for V1

Build in this exact sequence — each step unblocks the next.

### Step 1 — Manual Helm deployment (week 1)

Get one Airflow running in one namespace manually. Understand every Helm value before automating anything.

```bash
kubectl create namespace airflow-test-tenant-dev
helm upgrade --install airflow-test apache-airflow/airflow \
  --namespace airflow-test-tenant-dev \
  --values values-dev.yaml \
  --wait
```

Validate: scheduler running, webserver accessible, a simple DAG executes end to end.

### Step 2 — Parameterize into a script (week 1–2)

Write a shell script or Python script that accepts `tenant_id` and `environment` and provisions all three namespaces:

```bash
./provision-tenant.sh --tenant-id acme --environments dev,preprod,prod
```

### Step 3 — Add hibernation CronJobs (week 2)

Apply the wake/sleep CronJobs to every dev namespace as part of the provisioning script.

### Step 4 — Terraform module (week 2–3)

Codify the above into a reusable Terraform module:

```hcl
module "tenant_airflow" {
  source      = "./modules/airflow-tenant"
  tenant_id   = "acme"
  tenant_slug = "acme"
  tier        = "standard"          # standard | premium
  environments = ["dev", "preprod", "prod"]
  dag_repo    = "git@github.com:acme-corp/airflow-dags.git"
}
```

The module creates: namespaces, Helm releases, S3 buckets, Aurora databases, IAM roles, Route 53 records, and hibernation CronJobs.

### Step 5 — Provisioning API + Step Functions (week 3–4)

Wrap the Terraform module in a Step Functions state machine triggered by an API call:

```
POST /api/v1/tenants
{
  "tenant_id": "acme",
  "slug": "acme",
  "tier": "standard",
  "dag_repo": "git@github.com:acme-corp/airflow-dags.git"
}
```

State machine steps: `VALIDATE → CREATE_NAMESPACES → DEPLOY_HELM → CREATE_DNS → NOTIFY → DONE`

### Step 6 — Ephemeral deployments API (week 4–5)

Add the ephemeral deployment endpoint. It reuses all the above with a TTL cleanup Job appended.

### Step 7 — Secrets management API (week 5–6)

Build the `/secrets` endpoints. Write to Secrets Manager, patch Kubernetes Secrets, trigger rolling restart.

---

## Reference: Key AWS services used

| Service | Purpose |
|---|---|
| EKS | Kubernetes cluster hosting all tenant Airflow deployments |
| Aurora Serverless v2 | Airflow metadata DB per tenant (preprod and prod) |
| S3 | DAG storage, log storage per tenant |
| Secrets Manager | Fernet keys, DB passwords, Airflow connections |
| Step Functions | Provisioning state machine with retries and audit trail |
| Route 53 | Tenant subdomain per environment |
| ALB | Ingress for Airflow webserver per tenant |
| Cognito | User authentication and JWT issuance for the portal |
| CloudWatch | Scheduler heartbeat alarms, task metrics via StatsD, log routing |
| IAM + IRSA | Fine-grained pod-level AWS permissions per tenant |

---

---

## 10. Scaling to 2,000+ Tenants — Cluster Sharding

### The problem with one cluster

A single EKS cluster has practical limits that break down well before 2,000 tenants:

| Limit | Threshold | Impact |
|---|---|---|
| Nodes per cluster | ~150–300 | API server degrades beyond this |
| Namespaces per cluster | ~500 | etcd watch pressure slows control loops |
| Pods per cluster | ~110,000 | Kubernetes hard limit (300 nodes × 110 pods) |

At 3 namespaces per tenant (dev, preprod, prod) × 2,000 tenants = **6,000 namespaces** on one cluster — this is not viable.

### The solution: cluster sharding

Your control plane manages a **fleet of EKS clusters**, not one. Each cluster holds ~100 tenants. 2,000 tenants = ~20 clusters. The control plane decides which cluster each tenant lands on — tenants never know or care which cluster they're on.

This is exactly how Astronomer operates. Their standard cluster is a multi-tenant cluster holding many Deployments; when one fills up, their control plane routes new tenants to a new cluster automatically.

---

### Cluster registry

Add a cluster registry table to your control plane (DynamoDB):
```
ClusterID        | Region    | TenantCount | MaxTenants | Status
cluster-use1-01  | us-east-1 | 98          | 100        | NEAR_FULL
cluster-use1-02  | us-east-1 | 54          | 100        | AVAILABLE
cluster-euw1-01  | eu-west-1 | 12          | 100        | AVAILABLE
```

And a tenant-to-cluster mapping table:
```
TenantID   | ClusterID       | Region    | Namespaces
acme       | cluster-use1-02 | us-east-1 | dev, preprod, prod
globex     | cluster-use1-01 | us-east-1 | prod
initech    | cluster-euw1-01 | eu-west-1 | dev, prod
```

---

### Provisioner placement logic

The provisioner gains one extra step — find the right cluster before creating the namespace:
```python
def place_tenant(tenant_id, tier, region):
    # 1. Find a cluster in region with capacity under 75%
    cluster = cluster_registry.find_available(
        region=region,
        tier="standard",
        max_utilization=0.75
    )

    # 2. If none available, provision a new EKS cluster (~15 min async)
    if not cluster:
        cluster = provision_new_cluster(region=region)
        cluster_registry.register(cluster)

    # 3. Place tenant on that cluster
    namespace = f"airflow-{tenant_id}-prod"
    deploy_to_cluster(cluster.id, namespace, tenant_id)

    # 4. Record the mapping
    cluster_registry.assign(tenant_id=tenant_id, cluster_id=cluster.id)
    cluster_registry.increment(cluster.id)
```

The rest of the provisioning flow (Helm install, S3, Aurora, Route 53) is unchanged — it just runs against the selected cluster's kubeconfig.

---

### Lazy namespace provisioning

Don't create all three environments (dev, preprod, prod) upfront. Only create what the tenant actually uses:
```
Tenant signs up           → provision prod namespace only
Tenant requests dev       → provision dev namespace on-demand
Tenant deletes dev        → delete namespace, reclaim resources
Tenant inactive 90 days   → hibernate prod, flag for review
```

For 2,000 tenants where only ~30% actively use dev at any time:
```
Without lazy provisioning:  2,000 × 3 = 6,000 namespaces
With lazy provisioning:     2,000 prod + 600 active dev + 400 preprod = 3,000 namespaces
Cluster count reduction:    20 clusters → ~10 clusters
```

---

### Hibernated namespaces are nearly free

Dev namespaces scaled to zero (scheduler + webserver at 0 replicas) consume **no nodes** — they exist only as metadata in etcd. This means you can pack hibernated dev namespaces densely without affecting node capacity.

Practical packing density per cluster:
```
100 prod namespaces     × ~10 pods each  = 1,000 active pods
100 preprod namespaces  × ~6 pods each   =   600 active pods
200 dev namespaces      (hibernated)     =     0 pods at night

Total active pods:   ~1,600  (well within the 110,000 pod limit)
Total namespaces:    ~400    (manageable for etcd)
Cluster node count:  ~20–30 nodes at night, ~50 at peak
```

---

### Dedicated clusters for enterprise tenants

Some customers will require full cluster isolation (compliance, network policy, data residency). Offer a **dedicated cluster** tier:

- 1 EKS cluster per tenant
- All three environments (dev, preprod, prod) on that cluster
- Tenant pays a cluster-level base fee on top of usage
- Your control plane provisions and manages it identically — just a cluster with one tenant

This maps directly to Astronomer's dedicated cluster offering.

---

### Scaling summary

| Tenants | Clusters needed | Architecture change |
|---|---|---|
| 1–100 | 1 | Single EKS cluster, no cluster registry needed |
| 100–500 | 2–5 | Add cluster registry, manual cluster addition |
| 500–2,000 | 5–20 | Auto-provision new clusters when existing ones hit 75% |
| 2,000+ | 20+ | Same model — no architecture change, just more clusters |

The control plane code does not change as you scale beyond v1. The cluster registry and placement logic handle growth automatically. Each cluster is operationally identical — adding capacity means running another Terraform module, not redesigning anything.

---

### Auto-provision trigger (Step Functions addition)

Add one state to your existing provisioning state machine:
```
VALIDATE → FIND_OR_CREATE_CLUSTER → CREATE_NAMESPACE → DEPLOY_HELM → CREATE_DNS → NOTIFY → DONE
```

The `FIND_OR_CREATE_CLUSTER` state:
1. Queries the cluster registry for an available cluster in the target region
2. If found — returns `cluster_id`, state machine proceeds
3. If not found — triggers a sub-state-machine that provisions a new EKS cluster, waits for it to become ready (~15 min), registers it, then returns `cluster_id`
4. The rest of the flow is unchanged

This means tenant provisioning is fully automatic even when a new cluster needs to be spun up — it just takes longer for that first tenant on a fresh cluster.

*Blueprint version 1.0 — covers deployment model (dev / pre-prod / prod) with hibernation, ephemeral environments, and secrets isolation on AWS EKS.*
