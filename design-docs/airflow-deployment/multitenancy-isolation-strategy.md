# Multi-tenant Isolation Strategy — Solving the Namespace Scaling Problem

> **Context:** When running a managed Airflow platform for 2,000+ tenants on Kubernetes, the naive approach of one namespace per tenant breaks down. This document covers all four isolation approaches, their trade-offs, and the recommended architecture using vCluster.

---

## Table of Contents

1. [The Problem with One Namespace Per Tenant](#1-the-problem-with-one-namespace-per-tenant)
2. [The Four Approaches Compared](#2-the-four-approaches-compared)
3. [Recommendation: vCluster](#3-recommendation-vcluster)
4. [How vCluster Works](#4-how-vcluster-works)
5. [Provisioning a Tenant with vCluster](#5-provisioning-a-tenant-with-vcluster)
6. [Density at 2,000 Tenants](#6-density-at-2000-tenants)
7. [Tiered Isolation Model](#7-tiered-isolation-model)
8. [Provisioner Logic for All Tiers](#8-provisioner-logic-for-all-tiers)
9. [Migration Path from Namespace-per-Tenant](#9-migration-path-from-namespace-per-tenant)

---

## 1. The Problem with One Namespace Per Tenant

At small scale (< 100 tenants), one Kubernetes namespace per tenant works fine. At 2,000 tenants with 3 environments each (dev, preprod, prod), you hit hard Kubernetes limits:

| Limit | Threshold | What breaks |
|---|---|---|
| Namespaces per cluster | ~500 | etcd watch pressure slows all control loops |
| Pods per cluster | ~110,000 | Kubernetes scheduler degrades |
| API server load | ~150–300 nodes | Control plane becomes unresponsive |
| etcd size | ~8GB | etcd compaction and defrag required constantly |

At 2,000 tenants × 3 namespaces = **6,000 namespaces** on one cluster — this is not viable. Even with cluster sharding (one cluster per 100 tenants), you end up managing 60+ EKS clusters, each with its own upgrade cycle, monitoring, and operational burden.

The root cause: Kubernetes namespaces are a **soft boundary**, not a hard isolation primitive. They share the same API server, the same etcd, and the same control plane. Under heavy multi-tenant load, one misbehaving tenant's watch connections or frequent pod churn can degrade API server response times for all tenants on the cluster.

---

## 2. The Four Approaches Compared

### Approach 1: Namespace per tenant (current plan)

Each tenant gets one or more Kubernetes namespaces on a shared EKS cluster. Isolation is enforced by NetworkPolicy and RBAC.

**Isolation:** Strong for network traffic (NetworkPolicy) and API access (RBAC), but tenants share the same API server and etcd.

**Scale:** ~100 tenants per cluster before etcd pressure becomes noticeable. Requires cluster sharding at larger scale.

**Cost:** Medium. Shared nodes are efficient but cluster management overhead multiplies with sharding.

**Best for:** Up to ~500 tenants total with a sharded cluster fleet.

**Problems at 2,000 tenants:**
- 6,000 namespaces across 60 clusters
- 60 EKS clusters to upgrade, monitor, and operate
- etcd watch connections multiply with namespace count

---

### Approach 2: Shared namespace with RBAC only

All tenants share a single namespace and a single Airflow deployment. Isolation relies entirely on Airflow's built-in RBAC.

**Isolation:** Weak. Airflow RBAC controls what users *see* in the UI, but all tenants share the same metadata database, the same Connections store, and the same DAG processor. A tenant can write a DAG that queries the metadata DB directly.

**Scale:** Very high — one cluster for all tenants.

**Cost:** Lowest possible — fewest total resources.

**Best for:** Internal platforms where all users are trusted (single organization). Not suitable for a commercial multi-tenant SaaS product.

**Why to avoid:** Astronomer themselves documented this problem — shared Airflow is a false sense of security. A single broken DAG from one tenant can fill the metadata DB, crash the scheduler, or expose another tenant's connection credentials.

---

### Approach 3: vCluster — virtual cluster per tenant ✅ RECOMMENDED

Each tenant gets a **virtual Kubernetes cluster** running inside a single namespace on the host EKS cluster. The virtual cluster runs its own API server (k3s), its own etcd, and its own control plane — all as pods inside one host namespace.

**Isolation:** Very strong. Each tenant has a completely independent Kubernetes API server. Tenants cannot see each other's resources even if they have direct `kubectl` access. The host cluster's etcd only tracks one namespace per tenant (the vCluster pod), not hundreds of Airflow resources.

**Scale:** ~300 vClusters per host EKS cluster comfortably. 2,000 tenants = ~7 host clusters.

**Cost:** Low-medium. Each vCluster control plane uses ~100–128MB RAM at idle. At 300 vClusters per cluster, that's ~38GB for control planes — affordable on modern instances.

**Best for:** Any scale above 100 tenants where you need real isolation without the cost of dedicated clusters.

---

### Approach 4: Dedicated EKS cluster per tenant

Each tenant gets their own fully independent EKS cluster.

**Isolation:** Maximum. Complete blast radius isolation — a cluster failure affects only one tenant.

**Scale:** Very low. One cluster per tenant means thousands of clusters at scale.

**Cost:** Highest. A minimal EKS cluster costs ~$150–200/month in base control plane and node costs before any workloads.

**Best for:** Enterprise or compliance-driven customers (HIPAA, FedRAMP, SOC2 Type II with strict isolation requirements). Offered as a premium upsell tier, not the default.

---

## 3. Recommendation: vCluster

**Use vCluster as the default isolation primitive for your platform.** It gives you cluster-level isolation at namespace-level density — the sweet spot for 2,000+ tenants.

Key reasons:

- Host EKS etcd only sees one namespace per tenant (the vCluster pod namespace), not hundreds of Airflow resources. This is the core fix for the namespace explosion problem.
- Each tenant has their own Kubernetes API server — no shared control plane contention.
- The Airflow deployment inside a vCluster is identical to any other Kubernetes deployment. No changes to your Helm charts, Helm values, or DAG delivery pipeline.
- vCluster is open source (Loft Labs, Apache 2.0). No vendor lock-in beyond the Kubernetes ecosystem.
- You can offer namespace isolation, vCluster isolation, and dedicated cluster isolation as three tiers — same control plane code, different provisioning path.

---

## 4. How vCluster Works

```
Host EKS cluster (your infrastructure)
│
├── namespace: vcluster-acme                  ← 1 host namespace per tenant
│   ├── pod: vcluster-acme                    ← k3s virtual API server + etcd
│   ├── pod: airflow-acme-scheduler           ← synced up from vCluster
│   ├── pod: airflow-acme-webserver           ← synced up from vCluster
│   └── pod: airflow-acme-worker-xxx          ← KubernetesExecutor task pod
│
├── namespace: vcluster-globex
│   ├── pod: vcluster-globex
│   ├── pod: airflow-globex-scheduler
│   └── pod: airflow-globex-worker-xxx
│
└── namespace: vcluster-initech
    ├── pod: vcluster-initech
    └── pod: airflow-initech-scheduler
```

**What the tenant sees (inside their vCluster):**

```
Virtual cluster: acme
│
├── namespace: airflow-prod
│   ├── deployment: airflow-scheduler (2 replicas)
│   ├── deployment: airflow-webserver (2 replicas)
│   └── pods: airflow-worker-* (KubernetesExecutor)
│
├── namespace: airflow-dev
│   └── deployment: airflow-scheduler (0 replicas, hibernated)
│
└── namespace: airflow-preprod
    └── deployment: airflow-scheduler (1 replica)
```

The tenant's Airflow workers see a full Kubernetes API. They can create pods, inspect namespaces, and manage resources — all within their virtual cluster. They have zero visibility into other tenants' virtual clusters or the host cluster.

**The key insight:** When an Airflow worker using KubernetesExecutor spawns a task pod, that pod is created in the virtual cluster's API server, then synced down to the host cluster by vCluster's syncer component. The host cluster sees it as a pod in `vcluster-acme` namespace. The tenant sees it as a pod in their `airflow-prod` namespace. Both are correct — it's the same pod viewed through two different API servers.

---

## 5. Provisioning a Tenant with vCluster

### Install vCluster CLI

```bash
brew install loft-sh/tap/vcluster
# or
curl -L -o vcluster "https://github.com/loft-sh/vcluster/releases/latest/download/vcluster-linux-amd64"
chmod +x vcluster && mv vcluster /usr/local/bin
```

### vCluster values file (`vcluster-values.yaml`)

```yaml
vcluster:
  image: rancher/k3s:v1.27.4-k3s1
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

# What to sync from the virtual cluster down to the host cluster
sync:
  ingresses:
    enabled: true          # sync ingress objects → host ALB ingress controller
  persistentvolumes:
    enabled: false         # Airflow uses S3, not PVs
  storageclasses:
    enabled: false
  hoststorageclasses:
    enabled: false

# Isolate vCluster from host cluster networking
isolation:
  enabled: true
  networkPolicy:
    enabled: true          # prevent cross-tenant pod traffic at host level
  podSecurityStandard: baseline

# Resource limits for the entire vCluster (enforced on host)
resourceQuota:
  enabled: true
  quota:
    requests.cpu: "20"
    requests.memory: 40Gi
    limits.cpu: "40"
    limits.memory: 80Gi
    pods: "200"
```

### Provision a new tenant

```bash
TENANT_ID="acme"
NAMESPACE="vcluster-${TENANT_ID}"

# 1. Create the host namespace
kubectl create namespace ${NAMESPACE}

# 2. Label it for your platform
kubectl label namespace ${NAMESPACE} \
  platform/tenant-id=${TENANT_ID} \
  platform/tier=standard

# 3. Create the vCluster (non-blocking — returns immediately)
vcluster create airflow-${TENANT_ID} \
  --namespace ${NAMESPACE} \
  --connect=false \
  --helm-values vcluster-values.yaml

# 4. Wait for vCluster to be ready
vcluster list | grep ${TENANT_ID}

# 5. Connect to the vCluster and deploy Airflow inside it
vcluster connect airflow-${TENANT_ID} --namespace ${NAMESPACE} -- \
  helm upgrade --install airflow apache-airflow/airflow \
    --namespace airflow-prod \
    --create-namespace \
    --values values-prod.yaml \
    --set "dags.gitSync.repo=git@github.com:your-org/${TENANT_ID}-dags.git" \
    --wait
```

### Airflow Helm values inside vCluster (`values-prod.yaml`)

These are unchanged from the namespace-per-tenant approach. The vCluster is transparent to Helm and Airflow.

```yaml
executor: KubernetesExecutor

scheduler:
  replicas: 2
  resources:
    requests:
      cpu: "1"
      memory: 2Gi

workers:
  replicas: 3
  autoscaling:
    enabled: true
    minReplicas: 3
    maxReplicas: 20

webserver:
  replicas: 2

postgresql:
  enabled: false

data:
  metadataConnection:
    host: "{tenant-aurora-endpoint}"
    db: "airflow_${TENANT_ID}_prod"
    passwordSecretName: airflow-db-secret

dags:
  gitSync:
    enabled: true
    branch: main
    period: 30s

config:
  core:
    load_examples: "False"
  logging:
    remote_logging: "True"
    remote_base_log_folder: "s3://platform-logs-${TENANT_ID}/prod"
```

### Access a tenant's vCluster (for debugging)

```bash
# Get a kubeconfig for a specific tenant's vCluster
vcluster connect airflow-acme --namespace vcluster-acme \
  --kube-config ./kubeconfig-acme.yaml

# Use it
export KUBECONFIG=./kubeconfig-acme.yaml
kubectl get pods -n airflow-prod
kubectl logs -n airflow-prod deployment/airflow-scheduler
```

---

## 6. Density at 2,000 Tenants

### Per host EKS cluster (standard tier)

```
300 vClusters per host cluster

Control plane overhead:
  300 vClusters × 128MB RAM   = ~38GB RAM (control planes only)
  300 vClusters × 100m CPU    = 30 vCPU   (control planes only)

Airflow workload (prod, active):
  300 tenants × 10 pods       = 3,000 pods baseline
  KubernetesExecutor workers  = burst on-demand, not always present

Recommended node group:
  15–20 × m5.4xlarge (16 vCPU / 64GB RAM)
  Plus a separate node group for KubernetesExecutor task pods (spot instances)

Host namespaces: 300 (one per vCluster)
Host etcd entries: ~300 vCluster pods + their synced workload pods
```

### Fleet for 2,000 tenants

```
Namespace-per-tenant approach:   2,000 tenants ÷ 100/cluster = 20 host clusters
vCluster approach:               2,000 tenants ÷ 300/cluster =  7 host clusters

Operational reduction: 65% fewer clusters to manage, upgrade, and monitor
```

### Hibernation still applies

Dev vClusters scaled to zero (scheduler + webserver at 0 replicas) consume ~128MB for the k3s control plane pod but zero worker nodes. At night with 70% of dev environments hibernated:

```
2,000 tenants with lazy provisioning:
  2,000 prod vClusters    (always on)
    600 dev vClusters     (active during business hours)
    400 preprod vClusters (active for testing)

Night time active pods:   ~2,000 × 10 = 20,000 pods (prod only)
Peak active pods:         ~3,000 × 10 = 30,000 pods (all envs)

Host clusters needed:     7 (standard) + 1–2 (premium) + N (enterprise dedicated)
```

---

## 7. Tiered Isolation Model

Offer three tiers with the same control plane, different provisioning paths:

### Standard tier — vCluster on shared host cluster

- ~300 tenants per host EKS cluster
- vCluster per tenant, full Kubernetes isolation
- Shared node pools (bin-packed for cost efficiency)
- Hibernation available for dev environments
- Target: startups, SMBs, teams with standard compliance needs

### Premium tier — vCluster on lightly-shared host cluster

- ~50 tenants per host EKS cluster
- More node headroom per tenant, fewer neighbors
- Dedicated node group per vCluster optional
- Priority scheduling, guaranteed minimum resources
- Target: mid-market customers with SLA requirements

### Enterprise tier — dedicated EKS cluster

- 1 EKS cluster per tenant
- Zero shared infrastructure
- Tenant chooses region, VPC CIDR, node types
- Full network isolation from all other tenants
- Target: financial services, healthcare, government

### Provisioner routing

```python
def provision_tenant(tenant_id, tier, region):
    if tier == "enterprise":
        # Provision a brand new EKS cluster
        cluster = provision_dedicated_eks_cluster(
            tenant_id=tenant_id,
            region=region
        )
        deploy_airflow_on_cluster(cluster, tenant_id)

    elif tier in ("standard", "premium"):
        # Find a host cluster with capacity
        max_density = 300 if tier == "standard" else 50
        host_cluster = cluster_registry.find_available(
            region=region,
            tier=tier,
            max_tenants=max_density
        )
        if not host_cluster:
            host_cluster = provision_new_host_cluster(region, tier)

        # Provision vCluster on that host cluster
        provision_vcluster(
            tenant_id=tenant_id,
            host_cluster=host_cluster
        )
        deploy_airflow_in_vcluster(tenant_id)

    # Record mapping
    tenant_registry.update(tenant_id, {
        "cluster_id": host_cluster.id,
        "tier": tier,
        "status": "ACTIVE"
    })
```

---

## 8. Provisioner Logic for All Tiers

### Full provisioning Step Functions state machine

```
VALIDATE_REQUEST
    ↓
FIND_OR_CREATE_HOST_CLUSTER         (standard/premium only)
    ↓
PROVISION_VCLUSTER                  (standard/premium)
    OR
PROVISION_EKS_CLUSTER               (enterprise)
    ↓
CREATE_S3_BUCKET
    ↓
CREATE_AURORA_DB
    ↓
CREATE_IAM_ROLE_IRSA
    ↓
DEPLOY_AIRFLOW_HELM
    ↓
CREATE_DNS_RECORD
    ↓
SETUP_HIBERNATION_CRONJOBS          (dev environment only)
    ↓
STORE_TENANT_RECORD
    ↓
NOTIFY_TENANT
    ↓
DONE
```

### vCluster provisioning step (Python / boto3 + subprocess)

```python
import subprocess
import boto3

def provision_vcluster(tenant_id: str, host_cluster: dict) -> dict:
    namespace = f"vcluster-{tenant_id}"

    # Update local kubeconfig to target the host cluster
    subprocess.run([
        "aws", "eks", "update-kubeconfig",
        "--name", host_cluster["cluster_name"],
        "--region", host_cluster["region"]
    ], check=True)

    # Create host namespace
    subprocess.run([
        "kubectl", "create", "namespace", namespace
    ], check=True)

    # Label the namespace
    subprocess.run([
        "kubectl", "label", "namespace", namespace,
        f"platform/tenant-id={tenant_id}",
        f"platform/tier={host_cluster['tier']}"
    ], check=True)

    # Install vCluster via Helm (vcluster CLI wraps Helm)
    subprocess.run([
        "vcluster", "create", f"airflow-{tenant_id}",
        "--namespace", namespace,
        "--connect=false",
        "--helm-values", "vcluster-values.yaml",
        "--wait"
    ], check=True)

    return {
        "vcluster_name": f"airflow-{tenant_id}",
        "host_namespace": namespace,
        "host_cluster_id": host_cluster["id"]
    }


def deploy_airflow_in_vcluster(tenant_id: str, environment: str, values_file: str):
    vcluster_name = f"airflow-{tenant_id}"
    host_namespace = f"vcluster-{tenant_id}"
    airflow_namespace = f"airflow-{environment}"

    subprocess.run([
        "vcluster", "connect", vcluster_name,
        "--namespace", host_namespace,
        "--",
        "helm", "upgrade", "--install", "airflow",
        "apache-airflow/airflow",
        "--namespace", airflow_namespace,
        "--create-namespace",
        "--values", values_file,
        "--set", f"dags.gitSync.repo=git@github.com:your-org/{tenant_id}-dags.git",
        "--wait", "--timeout", "10m"
    ], check=True)
```

### Teardown (tenant offboarding)

```python
def teardown_tenant(tenant_id: str, host_cluster: dict):
    namespace = f"vcluster-{tenant_id}"

    # Update kubeconfig to host cluster
    subprocess.run([
        "aws", "eks", "update-kubeconfig",
        "--name", host_cluster["cluster_name"],
        "--region", host_cluster["region"]
    ], check=True)

    # Delete the vCluster (removes all Airflow resources inside it too)
    subprocess.run([
        "vcluster", "delete", f"airflow-{tenant_id}",
        "--namespace", namespace,
        "--delete-namespace"       # also deletes the host namespace
    ], check=True)

    # Decrement tenant count in cluster registry
    cluster_registry.decrement(host_cluster["id"])

    # Delete S3 buckets, Aurora DB, IAM roles, DNS records
    cleanup_tenant_aws_resources(tenant_id)

    # Mark tenant as deleted in registry
    tenant_registry.update(tenant_id, {"status": "DELETED"})
```

---

## 9. Migration Path from Namespace-per-Tenant

If you have already built namespace-per-tenant and want to migrate to vCluster, here is the path with zero tenant downtime.

### Step 1 — Run both models in parallel

New tenants get provisioned as vClusters. Existing tenants stay on namespace-per-tenant for now. Your provisioner branches on whether the tenant has a `vcluster_id` in their registry record.

### Step 2 — Migrate existing tenants one at a time

For each existing tenant:

```bash
TENANT_ID="acme"

# 1. Provision a vCluster for this tenant on the same host cluster
vcluster create airflow-${TENANT_ID}-new \
  --namespace vcluster-${TENANT_ID} \
  --connect=false \
  --helm-values vcluster-values.yaml

# 2. Deploy Airflow inside the new vCluster
# Point it at the SAME Aurora DB and S3 bucket (no data migration needed)
vcluster connect airflow-${TENANT_ID}-new \
  --namespace vcluster-${TENANT_ID} -- \
  helm install airflow apache-airflow/airflow \
    --values values-prod.yaml \
    --namespace airflow-prod \
    --create-namespace

# 3. Pause DAG scheduling on old deployment (maintenance window)
kubectl scale deployment airflow-scheduler \
  --replicas=0 \
  -n airflow-${TENANT_ID}-prod

# 4. Switch DNS record to new vCluster webserver
# Update Route 53 CNAME: acme.airflow.yourplatform.com → new ALB

# 5. Verify tenant is happy on new vCluster

# 6. Delete old namespace
kubectl delete namespace airflow-${TENANT_ID}-prod
kubectl delete namespace airflow-${TENANT_ID}-dev
kubectl delete namespace airflow-${TENANT_ID}-preprod
```

### Step 3 — Decommission over-provisioned host clusters

Once all tenants on a host cluster are migrated to vClusters, that cluster can be resized down (fewer nodes) or decommissioned if tenants have been moved to a denser vCluster-ready cluster. Your cluster count drops significantly.

---

## Reference: vCluster resources

| Resource | URL |
|---|---|
| vCluster documentation | https://www.vcluster.com/docs |
| vCluster GitHub (Apache 2.0) | https://github.com/loft-sh/vcluster |
| Helm chart | https://charts.loft.sh |
| vCluster vs namespace comparison | https://www.vcluster.com/docs/vcluster/introduction/why-use-vcluster |

---

## Summary

| Question | Answer |
|---|---|
| Should I use namespace per tenant? | Only for < 100 tenants or as the isolation unit inside a vCluster |
| What should I use for 2,000 tenants? | vCluster per tenant on a fleet of host EKS clusters |
| How many host clusters do I need? | ~7 for 2,000 standard tenants (vs 20 with namespace-per-tenant) |
| Does this change my Airflow Helm charts? | No — Airflow deployment inside a vCluster is identical |
| What about enterprise customers? | Dedicated EKS cluster per tenant, offered as a premium tier |
| Is vCluster production-ready? | Yes — used by Loft Labs customers at thousands of vClusters in production |

---

*Strategy document v1.0 — multi-tenant isolation with vCluster for 2,000+ tenant Airflow platform on AWS EKS.*
