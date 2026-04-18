# Airflow Metadata Database — Complete Analysis

> **Scope:** This document consolidates all database decisions for a multi-tenant managed Airflow platform (2,000+ tenants, vCluster-based isolation on AWS). It covers three questions in order:
> 1. Can we use a single shared database for all tenants?
> 2. Can we use one database with one schema per tenant?
> 3. What database engines can we use at all?

---

## Table of Contents

1. [Option A — Single Shared Database (All Tenants, Shared Tables)](#option-a--single-shared-database-all-tenants-shared-tables)
2. [Option B — Single Database, One Schema Per Tenant](#option-b--single-database-one-schema-per-tenant)
3. [Option C — One Database Per Tenant on Shared Cluster](#option-c--one-database-per-tenant-on-shared-cluster-recommended)
4. [Side-by-Side Comparison: A vs B vs C](#side-by-side-comparison-a-vs-b-vs-c)
5. [Database Engine Options](#database-engine-options)
6. [Final Decision Matrix](#final-decision-matrix)

---

## Option A — Single Shared Database (All Tenants, Shared Tables)

### What it looks like

```
Aurora PostgreSQL Cluster
└── database: airflow_platform          ← one database for ALL tenants

    Tables (Airflow schema — all tenants write here):
    ├── dag                             ← mixed: all tenants' DAGs
    ├── dag_run                         ← mixed: all tenants' run history
    ├── task_instance                   ← mixed: all tenants' task records
    ├── xcom                            ← mixed: all tenants' XCom values
    ├── connection                      ← mixed: all tenants' credentials
    └── variable                        ← mixed: all tenants' variables
```

All 2,000 tenants' Airflow deployments — each running in their own isolated vCluster — connect to the same database and write to the same tables. The vCluster gives Kubernetes isolation. But the moment all Airflow instances share one database, that isolation is meaningless at the data layer.

---

### Pros

| Benefit | Description |
|---|---|
| Lowest infrastructure cost | One Aurora cluster, one database — nothing else |
| Zero provisioning complexity | No `CREATE DATABASE` per tenant — just deploy and connect |
| Simple monitoring | One database to watch in CloudWatch / Performance Insights |
| Easy backups | One pg_dump covers everything |

---

### Cons and failure modes

#### 1. No data isolation — critical

Airflow does not add a `tenant_id` column to its internal tables. There is no row-level security. Any tenant's Airflow scheduler can read any other tenant's data using standard Airflow Python APIs:

```python
# Tenant A's DAG — standard Airflow ORM, no hacks needed
from airflow.models import DagRun, Connection, Variable, XCom
from airflow import settings

session = settings.Session()

# Reads ALL tenants' dag run history
all_runs = session.query(DagRun).all()

# Reads ALL tenants' XCom — may contain sensitive pipeline payloads
all_xcom = session.query(XCom).all()

# Reads ALL tenants' Connections — contains DB passwords, API keys
all_conns = session.query(Connection).all()

# Reads ALL tenants' Variables — may contain secrets
all_vars = session.query(Variable).all()
```

The DAG ID prefix (e.g. `acme__daily_etl`) does not prevent this. The ORM does not filter by prefix. This is not a theoretical risk — it requires no special knowledge to exploit.

#### 2. Fernet key must be platform-wide — critical

Airflow encrypts Connections and Variables using a Fernet key (`AIRFLOW__CORE__FERNET_KEY`). With a shared database, every tenant must use the **same Fernet key** — because the encrypted values in the `connection` table must be decryptable by whoever reads them.

```
Tenant A's Airflow: FERNET_KEY = key_A  →  encrypts with key_A
Tenant B's Airflow: FERNET_KEY = key_B  →  tries to decrypt key_A's values
→ fails — Tenant B cannot read any connection at all

Forced solution: all tenants use the same FERNET_KEY
→ if that key leaks, ALL tenants' credentials are compromised simultaneously
```

#### 3. One bad migration breaks all tenants — critical

When any tenant upgrades Airflow and `airflow db migrate` runs, it acquires locks on shared tables. All other tenants' schedulers stall during the migration.

```
Tenant A upgrades Airflow 2.6 → 2.7
→ airflow db migrate runs Alembic migration
→ acquires ALTER TABLE lock on dag_run
→ Tenant B's scheduler cannot write task state for 30–120 seconds
→ Tenant C's webserver times out on DAG run queries
→ 2,000 tenants degraded during every single upgrade of any one tenant
```

#### 4. Noisy neighbor — high

At 2,000 tenants, the `dag_run` table alone contains hundreds of millions of rows. One tenant running a heavy query (e.g. loading 2 years of history in the UI) causes a full table scan that degrades Aurora CPU for all tenants simultaneously.

```sql
-- Tenant A's analyst loads run history — no LIMIT, returns 50,000 rows
SELECT * FROM dag_run WHERE dag_id = 'acme__heavy_dag' ORDER BY execution_date;
-- Full table scan on a 200M-row table
-- Aurora CPU spikes
-- All 2,000 tenants' schedulers slow waiting for DB responses
```

#### 5. Scheduler lock contention — high

With 2,000 Airflow schedulers all connected to the same database, the scheduler heartbeat (`job` table) has 2,000 active rows updated every 5 seconds:

```
2,000 schedulers × heartbeat every 5 seconds
= 400 UPDATE statements per second on the job table
= constant row-level lock contention
= heartbeat delays → tasks appear zombied → Airflow kills and retries them
```

#### 6. No per-tenant backup or restore

A single tenant's accidental data deletion cannot be recovered without restoring the entire database to a point-in-time — which rolls back all 2,000 tenants simultaneously.

---

### Verdict: Do not use

Saves zero dollars compared to Option C (same Aurora cluster cost either way). Destroys all isolation that vCluster was meant to provide. Fails on every security and reliability dimension for a commercial SaaS platform.

**Acceptable only for:** Internal platforms with < 20 trusted teams who do not need data isolation between them.

---

## Option B — Single Database, One Schema Per Tenant

### What it looks like

```
Aurora PostgreSQL Cluster
└── database: airflow_platform          ← one database

    ├── schema: acme                    ← Tenant A's tables
    │   ├── dag
    │   ├── dag_run
    │   ├── task_instance
    │   ├── xcom
    │   └── connection
    │
    ├── schema: globex                  ← Tenant B's tables
    │   ├── dag
    │   ├── dag_run
    │   └── ...
    │
    └── schema: initech                 ← Tenant C's tables
        └── ...
```

Each tenant gets their own PostgreSQL schema. Airflow is directed to that schema via `search_path` set on the tenant's DB role. Physically separate table sets — one database.

---

### How it works technically

```sql
-- Per-tenant setup
CREATE SCHEMA acme;
CREATE USER airflow_acme WITH PASSWORD 'xxx';
ALTER ROLE airflow_acme SET search_path = acme, extensions;
GRANT USAGE, CREATE ON SCHEMA acme TO airflow_acme;
REVOKE ALL ON SCHEMA acme FROM PUBLIC;

-- Tenant B cannot access acme schema (must be explicitly granted)
REVOKE ALL ON SCHEMA acme FROM airflow_globex;
```

Airflow's connection URI is unchanged:
```
postgresql+psycopg2://airflow_acme:xxx@host/airflow_platform
```

When Airflow connects as `airflow_acme`, PostgreSQL sets `search_path = acme` automatically for every session. Airflow writes to `acme.dag_run`, `acme.task_instance` etc without knowing it is in a schema.

---

### Pros

| Benefit | Description |
|---|---|
| Physically separate tables | Each tenant has their own table set — not mixed rows |
| One cluster to manage | Simpler than managing many Aurora clusters |
| Fernet key per tenant | Each tenant can have their own Fernet key (different DB user) |
| Marginally cheaper | One fewer `CREATE DATABASE` call — though cost difference is zero |
| Easier single-cluster monitoring | One database in Performance Insights |

---

### Cons and failure modes

#### 1. Incompatible with PgBouncer transaction mode — critical

This is the single biggest blocker. You need PgBouncer at scale (2,000 tenants → thousands of connections). PgBouncer in **transaction mode** is required for Airflow. But `search_path` is a **session variable** — it is reset between transactions in transaction mode.

```
Session mode (works with search_path):
  Each client owns a real DB connection for its full lifetime
  → search_path is stable per session → schemas work correctly
  → BUT: no connection multiplexing
  → 200 tenants × 15 connections = 3,000 real Aurora connections
  → Aurora db.r6g.large limit: 2,000 connections → instant exhaustion

Transaction mode (required for scale):
  A real DB connection is borrowed per transaction, returned after
  → search_path is reset on every borrow (new session context)
  → Airflow lands in the wrong schema silently
  → Tenant A's scheduler writes to Tenant B's dag_run table
  → Silent data corruption — no error thrown
```

You cannot have both PgBouncer transaction mode and schema isolation. At 2,000 tenants, you absolutely need PgBouncer. This alone rules out schema-per-tenant for your platform.

#### 2. Airflow migrations are not schema-aware — high

Airflow's Alembic migration runner connects to the database and runs migrations against the role's `search_path`. Running `airflow db migrate` for 2,000 tenants requires 2,000 separate migration runs, each connecting as a different role. The Airflow Helm chart has no built-in mechanism for this. You must build custom per-tenant migration init containers for every Airflow upgrade.

```python
# Custom migration job you must build and maintain
for tenant in get_all_tenants():
    run_migration_job(
        namespace=f"airflow-{tenant.id}",
        db_user=f"airflow_{tenant.id}",
        db_name="airflow_platform",    # same DB every time
        search_path=tenant.id
    )
# Failure in any one migration must be caught and retried independently
# A bug in this code could migrate the wrong tenant's schema
```

#### 3. Cross-tenant access still possible — high

Schema isolation requires explicit `REVOKE` on every schema for every other tenant's role. You must maintain this grant/revoke matrix for every new tenant added:

```sql
-- When Tenant D joins, you must revoke their access from all 1,999 existing schemas
-- AND revoke all existing tenants from Tenant D's schema
-- At 2,000 tenants: 2,000 × 2,000 = 4,000,000 potential grant combinations to manage
```

A user with `CONNECT` on the database can still access other schemas using fully qualified names if the REVOKE was missed:

```sql
-- Tenant A's Airflow user — if REVOKE was missed for any schema:
SELECT * FROM globex.connection;     -- Tenant B's credentials accessible
SELECT * FROM initech.xcom;          -- Tenant C's XCom data accessible
```

Database-level isolation is enforced by PostgreSQL automatically with no maintenance burden. Schema-level isolation requires you to maintain it manually forever.

#### 4. PostgreSQL extension conflicts — medium

Airflow uses PostgreSQL extensions like `pgcrypto`. Extensions normally live in the `public` schema. With schema-per-tenant, `public` is no longer in `search_path`. You must create a shared `extensions` schema and include it in every tenant's `search_path`:

```sql
CREATE SCHEMA extensions;
CREATE EXTENSION pgcrypto SCHEMA extensions;
ALTER ROLE airflow_acme SET search_path = acme, extensions;
```

On Aurora PostgreSQL, extension management is restricted — not all extensions can be moved freely. Aurora does not support `CREATE EXTENSION` in arbitrary schemas for all extensions.

#### 5. Table bloat and vacuum pressure — medium

At 2,000 tenants × ~30 Airflow tables per schema = 60,000 tables in one database. PostgreSQL's autovacuum processes all 60,000 tables. Background vacuum workers spend significant time cycling through catalog entries, slowing overall DB responsiveness.

---

### Verdict: Not recommended for production SaaS at scale

The PgBouncer incompatibility alone is disqualifying at 2,000 tenants. The custom migration complexity adds significant ongoing engineering burden for zero cost savings over Option C. Schema-per-tenant belongs in small internal platforms or as a pattern for web applications (Rails, Django) — not for Airflow at scale.

**Acceptable only for:** Small internal platforms (< 50 tenants) with no connection pooling requirement, operated by a team comfortable managing the REVOKE matrix manually.

---

## Option C — One Database Per Tenant on Shared Cluster ✅ RECOMMENDED

### What it looks like

```
Aurora PostgreSQL Cluster (shared, Multi-AZ)
│
├── database: airflow_acme_prod        user: airflow_acme_prod    (isolated creds)
├── database: airflow_acme_preprod     user: airflow_acme_preprod
├── database: airflow_globex_prod      user: airflow_globex_prod
├── database: airflow_globex_preprod   user: airflow_globex_preprod
├── database: airflow_initech_prod     user: airflow_initech_prod
└── ... up to ~200 databases per cluster
```

Each tenant gets a completely separate PostgreSQL database with a dedicated user. The Aurora cluster is shared — you pay for one cluster, not one per tenant.

---

### Pros

| Benefit | Description |
|---|---|
| Full data isolation | PostgreSQL enforces hard boundaries between databases — automatic, no maintenance |
| Per-tenant Fernet key | Each DB user is separate — each tenant has their own encryption key |
| PgBouncer compatible | Transaction mode works perfectly — database boundary is not a session variable |
| Airflow migrations just work | `airflow db migrate` runs against one database, no custom logic needed |
| Per-tenant backup/restore | Point-in-time restore of one tenant's DB without touching others |
| Shared cluster cost | ~$1.50–2/tenant/month — same cost as schema or single DB approach |
| No grant/revoke matrix | PostgreSQL enforces DB-level access automatically |
| Simple provisioning | `CREATE DATABASE` + `CREATE USER` + `GRANT CONNECT` — 4 SQL statements |

---

### Cons

| Concern | Reality |
|---|---|
| More databases to track | Solved by DB cluster registry in DynamoDB — automated |
| ~200 DB limit per cluster | Solved by adding more Aurora clusters via cluster registry |
| Slightly more SQL at provision | 4 SQL statements per tenant — trivial to automate |

---

### Provisioning (4 SQL statements)

```sql
CREATE USER airflow_acme_prod WITH PASSWORD 'random-32-char-secret';
CREATE DATABASE airflow_acme_prod OWNER airflow_acme_prod;
REVOKE ALL ON DATABASE airflow_acme_prod FROM PUBLIC;
GRANT CONNECT ON DATABASE airflow_acme_prod TO airflow_acme_prod;
```

That is the entire isolation setup. PostgreSQL enforces the rest automatically.

---

### Verdict: Use this

Same cost as Option A and B. Full isolation. PgBouncer compatible. Airflow-native. Zero ongoing maintenance burden for isolation. Scales to any number of tenants by adding Aurora clusters to the registry.

---

## Side-by-Side Comparison: A vs B vs C

| Concern | A: Single shared DB | B: Schema per tenant | C: DB per tenant ✅ |
|---|---|---|---|
| Data isolation | None — shared tables | Medium — schema boundary | Full — DB boundary |
| Credential isolation | None | Possible but fragile | Full — separate DB user |
| Fernet key per tenant | Impossible | Possible | Yes — per DB |
| PgBouncer transaction mode | Compatible | Incompatible | Compatible |
| airflow db migrate | Shared — one run | Custom per-tenant logic needed | Just works — per DB |
| Cross-tenant data access | Trivially possible | Possible via qualified names | Impossible |
| Noisy neighbor (slow queries) | High risk | Medium risk | None |
| Per-tenant backup/restore | Impossible | Impossible | Yes |
| Scheduler lock contention | Severe at scale | Medium | None |
| Provisioning complexity | Zero | Medium | Low (4 SQL statements) |
| Grant/revoke maintenance | None needed | O(n²) as tenants grow | None needed |
| Extension conflicts | None | Yes — Aurora restriction | None |
| Infra cost per tenant | ~$1.50/mo | ~$1.50/mo | ~$1.50/mo |
| Suitable for commercial SaaS | No | No | Yes |
| Suitable for internal trusted platform | Yes | Yes | Yes |

**Cost is identical across all three options** — all use one Aurora cluster. Only the isolation and operational complexity differ.

---

## Database Engine Options

Airflow officially supports exactly three database backends. Only two are viable for production.

### Officially supported engines

| Engine | Support level | AWS managed service |
|---|---|---|
| PostgreSQL | Primary — 82% of users | Aurora PostgreSQL, RDS PostgreSQL |
| MySQL | Secondary — 16% of users | Aurora MySQL, RDS MySQL |
| SQLite | Development only — never production | Not available on AWS managed |

### Explicitly unsupported engines

| Engine | Status | Why |
|---|---|---|
| MariaDB | Explicitly NOT supported | Known index handling issues, migrations not tested |
| CockroachDB | Community only, not official | Alembic migration failures, transaction semantic differences |
| Microsoft SQL Server | Not supported | Never added to SQLAlchemy backend list |
| Oracle | Not supported | Never added to SQLAlchemy backend list |

---

### Engine 1: PostgreSQL / Aurora PostgreSQL ✅ Best choice

PostgreSQL is the de-facto standard for Airflow. Every feature, migration, and HA scheduler mode is tested against PostgreSQL first. 82% of the Airflow community uses it.

**Why Aurora PostgreSQL specifically on AWS:**
- Multi-AZ automatic failover (< 30 second RTO)
- Up to 15 read replicas
- Auto-scales storage from 10GB to 128TB
- Aurora Global Database for cross-region replication
- Performance Insights for query-level monitoring
- Compatible with `pgbouncer` fully in transaction mode

**Airflow HA scheduler requirement:**

Airflow 2.x supports running 2 schedulers simultaneously with leader election via the metadata DB. This uses PostgreSQL advisory locks:

```sql
-- Airflow scheduler leader election (internal)
SELECT pg_try_advisory_lock(scheduler_lock_id);
-- Only one scheduler acquires this — the other stands by
```

This is fully supported on PostgreSQL. MySQL support was added later and is less battle-tested in production.

**Recommended Aurora instance sizing:**

| Tier | Instance | Max connections | Tenants (with PgBouncer) | Monthly cost |
|---|---|---|---|---|
| Standard | db.r6g.large | ~2,000 | ~200 | ~$300–400 |
| Premium | db.r6g.xlarge | ~3,000 | ~300 | ~$600–800 |
| Enterprise | db.r6g.2xlarge | ~5,000 | dedicated | ~$1,200–1,600 |

**Connection math:**

```
Without PgBouncer:
  Each Airflow deployment: ~15 persistent connections (scheduler + webserver)
  200 tenants × 15 = 3,000 connections → exceeds db.r6g.large limit (2,000)
  → Not viable without PgBouncer

With PgBouncer (transaction mode):
  PgBouncer maintains: 10 real connections per database to Aurora
  200 databases × 10 = 2,000 real connections → exactly at limit with headroom
  → Use 160 tenants as the trigger to provision a new Aurora cluster (80% threshold)
```

---

### Engine 2: MySQL / Aurora MySQL — viable second choice

MySQL is officially supported and works for Airflow. On AWS, Aurora MySQL is the managed equivalent of Aurora PostgreSQL.

**Where MySQL is acceptable:**
- Your team has existing MySQL expertise and tooling
- You already operate an Aurora MySQL fleet
- You want to reuse existing RDS MySQL infrastructure

**Important MySQL-specific configuration required:**

```ini
# my.cnf / Aurora Parameter Group — required for Airflow
explicit_defaults_for_timestamp = 1
sql_mode = NO_ENGINE_SUBSTITUTION
innodb_large_prefix = 1
innodb_file_format = barracuda
innodb_file_per_table = 1
```

Without these settings, Airflow's table creation and migrations will fail.

**MySQL limitations vs PostgreSQL for your platform:**

| Aspect | MySQL | PostgreSQL |
|---|---|---|
| HA scheduler advisory locks | Less battle-tested | Fully tested |
| ARM instance support | Limited | Full |
| Community Airflow resources | 16% of users | 82% of users |
| Schema migration tooling | Standard | Better documented |
| PgBouncer equivalent | ProxySQL | PgBouncer |
| Connection pooler in transaction mode | ProxySQL (more complex) | PgBouncer (simpler) |

Note: MySQL uses **ProxySQL** as its connection pooler equivalent, not PgBouncer. ProxySQL is more complex to configure but works similarly.

**Database-per-tenant isolation on MySQL:**

```sql
-- Same pattern as PostgreSQL — works identically
CREATE DATABASE airflow_acme_prod;
CREATE USER 'airflow_acme'@'%' IDENTIFIED BY 'random-password';
GRANT ALL PRIVILEGES ON airflow_acme_prod.* TO 'airflow_acme'@'%';
FLUSH PRIVILEGES;
```

**Verdict:** Use MySQL only if you have a compelling existing reason. PostgreSQL is the better default for a new platform.

---

### Engine 3: SQLite — development only

SQLite is a file-based database with no server process. It is included in Airflow's supported list purely for local development convenience.

**Hard limits that rule it out for any production use:**
- Only works with `SequentialExecutor` — one task at a time, globally
- No concurrent write support — multi-scheduler impossible
- No network access — cannot be shared across pods
- No connection pooling support
- Data lives on a single node's filesystem — lost on pod restart without PVC

**Where it is useful in your platform:**

Nowhere in production. For local developer testing of DAGs before pushing to a dev vCluster, SQLite is fine — but it is the developer's local concern, not your platform's.

---

### Engine 4: CockroachDB — avoid

CockroachDB is PostgreSQL-wire-compatible, meaning Airflow can connect to it in theory. In practice, significant problems exist:

**Why it fails for Airflow:**

1. Alembic (Airflow's migration tool) uses PostgreSQL-specific DDL syntax that CockroachDB does not fully implement. Migrations fail or produce incorrect schemas.

2. CockroachDB uses serializable isolation exclusively. Airflow's SQLAlchemy code uses read-committed semantics in several places — behaviour differences cause subtle bugs.

3. Advisory locks (`pg_try_advisory_lock`) used by the HA scheduler are not implemented in CockroachDB.

4. Not officially tested or supported by the Airflow maintainers — any bug you encounter is yours to debug alone.

**Verdict:** Not worth the risk. CockroachDB's distributed SQL value proposition (geo-distribution, zero-downtime horizontal scaling) is irrelevant for Airflow's metadata DB workload, which is low-volume transactional writes on a single region. Aurora PostgreSQL handles this perfectly.

---

### Engine 5: MariaDB — never use

Despite being a MySQL fork, MariaDB is **explicitly not supported** by Airflow:

- Known problems with index handling that cause migration failures
- Airflow's migration scripts are not tested on MariaDB
- The Airflow community will not provide support for MariaDB issues
- Diverges from MySQL in ways that break Airflow's SQLAlchemy dialect assumptions

Even if it appears to work initially, you will hit unexplained migration failures or data corruption issues on Airflow upgrades.

---

## Final Decision Matrix

### Database isolation model

| Your situation | Recommended approach |
|---|---|
| Commercial SaaS, paying customers, any scale | One database per tenant on shared Aurora cluster (Option C) |
| Internal platform, < 20 trusted teams | Single shared database acceptable (Option A) |
| Internal platform, 20–100 teams, no PgBouncer | Schema per tenant may work (Option B) |
| Internal platform, 100+ teams with PgBouncer | One database per tenant (Option C) |
| Enterprise tenant requiring full isolation | Dedicated Aurora cluster (one cluster per tenant) |

### Database engine

| Your situation | Recommended engine |
|---|---|
| New platform, greenfield | Aurora PostgreSQL |
| Existing MySQL fleet and expertise | Aurora MySQL |
| Local developer testing | SQLite (local only, never deployed) |
| Any other engine | Not supported — do not use |

### Summary in one sentence

> Use **one PostgreSQL database per tenant** on a **shared Aurora PostgreSQL cluster**, with **PgBouncer in transaction mode** as the connection pooler. This is the only approach that satisfies isolation, scalability, Airflow compatibility, and cost efficiency simultaneously.

---

## Appendix: Why the cost difference between options is zero

This is the most important practical point. People reach for shared-DB or schema-per-tenant approaches to save money. The savings do not exist:

```
Option A — Single shared DB:
  1 Aurora db.r6g.large cluster = ~$350/month
  200 tenants → $1.75/tenant/month

Option B — Schema per tenant, same cluster:
  1 Aurora db.r6g.large cluster = ~$350/month
  200 tenants → $1.75/tenant/month

Option C — DB per tenant, same cluster:
  1 Aurora db.r6g.large cluster = ~$350/month
  200 databases on same cluster (CREATE DATABASE costs $0)
  200 tenants → $1.75/tenant/month
```

All three options use the same Aurora cluster. A `CREATE DATABASE` statement is free. The choice between them costs exactly zero dollars. Choose based on isolation and operational properties — not cost.

---

*Analysis document v1.0 — Airflow metadata database strategy for multi-tenant platform on AWS EKS with vCluster isolation.*
