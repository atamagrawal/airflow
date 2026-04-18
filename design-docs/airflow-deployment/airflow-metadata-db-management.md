# Airflow Metadata DB Management — vCluster Multi-tenancy

> **Context:** When running 2,000+ tenants each with their own vCluster-isolated Airflow deployment, the metadata database strategy is one of the most critical infrastructure decisions. This document covers all approaches, the recommended architecture, provisioning logic, connection pooling, and a per-tier breakdown.

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [The Four Strategies](#2-the-four-strategies)
3. [Recommended Approach: Database per Tenant on Shared Aurora](#3-recommended-approach-database-per-tenant-on-shared-aurora)
4. [Provisioning a Tenant Database](#4-provisioning-a-tenant-database)
5. [Connecting Airflow Inside vCluster to Aurora](#5-connecting-airflow-inside-vcluster-to-aurora)
6. [Scaling: Aurora Cluster Registry](#6-scaling-aurora-cluster-registry)
7. [Connection Pooling with PgBouncer](#7-connection-pooling-with-pgbouncer)
8. [Dev Environments: In-Cluster PostgreSQL](#8-dev-environments-in-cluster-postgresql)
9. [Database Strategy by Tier](#9-database-strategy-by-tier)
10. [Teardown and Offboarding](#10-teardown-and-offboarding)
11. [Full Provisioning Summary](#11-full-provisioning-summary)

---

## 1. The Problem

Each Airflow deployment requires a **metadata database** to store:

- DAG run history and state
- Task instance records
- XCom values (data passed between tasks)
- Connections and Variables
- User and RBAC data
- Scheduler heartbeats and Job records

In a vCluster multi-tenant platform, each tenant has their own isolated Airflow deployment inside their own virtual cluster. The question is: **how do you provide each tenant with a metadata DB that is isolated, cost-efficient, and operationally manageable at 2,000+ tenant scale?**

The naive answer — one Aurora cluster per tenant — costs ~$50/month per tenant in base RDS charges alone, totalling $100,000/month at 2,000 tenants before any compute. This is not viable as a default.

The other extreme — one shared database for all tenants — defeats the entire isolation purpose of vCluster and reintroduces the cross-tenant data access problem that DAG ID prefixing also fails to solve.

The answer sits in the middle.

---

## 2. The Four Strategies

### Strategy 1: One Aurora cluster per tenant

One dedicated RDS Aurora PostgreSQL cluster per tenant. Complete blast radius isolation.

**Isolation:** Maximum. A cluster failure, a runaway query, or a corrupted metadata DB affects only one tenant.

**Cost:** Highest. Aurora minimum cost is ~$50–70/month per cluster (Multi-AZ, db.t4g.medium). At 2,000 tenants: ~$100,000–140,000/month in DB costs alone, before compute.

**Ops burden:** Very high. 2,000 Aurora clusters to monitor, patch, back up, and upgrade.

**Best for:** Enterprise tier only — customers with strict compliance requirements (HIPAA, PCI-DSS, FedRAMP) who require full infrastructure isolation and can pay a premium.

---

### Strategy 2: One database per tenant on a shared Aurora cluster ✅ RECOMMENDED

One Aurora PostgreSQL cluster shared across ~200 tenants. Each tenant gets their own PostgreSQL **database** (not schema, not table prefix — a full database) with a dedicated DB user that has zero access to other tenants' databases.

**Isolation:** Strong. PostgreSQL enforces hard boundaries between databases. A user connected to `airflow_acme_prod` cannot query `airflow_globex_prod` even with a direct SQL connection. Credentials are unique per tenant.

**Cost:** Low. One Aurora Multi-AZ cluster (~$200–400/month) shared across 200 tenants = ~$1–2/tenant/month for DB infrastructure.

**Ops burden:** Low. One cluster to manage, monitor, and upgrade. Tenant databases are created and destroyed programmatically.

**Best for:** Standard and premium tiers. The default for the vast majority of tenants.

---

### Strategy 3: One schema per tenant on a shared database

One Aurora cluster, one PostgreSQL database, but each tenant gets their own **schema** (namespace within the database). Airflow is pointed at `search_path=acme` so it reads and writes only to that schema's tables.

**Isolation:** Medium. PostgreSQL schema-level access control works, but a misconfigured `search_path` or a superuser connection bypasses it entirely. Harder to reason about than database-level isolation.

**Cost:** Lowest. One database for all tenants.

**Ops burden:** Medium. Schema migrations for Airflow version upgrades must be run per-schema — error-prone at scale.

**Practical problem:** Airflow's Alembic migrations are not designed for schema-per-tenant. Running `airflow db migrate` inside a vCluster will attempt to migrate the entire database, not just the tenant's schema. Significant custom patching required.

**Best for:** Cost-sensitive internal platforms or development environments only. Not recommended for production SaaS.

---

### Strategy 4: Aurora Serverless v2 — database per tenant on shared cluster

Same as Strategy 2, but the Aurora cluster uses **Serverless v2** capacity mode. Each database scales its ACU (Aurora Capacity Units) up and down based on actual query load, and can scale to a very low minimum (0.5 ACU) during idle periods.

**Isolation:** Strong — same as Strategy 2.

**Cost:** Low-to-medium. Better than provisioned Aurora for variable or bursty workloads. Dev and pre-prod databases that are idle most of the time benefit most. Prod databases with steady load may be cheaper on provisioned instances.

**Ops burden:** Low. Serverless v2 handles scaling automatically.

**Best for:** Pre-prod environments, dev environments that need a real Aurora DB (rather than in-cluster Postgres), and premium tier tenants who want Aurora-backed dev environments.

---

## 3. Recommended Approach: Database per Tenant on Shared Aurora

### Physical layout

```
Aurora PostgreSQL Cluster (shared, Multi-AZ, provisioned)
│
├── database: airflow_acme_prod          user: airflow_acme_prod
├── database: airflow_acme_preprod       user: airflow_acme_preprod
├── database: airflow_globex_prod        user: airflow_globex_prod
├── database: airflow_globex_preprod     user: airflow_globex_preprod
├── database: airflow_initech_prod       user: airflow_initech_prod
├── database: airflow_initech_preprod    user: airflow_initech_preprod
│   ... up to ~200 tenant databases per cluster
│
└── database: postgres                   (admin DB — never used by tenants)
```

Each tenant database:
- Has a **dedicated PostgreSQL user** with credentials unique to that tenant
- That user has `CONNECT` and full privileges on their own database only
- `PUBLIC` schema access is explicitly revoked
- No user can cross into another tenant's database

### Why database-level isolation and not schema-level

| Aspect | Database isolation | Schema isolation |
|---|---|---|
| Access control primitive | `GRANT CONNECT ON DATABASE` | `GRANT USAGE ON SCHEMA` |
| Cross-tenant query risk | Impossible without re-connecting | Possible via `SET search_path` |
| Airflow migration compatibility | Full — `airflow db migrate` just works | Requires patching Alembic |
| Credential scope | User tied to one database | User may access other schemas |
| Reasoning simplicity | Simple — one DB = one tenant | Complex — schema leakage possible |

Database-level isolation is the right choice. It is simpler, safer, and compatible with Airflow out of the box.

---

## 4. Provisioning a Tenant Database

### Step 1: Create the database and user (SQL)

Run as the platform admin user on the shared Aurora cluster:

```sql
-- 1. Create a dedicated user for this tenant environment
CREATE USER airflow_acme_prod WITH PASSWORD 'randomly-generated-32-char-password';

-- 2. Create the tenant's database owned by their user
CREATE DATABASE airflow_acme_prod OWNER airflow_acme_prod;

-- 3. Revoke default PUBLIC access (critical security step)
REVOKE ALL ON DATABASE airflow_acme_prod FROM PUBLIC;

-- 4. Grant only this tenant's user access to their database
GRANT CONNECT ON DATABASE airflow_acme_prod TO airflow_acme_prod;
GRANT ALL PRIVILEGES ON DATABASE airflow_acme_prod TO airflow_acme_prod;

-- 5. Connect to the tenant's DB and lock down the public schema
\c airflow_acme_prod
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO airflow_acme_prod;
```

### Step 2: Store credentials in Secrets Manager

```python
import psycopg2
import secrets
import boto3
import json

def provision_tenant_db(
    tenant_id: str,
    environment: str,
    aurora_cluster: dict
) -> dict:

    db_name = f"airflow_{tenant_id}_{environment}"
    db_user = f"airflow_{tenant_id}_{environment}"
    db_password = secrets.token_urlsafe(32)     # cryptographically random

    # Connect as platform admin to the shared Aurora cluster
    conn = psycopg2.connect(
        host=aurora_cluster["endpoint"],
        dbname="postgres",
        user="platform_admin",
        password=get_admin_password(aurora_cluster["secret_arn"])
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Create isolated user and database
    cur.execute(
        "CREATE USER %s WITH PASSWORD %s",
        (psycopg2.extensions.AsIs(db_user), db_password)
    )
    cur.execute(
        "CREATE DATABASE %s OWNER %s",
        (psycopg2.extensions.AsIs(db_name), psycopg2.extensions.AsIs(db_user))
    )
    cur.execute(
        "REVOKE ALL ON DATABASE %s FROM PUBLIC",
        (psycopg2.extensions.AsIs(db_name),)
    )
    cur.execute(
        "GRANT CONNECT ON DATABASE %s TO %s",
        (psycopg2.extensions.AsIs(db_name), psycopg2.extensions.AsIs(db_user))
    )
    cur.execute(
        "GRANT ALL PRIVILEGES ON DATABASE %s TO %s",
        (psycopg2.extensions.AsIs(db_name), psycopg2.extensions.AsIs(db_user))
    )

    # Lock down public schema inside the tenant's DB
    tenant_conn = psycopg2.connect(
        host=aurora_cluster["endpoint"],
        dbname=db_name,
        user="platform_admin",
        password=get_admin_password(aurora_cluster["secret_arn"])
    )
    tenant_conn.autocommit = True
    tenant_cur = tenant_conn.cursor()
    tenant_cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    tenant_cur.execute(
        "GRANT ALL ON SCHEMA public TO %s",
        (psycopg2.extensions.AsIs(db_user),)
    )
    tenant_cur.close()
    tenant_conn.close()

    cur.close()
    conn.close()

    # Store credentials in Secrets Manager
    secret_path = f"/tenants/{tenant_id}/{environment}/db"
    boto3.client("secretsmanager").create_secret(
        Name=secret_path,
        Description=f"Airflow metadata DB credentials for {tenant_id} {environment}",
        SecretString=json.dumps({
            "host": aurora_cluster["endpoint"],
            "dbname": db_name,
            "username": db_user,
            "password": db_password,
            "port": 5432,
            "aurora_cluster_id": aurora_cluster["id"]
        })
    )

    # Increment tenant count in DB cluster registry
    db_registry.increment(aurora_cluster["id"])

    return {
        "db_name": db_name,
        "secret_path": secret_path,
        "aurora_cluster_id": aurora_cluster["id"]
    }
```

### Step 3: Create Kubernetes Secret inside the vCluster

After provisioning the DB, your provisioner reads the credentials from Secrets Manager and creates a Kubernetes Secret inside the tenant's vCluster:

```python
import subprocess
import base64

def create_db_secret_in_vcluster(
    tenant_id: str,
    environment: str,
    secret_path: str
):
    # Read credentials from Secrets Manager
    sm = boto3.client("secretsmanager")
    secret = json.loads(
        sm.get_secret_value(SecretId=secret_path)["SecretString"]
    )

    # Base64-encode the password for the Kubernetes Secret
    password_b64 = base64.b64encode(
        secret["password"].encode()
    ).decode()

    # Apply the Kubernetes Secret inside the vCluster
    k8s_secret = f"""
apiVersion: v1
kind: Secret
metadata:
  name: airflow-metadata-db-secret
  namespace: airflow-{environment}
type: Opaque
data:
  password: {password_b64}
"""
    subprocess.run([
        "vcluster", "connect", f"airflow-{tenant_id}",
        "--namespace", f"vcluster-{tenant_id}",
        "--",
        "kubectl", "apply", "-f", "-"
    ], input=k8s_secret.encode(), check=True)
```

---

## 5. Connecting Airflow Inside vCluster to Aurora

### Helm values configuration

```yaml
# values-prod.yaml — inside the tenant's vCluster
postgresql:
  enabled: false                    # disable in-cluster postgres for prod

data:
  metadataConnection:
    protocol: postgresql
    host: "pgbouncer.platform-infra.svc.cluster.local"   # via PgBouncer (see section 7)
    port: 5432
    db: "airflow_acme_prod"
    user: "airflow_acme_prod"
    passwordSecretName: airflow-metadata-db-secret        # k8s Secret created above
    passwordSecretKey: password
    sslmode: require                # always use SSL to Aurora

  resultBackendConnection:          # Celery result backend (if using CeleryExecutor)
    protocol: postgresql
    host: "pgbouncer.platform-infra.svc.cluster.local"
    port: 5432
    db: "airflow_acme_prod"
    user: "airflow_acme_prod"
    passwordSecretName: airflow-metadata-db-secret
    passwordSecretKey: password

# KubernetesExecutor does not need a result backend
executor: KubernetesExecutor
```

### Airflow DB initialisation

When Airflow starts for the first time in a new vCluster, it needs to initialise the database schema. This happens automatically via the `airflow db migrate` init container in the Helm chart:

```yaml
# The Helm chart runs this as an init job before starting the scheduler
# No extra configuration needed — Airflow handles schema creation automatically
# on first connection to the (empty) tenant database
migrateDatabaseJob:
  enabled: true
  jobAnnotations:
    "helm.sh/hook": post-install,post-upgrade
    "helm.sh/hook-weight": "1"
```

---

## 6. Scaling: Aurora Cluster Registry

At 2,000 tenants × 3 environments, you have up to 6,000 databases. PostgreSQL on Aurora handles up to ~200 databases per cluster comfortably before connection management overhead increases. You need a DB cluster registry in DynamoDB to track which Aurora cluster has capacity.

### DynamoDB table: `platform-db-clusters`

```
ClusterID          | Region    | Endpoint                              | Tier     | DBCount | MaxDBs | Status
aurora-use1-01     | us-east-1 | aurora-use1-01.xxx.rds.amazonaws.com  | standard | 187     | 200    | NEAR_FULL
aurora-use1-02     | us-east-1 | aurora-use1-02.xxx.rds.amazonaws.com  | standard | 94      | 200    | AVAILABLE
aurora-euw1-01     | eu-west-1 | aurora-euw1-01.xxx.rds.amazonaws.com  | standard | 12      | 200    | AVAILABLE
aurora-use1-ent-01 | us-east-1 | aurora-ent-01.xxx.rds.amazonaws.com   | enterprise| 1      | 1      | FULL
```

### DynamoDB table: `platform-tenant-db-mapping`

```
TenantID  | Environment | ClusterID      | DBName                  | SecretPath
acme      | prod        | aurora-use1-02 | airflow_acme_prod       | /tenants/acme/prod/db
acme      | preprod     | aurora-use1-02 | airflow_acme_preprod    | /tenants/acme/preprod/db
globex    | prod        | aurora-use1-01 | airflow_globex_prod     | /tenants/globex/prod/db
```

### Provisioner: find or create an Aurora cluster

```python
def find_aurora_cluster(region: str, tier: str) -> dict:
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("platform-db-clusters")

    # Query for an available cluster in the target region and tier
    response = table.scan(
        FilterExpression=Attr("region").eq(region)
            & Attr("tier").eq(tier)
            & Attr("status").eq("AVAILABLE")
            & Attr("db_count").lt(Attr("max_dbs"))
    )

    clusters = sorted(
        response["Items"],
        key=lambda c: c["db_count"]     # fill least-loaded cluster first
    )

    if clusters:
        return clusters[0]

    # No available cluster — provision a new one
    return provision_new_aurora_cluster(region=region, tier=tier)


def provision_new_aurora_cluster(region: str, tier: str) -> dict:
    # Call Terraform or CDK to create a new Aurora cluster
    # This is async — takes 5–10 minutes
    cluster_id = f"aurora-{region.replace('-', '')[:8]}-{secrets.token_hex(3)}"

    # Trigger Terraform via Step Functions sub-workflow
    sfn = boto3.client("stepfunctions")
    sfn.start_execution(
        stateMachineArn=AURORA_PROVISION_STATE_MACHINE_ARN,
        input=json.dumps({
            "cluster_id": cluster_id,
            "region": region,
            "tier": tier,
            "instance_class": "db.r6g.large" if tier == "standard" else "db.r6g.2xlarge",
            "multi_az": True
        })
    )

    # Register the cluster (status: PROVISIONING until ready)
    db_registry.put_item(Item={
        "cluster_id": cluster_id,
        "region": region,
        "tier": tier,
        "db_count": 0,
        "max_dbs": 200,
        "status": "PROVISIONING"
    })

    return wait_for_cluster_ready(cluster_id)
```

---

## 7. Connection Pooling with PgBouncer

### The problem

Each Airflow deployment opens multiple database connections:

- Scheduler: 5–10 connections (multiple threads)
- Webserver: 2–5 connections per gunicorn worker
- Workers: 1–2 connections per task (KubernetesExecutor)

At 200 tenants per Aurora cluster:
```
200 tenants × (10 scheduler + 5 webserver) connections = 3,000 persistent connections

Aurora db.r6g.large max connections: ~1,000
Without pooling: cluster is immediately overwhelmed
```

**The fix:** PgBouncer sits between Airflow and Aurora, maintaining a small pool of real Aurora connections and multiplexing thousands of client connections through them.

### PgBouncer deployment (one per Aurora cluster)

```yaml
# pgbouncer-deployment.yaml
# Deploy in the platform-infra namespace on the host EKS cluster
# Accessible from all tenant vClusters via the host cluster's service mesh
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pgbouncer-aurora-use1-02
  namespace: platform-infra
spec:
  replicas: 2                        # HA — two PgBouncer instances
  selector:
    matchLabels:
      app: pgbouncer
      cluster: aurora-use1-02
  template:
    metadata:
      labels:
        app: pgbouncer
        cluster: aurora-use1-02
    spec:
      containers:
      - name: pgbouncer
        image: bitnami/pgbouncer:1.21.0
        ports:
        - containerPort: 5432
        env:
        - name: POSTGRESQL_HOST
          value: "aurora-use1-02.cluster-xxx.us-east-1.rds.amazonaws.com"
        - name: POSTGRESQL_PORT
          value: "5432"
        - name: PGBOUNCER_POOL_MODE
          value: "transaction"        # transaction mode: best for Airflow
        - name: PGBOUNCER_MAX_CLIENT_CONN
          value: "10000"              # accept up to 10k client connections
        - name: PGBOUNCER_DEFAULT_POOL_SIZE
          value: "10"                 # 10 real Aurora connections per database
        - name: PGBOUNCER_MIN_POOL_SIZE
          value: "2"                  # keep 2 connections warm per database
        - name: PGBOUNCER_SERVER_IDLE_TIMEOUT
          value: "600"                # release idle Aurora connections after 10m
        - name: PGBOUNCER_AUTH_TYPE
          value: "scram-sha-256"
        resources:
          requests:
            cpu: 500m
            memory: 256Mi
          limits:
            cpu: "2"
            memory: 512Mi
---
apiVersion: v1
kind: Service
metadata:
  name: pgbouncer-aurora-use1-02
  namespace: platform-infra
spec:
  selector:
    app: pgbouncer
    cluster: aurora-use1-02
  ports:
  - port: 5432
    targetPort: 5432
  type: ClusterIP
```

### Airflow connection through PgBouncer

```yaml
# Airflow Helm values — point at PgBouncer service, not Aurora directly
data:
  metadataConnection:
    host: "pgbouncer-aurora-use1-02.platform-infra.svc.cluster.local"
    port: 5432
    db: "airflow_acme_prod"
    user: "airflow_acme_prod"
    passwordSecretName: airflow-metadata-db-secret
    passwordSecretKey: password
```

### Connection math with PgBouncer

```
200 tenants × 15 client connections each = 3,000 client connections to PgBouncer

PgBouncer maintains:
  200 databases × 10 real connections each = 2,000 real Aurora connections
  (well within Aurora db.r6g.large limit of 2,000 max_connections)

Net result:
  Airflow sees fast connections (PgBouncer is local on the cluster)
  Aurora sees a stable, bounded pool
  No connection exhaustion at any scale
```

### Important: Airflow + PgBouncer in transaction mode

Airflow uses SQLAlchemy which may use `SAVEPOINT` in transaction mode. Configure Airflow to disable server-side cursors and savepoints when using PgBouncer:

```yaml
config:
  core:
    sql_alchemy_pool_pre_ping: "True"
  database:
    sql_alchemy_pool_size: "5"
    sql_alchemy_max_overflow: "10"
    sql_alchemy_pool_recycle: "1800"
    # Disable savepoints for PgBouncer transaction mode compatibility
    sql_alchemy_connect_args: '{"options": "-c statement_timeout=30000"}'
```

---

## 8. Dev Environments: In-Cluster PostgreSQL

For dev vClusters that hibernate at night, an Aurora database wastes money even at $1–2/month — multiplied by 2,000 tenants that is still significant, and many dev environments are idle 70% of the time.

**Use the in-cluster PostgreSQL that ships with the Airflow Helm chart for all dev environments.**

```yaml
# values-dev.yaml — in-cluster postgres, no Aurora
postgresql:
  enabled: true
  auth:
    username: airflow
    password: airflow              # non-sensitive — dev only, not internet-exposed
    database: airflow
  primary:
    persistence:
      enabled: true
      size: 5Gi                    # small — dev workloads are light
    resources:
      requests:
        cpu: 250m
        memory: 256Mi
      limits:
        cpu: "1"
        memory: 512Mi

# No data.metadataConnection needed — Helm chart wires it automatically
```

**Trade-offs of in-cluster postgres for dev:**

| Aspect | In-cluster postgres | Aurora |
|---|---|---|
| Cost when hibernated | $0 (pod scales to 0) | $1–2/month minimum |
| HA | No (single pod) | Yes (Multi-AZ) |
| Data persistence | PVC-backed (survives restarts) | Fully durable |
| Data loss risk | PVC deletion = data loss | None |
| Setup complexity | Zero (Helm handles it) | Requires provisioner step |
| Acceptable for dev | Yes | Overkill |
| Acceptable for prod | No | Yes |

When a dev vCluster hibernates (scheduler + webserver scaled to 0), the in-cluster postgres pod also scales to 0 automatically because it is managed by the same Helm release. Zero cost. Zero Aurora databases needed for dev environments.

---

## 9. Database Strategy by Tier

| Environment | DB approach | Aurora cluster type | Cost per tenant/mo | Notes |
|---|---|---|---|---|
| Dev | In-cluster PostgreSQL | None | $0 (hibernates) | Helm chart built-in |
| Pre-prod | DB per tenant on shared Aurora Serverless v2 | Serverless v2 | ~$0.50–2 | Scales to near-zero when idle |
| Prod (standard) | DB per tenant on shared Aurora Multi-AZ | Provisioned | ~$1–2 | Shared across 200 tenants |
| Prod (premium) | DB per tenant on lightly-shared Aurora | Provisioned (larger) | ~$3–5 | Fewer tenants per cluster |
| Prod (enterprise) | Dedicated Aurora cluster | Provisioned (dedicated) | ~$50–150 | Full isolation, compliance |

### Aurora instance sizing by tier

```
Standard tier Aurora cluster (200 tenants):
  Instance: db.r6g.large (2 vCPU, 16GB RAM)
  Multi-AZ: Yes (primary + 1 replica)
  Max connections: ~2,000
  With PgBouncer: supports 200 tenants × 15 connections each comfortably
  Monthly cost: ~$300–400 / 200 tenants = ~$1.50–2/tenant

Premium tier Aurora cluster (50 tenants):
  Instance: db.r6g.xlarge (4 vCPU, 32GB RAM)
  Multi-AZ: Yes + 1 read replica
  Monthly cost: ~$600–800 / 50 tenants = ~$12–16/tenant

Enterprise tier Aurora cluster (1 tenant):
  Instance: db.r6g.2xlarge (8 vCPU, 64GB RAM)
  Multi-AZ: Yes + 2 read replicas
  Monthly cost: ~$1,200–1,600 / 1 tenant = ~$1,200–1,600/tenant
```

---

## 10. Teardown and Offboarding

When a tenant leaves or an environment is deleted, clean up in reverse order:

```python
def teardown_tenant_db(tenant_id: str, environment: str) -> None:
    # Get the tenant's DB mapping
    mapping = db_registry.get_tenant_mapping(tenant_id, environment)
    aurora_cluster = db_registry.get_cluster(mapping["cluster_id"])

    db_name = f"airflow_{tenant_id}_{environment}"
    db_user = f"airflow_{tenant_id}_{environment}"

    # Connect as platform admin
    conn = psycopg2.connect(
        host=aurora_cluster["endpoint"],
        dbname="postgres",
        user="platform_admin",
        password=get_admin_password(aurora_cluster["secret_arn"])
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Terminate active connections to this database before dropping
    cur.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = %s AND pid <> pg_backend_pid()
    """, (db_name,))

    # Drop the database and user
    cur.execute(
        "DROP DATABASE IF EXISTS %s",
        (psycopg2.extensions.AsIs(db_name),)
    )
    cur.execute(
        "DROP USER IF EXISTS %s",
        (psycopg2.extensions.AsIs(db_user),)
    )

    cur.close()
    conn.close()

    # Delete credentials from Secrets Manager
    boto3.client("secretsmanager").delete_secret(
        SecretId=f"/tenants/{tenant_id}/{environment}/db",
        ForceDeleteWithoutRecovery=True
    )

    # Decrement tenant count in DB cluster registry
    db_registry.decrement(aurora_cluster["id"])

    # Remove tenant DB mapping record
    db_registry.delete_tenant_mapping(tenant_id, environment)
```

### Important: export data before teardown

Always offer (and for enterprise, require) a data export before dropping the database:

```python
def export_tenant_db(tenant_id: str, environment: str, s3_bucket: str) -> str:
    db_name = f"airflow_{tenant_id}_{environment}"
    export_key = f"exports/{tenant_id}/{environment}/{datetime.now().isoformat()}.sql.gz"

    # pg_dump to S3 via a Kubernetes Job
    subprocess.run([
        "kubectl", "run", f"export-{tenant_id}",
        "--image=postgres:15",
        "--restart=Never",
        "--env", f"PGPASSWORD={get_admin_password()}",
        "--",
        "sh", "-c",
        f"pg_dump -h {aurora_endpoint} -U platform_admin {db_name} "
        f"| gzip | aws s3 cp - s3://{s3_bucket}/{export_key}"
    ], check=True)

    return f"s3://{s3_bucket}/{export_key}"
```

---

## 11. Full Provisioning Summary

When a new tenant `acme` is onboarded, your Step Functions state machine creates the following:

### EKS / vCluster side

```
Host EKS cluster
└── namespace: vcluster-acme
    └── vCluster: airflow-acme (k3s)
        ├── namespace: airflow-prod
        │   ├── Secret: airflow-metadata-db-secret   ← DB password
        │   └── Helm: apache-airflow (prod values)
        ├── namespace: airflow-preprod
        │   ├── Secret: airflow-metadata-db-secret
        │   └── Helm: apache-airflow (preprod values)
        └── namespace: airflow-dev
            └── Helm: apache-airflow (dev values, in-cluster postgres)
```

### Database side

```
Aurora cluster: aurora-use1-02 (shared, standard tier)
├── database: airflow_acme_prod       user: airflow_acme_prod
└── database: airflow_acme_preprod    user: airflow_acme_preprod

In-cluster postgres (dev):
└── runs as a pod inside the airflow-dev namespace of the vCluster
    no Aurora entry — zero cost when hibernated
```

### Secrets Manager

```
/tenants/acme/prod/db      → { host, dbname, username, password, port, cluster_id }
/tenants/acme/preprod/db   → { host, dbname, username, password, port, cluster_id }
/tenants/acme/dev/db       → not created (in-cluster postgres needs no secret)
```

### DynamoDB registry updates

```
platform-db-clusters:
  aurora-use1-02.db_count: 187 → 189   (prod + preprod)

platform-tenant-db-mapping:
  acme / prod    → { cluster: aurora-use1-02, db: airflow_acme_prod, ... }
  acme / preprod → { cluster: aurora-use1-02, db: airflow_acme_preprod, ... }
```

---

## Key Takeaways

- Use **one database per tenant on a shared Aurora cluster** as the default for prod and preprod. It gives full data isolation at ~$1–2/tenant/month in DB costs.
- Use **in-cluster PostgreSQL** for dev environments. It hibernates to zero cost with the rest of the dev vCluster.
- Use **PgBouncer** in transaction mode between Airflow and Aurora. Without it, connection exhaustion will occur well before 100 tenants.
- Track Aurora clusters in a **DB cluster registry** (DynamoDB). Your provisioner places new tenant databases on the cluster with the most available capacity.
- Store all credentials in **AWS Secrets Manager**, namespaced per tenant and environment. Never hardcode or pass them through environment variables in plain text.
- For **enterprise tier**, provision a dedicated Aurora cluster per tenant — same provisioner logic, different branch.

---

*DB management document v1.0 — Airflow metadata database strategy for vCluster-based multi-tenant platform on AWS.*
