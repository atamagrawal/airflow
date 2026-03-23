# Per-Customer Agent Architecture — Design Document

**Version:** 1.2  
**Status:** Draft  
**Authors:** Platform Team  
**Last Updated:** 2026-03-22

---

## Table of Contents

1. [Overview](#overview)
2. [Goals and Non-Goals](#goals-and-non-goals)
3. [Architecture Overview](#architecture-overview)
4. [System Architecture Diagram](#system-architecture-diagram)
5. [Agent Lifecycle](#agent-lifecycle)
6. [On-Demand Agent Model](#on-demand-agent-model)
7. [Agent Internals](#agent-internals)
8. [Test Deployment Mode](#test-deployment-mode)
9. [Production Proxy Mode](#production-proxy-mode)
10. [Control Plane & Routing](#control-plane--routing)
11. [Warm Pool Design](#warm-pool-design)
12. [Sidecar Alternative](#sidecar-alternative)
13. [Agent Runtime: EC2 vs Kubernetes Pod](#agent-runtime-ec2-vs-kubernetes-pod)
14. [Service Layer](#service-layer)
    - 14.1 [Core Platform Services](#141-core-platform-services)
    - 14.2 [Agent Services](#142-agent-services)
    - 14.3 [Supporting Services](#143-supporting-services)
    - 14.4 [Data Stores](#144-data-stores)
    - 14.5 [Observability Stack](#145-observability-stack)
    - 14.6 [Infrastructure & Ops](#146-infrastructure--ops)
    - 14.7 [Service Dependency Map](#147-service-dependency-map)
    - 14.8 [Event Bus](#148-event-bus)
15. [Security](#security)
16. [Failure Modes & Mitigations](#failure-modes--mitigations)
17. [Cost Model](#cost-model)
18. [Implementation Phases](#implementation-phases)
19. [Open Questions](#open-questions)

---

## 1. Overview

This document describes the **per-customer agent architecture** for a managed Apache Airflow platform serving 1,000+ customers.

Each customer is assigned a logical **agent** — a smart proxy and local runtime — that acts as the single point of contact for all operations related to that customer. The agent has two modes:

- **Test mode** — spins up a local Docker-based Airflow environment inside the agent so customers can validate DAGs before promoting to production.
- **Production proxy mode** — forwards all API calls transparently to the customer's isolated Airflow deployment running in a Kubernetes namespace.

The critical design constraint is that **agents are on-demand, not always-on**. With 1,000 customers, running 1,000 agent pods 24/7 is wasteful. In practice only 5–10% of customers are actively interacting with the platform at any moment. The rest are running scheduled DAGs autonomously — they do not need an agent for that.

---

## 2. Goals and Non-Goals

### Goals

- One agent identity per customer — all requests for a customer route through their agent.
- Agents are created on first request and destroyed after idle timeout.
- Test deployments run inside the agent as local Docker containers — no extra K8s namespace needed.
- Agents are stateless — all durable state lives in Vault and the control plane database, not in agent memory.
- The system supports 1,000 customers with fewer than 150 agent pods running at any time.
- Cold start time (dormant → serving) is under 10 seconds via a warm pool.

### Non-Goals

- Agent is not responsible for running scheduled DAGs — the Airflow scheduler in the K8s namespace handles that independently.
- Agent is not a long-term log store — it streams logs to the central observability stack.
- Agent does not manage billing — that is the control plane's responsibility.
- This document does not cover the Airflow namespace provisioning process (covered separately).

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Customer / CI system                    │
│              (Web UI · REST API · CLI · SDK)                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Control Plane API                         │
│    Authenticates request → looks up agent in registry        │
│    Routes to active agent or wakes a dormant one            │
└───────┬──────────────────┬───────────────────┬─────────────┘
        │                  │                   │
        ▼                  ▼                   ▼
┌──────────────┐  ┌──────────────┐   ┌──────────────────┐
│ Agent        │  │ Agent        │   │  Warm Pool       │
│ cust-001     │  │ cust-042     │   │  (20-30 pods)    │
│ [ACTIVE]     │  │ [ACTIVE]     │   │  unassigned,     │
│              │  │              │   │  ready to assign  │
│ ┌──────────┐ │  │ ┌──────────┐ │   └──────────────────┘
│ │Test mode │ │  │ │Prod mode │ │
│ │Docker    │ │  │ │K8s proxy │ │
│ │containers│ │  │ │          │ │
│ └──────────┘ │  │ └──────────┘ │
└──────┬───────┘  └──────┬───────┘
       │                 │
       ▼                 ▼
┌────────────┐    ┌───────────────────────────┐
│ Local      │    │ K8s Tenant Namespace       │
│ Docker     │    │ cust-042                  │
│ (ephemeral)│    │ Airflow scheduler          │
└────────────┘    │ Airflow webserver          │
                  │ Workers · Postgres · Redis │
                  └───────────────────────────┘
```

### Key components

| Component | Description |
|---|---|
| **Control Plane API** | The front door. Authenticates all requests, maintains the agent registry, routes traffic. |
| **Agent** | A lightweight Go or Python process (pod) that proxies, manages secrets, and runs test deployments. One logical agent per customer. |
| **Warm Pool** | A set of pre-initialized, unassigned agent pods that can be claimed in under 1 second. |
| **Agent Registry** | A Redis hash map of `customer_id → agent_url` maintained by the control plane. |
| **Airflow Namespace** | The customer's actual Airflow deployment in Kubernetes — runs autonomously, independent of the agent. |

---

## 4. System Architecture Diagram

The platform is organised into six horizontal layers, each building on the one below.

```
┌──────────────────────────────────────────────────────────────────┐
│  Layer 1 — Customer                                              │
│  Web UI · REST API · CLI · SDK                                   │
│  All traffic carries a platform-issued JWT                       │
└────────────────────────┬─────────────────────────────────────────┘
                         │ HTTPS / JWT
┌────────────────────────▼─────────────────────────────────────────┐
│  Layer 2 — Core platform services                                │
│  API Gateway · Auth · Tenant · Provisioning · Billing            │
│  Event bus (SNS/SQS) decouples these services                    │
└──────────┬─────────────────────────────────┬─────────────────────┘
           │                                 │
┌──────────▼──────────┐         ┌────────────▼──────────────────────┐
│  Layer 3 — Agent    │         │  Agent pool                       │
│  Control plane      │         │  Standard: K8s warm pool (pods)   │
│  router             │         │  Enterprise: EC2 ASG warm pool    │
│  Pool controller    │         │  (Stopped state, ~30s resume)     │
│  EC2 provisioner    │         └───────────────────────────────────┘
└──────────┬──────────┘
           │
┌──────────▼──────────────────────────────────────────────────────┐
│  Layer 4 — Active agents (one per active customer session)      │
│  Proxy (prod) · Docker test mode · Rate limit · Secrets         │
│  Standard: K8s pod + DinD sidecar                               │
│  Enterprise: dedicated EC2 instance, native Docker              │
└──────────┬─────────────────────────────┬───────────────────────┘
           │ test mode                   │ prod proxy
    ┌──────▼───────┐            ┌────────▼──────────────────────┐
    │ Local Docker │            │ K8s tenant namespaces         │
    │ (DinD)       │            │ cust-001 … cust-1000          │
    │ ephemeral    │            │ Airflow: scheduler · webserver │
    │ test stack   │            │ workers · postgres · redis    │
    └──────────────┘            └───────────────────────────────┘
           │                                │
┌──────────▼────────────────────────────────▼───────────────────┐
│  Layer 5 — Shared platform infrastructure                      │
│  Vault · Redis · Postgres · S3 · Prometheus · Loki · Grafana   │
└──────────────────────────┬────────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────────┐
│  Layer 6 — Cloud (AWS)                                         │
│  EKS · RDS Aurora · ElastiCache · EC2 ASG · S3 · Route53      │
└───────────────────────────────────────────────────────────────┘
```

### Color legend (used throughout this document)

| Color | Meaning |
|---|---|
| Purple | Core platform services (Auth, Tenant, Provisioning, Billing) |
| Teal | Standard tier — K8s pod agents + warm pool |
| Amber | Enterprise tier — EC2 ASG agents |
| Coral | Tenant Airflow deployments |
| Blue | Supporting services (DAG, Secret, Notify, Upgrade) |
| Green | Observability (Prometheus, Loki, Grafana, Tempo) |
| Gray | Infrastructure, data stores, gateways |

---

## 5. Agent Lifecycle

An agent transitions through four states:

```
  ┌──────────┐    request arrives     ┌──────────┐
  │ DORMANT  │ ─────────────────────► │ WARMING  │
  │ (no pod) │                        │ (~5–10s) │
  └──────────┘                        └────┬─────┘
       ▲                                   │ pod ready + config loaded
       │                                   ▼
       │  idle timeout               ┌──────────┐
       │  pod terminated             │  ACTIVE  │
       └──────────────────────────── │          │
                                     └────┬─────┘
                                          │ no traffic for N min
                                          ▼
                                     ┌──────────┐
                                     │  IDLE    │
                                     │ (cooling)│
                                     └────┬─────┘
                                          │
                              ┌───────────┴──────────┐
                              │                       │
                              ▼                       ▼
                        return to               terminate
                        warm pool              (if pool full)
```

### State definitions

**DORMANT** — No pod is running. The customer's Airflow namespace runs fine without an agent. The only thing that exists is a row in the `agents` table in the control plane DB. This is the state for ~90% of customers at any given time.

**WARMING** — A warm pool pod has been claimed and is loading the customer's configuration: secrets from Vault, environment variables, K8s service account credentials, and the customer's Airflow endpoint URL. Target time: under 5 seconds.

**ACTIVE** — The agent is serving requests. It refreshes its idle timer on every request.

**IDLE** — No traffic for `IDLE_TIMEOUT` minutes. The agent flushes any buffered state, deregisters from the registry, and either returns its pod to the warm pool or terminates (if the pool is already full).

---

## 6. On-Demand Agent Model

### The core insight

At 1,000 customers, only 50–100 customers are actively making API calls at any moment. Scheduled DAG runs do not need an agent — the Airflow scheduler in the K8s namespace handles those entirely on its own.

Keeping 1,000 agents alive would waste ~64GB+ of RAM and create unnecessary API server load. Instead, we use a **three-tier model**:

```
Tier 1 — Dormant    ~900 customers
                    No pod. Row in DB only.
                    Airflow namespace runs scheduled DAGs autonomously.
                    Cost: ~$0 for agent infrastructure.

Tier 2 — Warm pool  20–30 pods
                    Generic, unassigned agent processes.
                    Config not yet loaded.
                    Can be claimed and initialized in < 1 second.

Tier 3 — Active     50–100 pods
                    Fully initialized for a specific customer.
                    Serving live traffic.
                    Idle timeout: configurable, default 10 minutes.
```

### Expected steady-state at 1,000 customers

| State | Count | RAM per pod | Total RAM |
|---|---|---|---|
| Dormant | ~900 | 0 MB | 0 |
| Warm pool | 25 | 64 MB | 1.6 GB |
| Active | ~75 | 128 MB | 9.6 GB |
| **Total** | | | **~11 GB** |

Compare this to always-on model: 1,000 × 128 MB = **128 GB** — a 12x reduction.

### Warm pool sizing

The warm pool size is a trade-off between cold start latency and idle resource cost.

```
min_pool_size = peak_requests_per_minute × avg_warm_time_seconds / 60
```

For a platform where peak traffic is 30 new customer sessions per minute and warm time is 5 seconds:

```
min_pool_size = 30 × 5 / 60 = 2.5 → round up to 5 (with safety margin → 20)
```

The pool autoscales: if it drops below `min_size`, the pool controller spawns new pods. If requests are slow, it drains excess pods.

---

## 7. Agent Internals

The agent is a single lightweight process — approximately 2,000 lines of Go or Python. It exposes an internal HTTP API that the control plane routes to.

### Agent structure

```python
class CustomerAgent:

    customer_id: str
    mode: Literal["prod", "test"]

    # Loaded on WARMING
    config: CustomerConfig       # from Vault
    k8s_client: KubernetesClient # scoped to customer namespace
    docker_client: DockerClient  # for test mode
    airflow_url: str             # http://webserver.cust-X.svc:8080

    # Runtime state
    active_containers: dict      # test mode only
    idle_timer: Timer
    last_request_at: datetime

    # --- Core methods ---

    async def handle(self, request: Request) -> Response:
        self.idle_timer.reset()
        if self.mode == "test":
            return await self._handle_test(request)
        return await self._proxy_to_airflow(request)

    async def _proxy_to_airflow(self, request: Request) -> Response:
        """Forward to customer's K8s Airflow deployment."""
        return await http_forward(
            url=f"{self.airflow_url}{request.path}",
            method=request.method,
            body=request.body,
            headers=self._inject_auth_headers()
        )

    async def start_test_deployment(self, dag_bundle_path: str):
        """Spin up ephemeral Airflow stack via Docker."""
        self.active_containers = await self.docker_client.compose_up(
            config=self._build_compose_config(dag_bundle_path)
        )
        self.mode = "test"

    async def stop_test_deployment(self):
        await self.docker_client.compose_down(self.active_containers)
        self.active_containers = {}
        self.mode = "prod"

    async def health_check(self) -> dict:
        return {
            "customer_id": self.customer_id,
            "mode": self.mode,
            "airflow_healthy": await self._check_airflow_health(),
            "uptime_seconds": self.uptime(),
            "idle_seconds": self.idle_timer.elapsed()
        }

    async def drain(self):
        """Graceful shutdown — called before pod is reclaimed by warm pool."""
        if self.active_containers:
            await self.stop_test_deployment()
        await control_plane.deregister_agent(self.customer_id)
```

### Agent initialization sequence (WARMING → ACTIVE)

```
1. Pool controller claims pod from warm pool
2. Control plane sends: initialize(customer_id)
3. Agent fetches customer config from Vault:
     - Airflow fernet key
     - DB connection string
     - Git credentials (for DAG sync)
     - Customer-specific env vars
4. Agent creates scoped K8s client for namespace cust-{id}
5. Agent registers with control plane:
     registry.set(customer_id, agent_url)
6. Agent starts serving — state transitions to ACTIVE
```

Total initialization time target: **< 5 seconds**.

---

## 8. Test Deployment Mode

When a customer wants to validate DAGs before pushing to production, the agent spins up a local ephemeral Airflow stack using Docker.

### Docker-in-pod approach

The agent pod runs with a **Docker-in-Docker (DinD) sidecar**. This avoids mounting the host Docker socket (which grants node-level privileges) and gives each agent a fully isolated Docker daemon.

```yaml
# Agent pod spec
containers:
  - name: agent
    image: platform/customer-agent:latest
    env:
      - name: DOCKER_HOST
        value: tcp://localhost:2375   # Talk to DinD sidecar
      - name: CUSTOMER_ID
        valueFrom:
          fieldRef: { fieldPath: metadata.labels['customer-id'] }
    resources:
      requests: { cpu: "100m", memory: "128Mi" }
      limits:   { cpu: "500m", memory: "512Mi" }

  - name: dind
    image: docker:24-dind
    securityContext:
      privileged: true               # Required for DinD
    volumeMounts:
      - name: docker-storage
        mountPath: /var/lib/docker
    resources:
      requests: { cpu: "100m", memory: "256Mi" }
      limits:   { cpu: "1",    memory: "1Gi"  }

volumes:
  - name: docker-storage
    emptyDir: {}
```

### Test deployment compose config

```python
def _build_compose_config(self, dag_path: str) -> dict:
    image = f"apache/airflow:{self.config.airflow_version}"
    env = {
        "AIRFLOW__CORE__EXECUTOR":         "LocalExecutor",
        "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": "sqlite:////opt/airflow/airflow.db",
        "AIRFLOW__CORE__LOAD_EXAMPLES":    "False",
        **self.config.env_vars
    }
    volume = f"{dag_path}:/opt/airflow/dags:ro"

    return {
        "scheduler": {
            "image": image, "environment": env,
            "volumes": [volume], "command": "scheduler"
        },
        "webserver": {
            "image": image, "environment": env,
            "volumes": [volume], "command": "webserver",
            "ports": ["8080:8080"]
        }
    }
```

### Test deployment lifecycle

```
Customer: POST /api/v1/test-deployments
  Body: { dag_bundle: <base64 zip> }

Agent:
  1. Unzip DAG bundle to /tmp/dags/{customer_id}/
  2. docker compose up (scheduler + webserver)
  3. Wait for webserver health check (max 60s)
  4. Return: { endpoint, token, ttl: "2h" }

Customer uses test environment (runs DAGs, checks logs)

Auto-teardown after TTL or explicit DELETE:
  Agent: docker compose down
  Agent: rm -rf /tmp/dags/{customer_id}/
  Agent: switch mode back to "prod"
```

### Test vs production feature parity

| Feature | Test mode | Prod mode |
|---|---|---|
| Trigger DAG run | Yes (local) | Yes (K8s) |
| View DAG logs | Yes (streamed) | Yes (streamed) |
| Edit connections/variables | Yes (ephemeral) | Yes (persisted) |
| KubernetesExecutor | No (LocalExecutor) | Yes |
| Custom operators | If in DAG bundle | Yes |
| Persistent state | No — ephemeral | Yes |
| SLA / alerting | No | Yes |

---

## 9. Production Proxy Mode

In production mode, the agent is a thin authenticated proxy. Every Airflow REST API call from the customer is forwarded to their Airflow webserver running in the K8s namespace.

### Why proxy instead of direct access?

- The customer never gets a direct URL to their Airflow webserver — everything goes through the agent, which enforces auth, rate limiting, and audit logging.
- The agent can inject platform-level headers (customer ID, request trace ID) without the customer knowing.
- Switching from test → prod is transparent to the customer's tooling.

### Proxy implementation

```python
async def _proxy_to_airflow(self, request: Request) -> Response:
    # Validate customer's API token
    await self.auth.verify(request.headers["Authorization"])

    # Build forwarded request
    target_url = f"{self.airflow_url}/api/v1{request.path}"
    headers = {
        "X-Platform-Customer": self.customer_id,
        "X-Platform-Request-ID": request.id,
        "Authorization": f"Basic {self.config.airflow_basic_auth}"
    }

    # Forward with timeout and circuit breaker
    async with self.circuit_breaker:
        response = await http_client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            body=request.body,
            timeout=30
        )

    # Log for audit trail
    await audit_log.record(
        customer_id=self.customer_id,
        path=request.path,
        method=request.method,
        status=response.status_code
    )

    return response
```

### Per-agent rate limiting

Because all requests for a customer pass through one agent, per-customer rate limiting is trivial — just a token bucket in the agent's memory:

```python
rate_limiter = TokenBucket(
    capacity=100,        # max burst
    refill_rate=20       # requests per second sustained
)
```

---

## 10. Control Plane & Routing

The control plane is the entry point for all customer requests. Its router does the following:

```python
class AgentRouter:

    registry: Redis           # customer_id → agent_url
    warm_pool: WarmPool       # pool of unassigned agent pods
    db: Database              # agent state persistence

    async def route(self, customer_id: str, request: Request) -> Response:

        # 1. Check if customer has an active agent
        if agent_url := await self.registry.get(customer_id):
            return await forward(agent_url, request)

        # 2. No active agent — acquire from warm pool
        agent_url = await self.warm_pool.acquire_and_init(customer_id)
        await self.registry.set(customer_id, agent_url, ttl=3600)

        return await forward(agent_url, request)

    async def reap_idle_agents(self):
        """Runs every 60 seconds via cron."""
        idle_agents = await self.db.query(
            "SELECT customer_id, agent_url FROM agents "
            "WHERE last_request_at < NOW() - INTERVAL %s MINUTES",
            IDLE_TIMEOUT_MINUTES
        )
        for agent in idle_agents:
            await self._reclaim_agent(agent)

    async def _reclaim_agent(self, agent):
        await forward(agent.agent_url, Request("POST", "/drain"))
        await self.registry.delete(agent.customer_id)
        await self.warm_pool.return_pod(agent.agent_url)
        await self.db.update_agent_state(agent.customer_id, "dormant")
```

### Agent registry schema

```sql
CREATE TABLE agents (
    customer_id       VARCHAR(64) PRIMARY KEY,
    agent_url         VARCHAR(256),          -- null if dormant
    state             VARCHAR(16),           -- dormant | warming | active | idle
    last_request_at   TIMESTAMPTZ,
    test_mode_active  BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_agents_state ON agents(state);
CREATE INDEX idx_agents_idle  ON agents(last_request_at) WHERE state = 'active';
```

### Routing latency targets

| Scenario | Target latency |
|---|---|
| Request hits active agent | < 5 ms overhead (just a proxy hop) |
| Request wakes from warm pool | < 1 second (config load only) |
| Request with no warm pods (true cold start) | < 15 seconds (pod schedule + init) |

---

## 11. Warm Pool Design

The warm pool is managed by a **Pool Controller** — a dedicated Kubernetes deployment that continuously reconciles desired vs actual pool size.

### Pool controller

```python
class WarmPoolController:

    target_size: int = 25        # configurable
    min_size: int = 10
    max_size: int = 50

    async def reconcile(self):
        """Called every 30 seconds."""
        available = await self.count_available_pods()

        if available < self.min_size:
            shortage = self.target_size - available
            await self.spawn_pods(shortage)

        elif available > self.max_size:
            excess = available - self.target_size
            await self.drain_pods(excess)

    async def spawn_pods(self, count: int):
        for _ in range(count):
            await k8s.create_pod(
                name=f"agent-pool-{uuid4().hex[:8]}",
                namespace="platform-agents",
                image="platform/customer-agent:latest",
                labels={"pool": "warm", "assigned": "false"}
            )

    async def acquire_and_init(self, customer_id: str) -> str:
        """Claim a warm pod and initialize it for a customer."""
        pod = await k8s.patch_pod_label(
            label_selector="pool=warm,assigned=false",
            patch={"assigned": "true", "customer-id": customer_id},
            limit=1
        )
        agent_url = f"http://{pod.status.pod_ip}:9000"
        await http_post(f"{agent_url}/initialize", {"customer_id": customer_id})
        return agent_url
```

### Warm pool autoscaling

The pool size scales with traffic patterns. It can be driven by a simple Prometheus metric:

```yaml
# KEDA ScaledObject for warm pool
triggers:
  - type: prometheus
    metadata:
      query: |
        avg_over_time(platform_warm_pool_size[5m]) /
        avg_over_time(platform_warm_pool_acquisitions_per_minute[5m])
      threshold: "2"   # keep at least 2 minutes of acquisition capacity
```

---

## 12. Sidecar Alternative

For teams that want to avoid warm pool complexity entirely, the agent can run as a **sidecar container** co-located with the Airflow webserver pod.

```yaml
# airflow-webserver deployment (per tenant namespace)
containers:
  - name: airflow-webserver
    image: apache/airflow:2.9.1
    ports: [{ containerPort: 8080 }]

  - name: agent                          # sidecar
    image: platform/customer-agent:latest
    env:
      - name: CUSTOMER_ID
        value: "cust-001"
      - name: MODE
        value: "sidecar"                 # no warm pool logic needed
      - name: AIRFLOW_URL
        value: "http://localhost:8080"   # localhost — same pod
    ports: [{ containerPort: 9000 }]
    resources:
      requests: { cpu: "50m", memory: "64Mi" }
      limits:   { cpu: "200m", memory: "128Mi" }
```

### Sidecar trade-offs

| Aspect | Sidecar | Warm pool |
|---|---|---|
| Complexity | Low — no pool management | High — pool controller needed |
| Cold start | Zero — always running | < 1s from pool |
| Cost at 1,000 tenants | 1,000 × 64 MB = 64 GB total | 75–130 pods × 128 MB = ~11 GB |
| Failure scope | Agent dies if webserver crashes | Independent failure domains |
| Test mode (DinD) | Needs privileged sidecar per tenant | DinD only on active agent pods |

**Recommendation:** Use the sidecar model if your webserver pods are always running (no scale-to-zero). Use the warm pool model if you want agents to be truly on-demand or if DinD security is a concern.

---

## 13. Agent Runtime: EC2 vs Kubernetes Pod

This section documents the decision rationale for choosing Kubernetes pods as the agent runtime over EC2 instances, and defines the hybrid model for enterprise customers.

### Decision: Kubernetes pod (standard), EC2 (enterprise tier only)

Since the Airflow tenant environments are already Kubernetes namespaces, the agent runs as a **Kubernetes pod** for standard customers. EC2 is reserved as a premium option for enterprise customers who need dedicated infrastructure.

### Full comparison

| Dimension | EC2 instance | Kubernetes pod |
|---|---|---|
| Startup time (dormant → serving) | 60–120s (AMI boot) | 1–5s (cached image), <1s from warm pool |
| Docker for test mode | Native — installed on OS directly | Needs DinD sidecar or privileged container |
| Cost at idle | ~$15/mo per instance (t3.small) | ~$0.10/mo (bin-packed on shared node) |
| Density at 75 active agents | 75 EC2 instances minimum | 3–5 shared nodes (20–40 pods per node) |
| Ops burden | High — AMI patches, SSM, instance lifecycle | Low — redeploy image, K8s manages lifecycle |
| Per-agent isolation | Strong — full OS boundary, separate VPC possible | Good — cgroup + kernel namespaces, NetworkPolicy |
| Warm pool feasibility | No — 90s boot makes true on-demand impractical | Yes — pod starts in seconds from cached image |
| Upgrade path | AMI pipeline + rolling replace | `kubectl rollout restart` |
| Best for | Long-lived sessions, heavy Docker workloads, air-gapped | Short-lived on-demand sessions, already on K8s |

### Why EC2 fails the on-demand requirement

The warm pool model only works if a dormant agent can become active in under 5 seconds. EC2 boot time is 60–120 seconds even with a pre-baked AMI. This means you either:

- Keep EC2 instances running idle 24/7 (expensive — $15/mo × 1,000 = $15,000/mo just for agents), or
- Accept 2-minute cold starts on every new customer session (terrible UX).

Neither is acceptable. Kubernetes pods start in 1–5 seconds from a cached image, making the warm pool viable and on-demand agents practical.

### Why pod wins on cost

```
EC2 always-on (1,000 agents):   1,000 × t3.small × $15/mo = $15,000/mo
EC2 on-demand (100 active):       100 × t3.small × $15/mo =  $1,500/mo  (still needs warm fleet)

Pod always-on (1,000 agents):   1,000 × 128MB pod × $0.10/mo =   $100/mo
Pod on-demand (warm pool of 25):   25 warm + 75 active pods       =    ~$70/mo
```

Pod model is **200x cheaper** than EC2 always-on, and **20x cheaper** than EC2 on-demand.

### The hybrid model: pods for standard, EC2 for enterprise

```
Standard tier   →  Kubernetes pod agent
                   Warm pool, shared node
                   DinD sidecar for test mode
                   Cold start: < 1s from pool

Enterprise tier →  Dedicated EC2 agent
                   Provisioned on login, terminated on idle
                   Native Docker — no DinD needed
                   Full OS isolation, dedicated ENI
                   Cold start: ~90s (acceptable for enterprise sessions)
                   Billed per session-hour to the customer
```

This creates a natural upsell: enterprise customers get a dedicated machine with native Docker, OS-level isolation, and no noisy-neighbor concerns. Standard customers get fast, cheap, on-demand pods.

### EC2 agent provisioning (enterprise tier)

When an enterprise customer logs in, the control plane provisions a dedicated EC2 agent via the AWS SDK:

```python
async def provision_ec2_agent(customer_id: str) -> str:
    # Launch pre-baked AMI with agent binary + Docker pre-installed
    response = await ec2.run_instances(
        ImageId=AGENT_AMI_ID,           # AMI with agent + Docker CE pre-installed
        InstanceType="t3.medium",       # 2 vCPU / 4 GB — enough for Docker test stack
        MinCount=1, MaxCount=1,
        SubnetId=AGENT_SUBNET_ID,       # private subnet, no public IP
        SecurityGroupIds=[AGENT_SG_ID],
        IamInstanceProfile={"Name": "customer-agent-role"},
        UserData=base64.b64encode(f"""
            #!/bin/bash
            export CUSTOMER_ID={customer_id}
            export VAULT_ADDR=https://vault.internal
            export PLATFORM_ENV=production
            systemctl enable --now customer-agent
        """.encode()).decode(),
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "customer-id",  "Value": customer_id},
                {"Key": "managed-by",   "Value": "platform"},
                {"Key": "tier",         "Value": "enterprise"},
            ]
        }],
        InstanceInitiatedShutdownBehavior="terminate"  # self-terminates on agent exit
    )

    instance_id = response["Instances"][0]["InstanceId"]

    # Wait for agent to boot and self-register with control plane (~90s)
    agent_url = await wait_for_agent_registration(
        customer_id=customer_id,
        timeout_seconds=120
    )
    return agent_url

async def terminate_ec2_agent(customer_id: str, instance_id: str):
    """Called by idle reaper after IDLE_TIMEOUT for enterprise agents."""
    await ec2.terminate_instances(InstanceIds=[instance_id])
    await registry.delete(customer_id)
    await db.update_agent_state(customer_id, "dormant")
```

The agent binary on the EC2 instance self-registers with the control plane on startup and self-deregisters on shutdown, following the same lifecycle state machine as pod agents.

### AMI bake pipeline

The EC2 agent AMI is rebuilt on every agent release using a Packer pipeline:

```json
{
  "builders": [{
    "type": "amazon-ebs",
    "instance_type": "t3.small",
    "source_ami_filter": {
      "filters": { "name": "ubuntu/images/hvm-ssd/ubuntu-22.04-*" }
    }
  }],
  "provisioners": [
    { "type": "shell", "script": "install-docker.sh"   },
    { "type": "shell", "script": "install-agent.sh"    },
    { "type": "shell", "script": "configure-vault.sh"  }
  ]
}
```

Target AMI boot-to-serving time: **under 90 seconds**.

### Routing layer: pod vs EC2 agents are transparent

The control plane router does not care whether the agent is a pod or an EC2 instance. Both register the same way in the agent registry (`customer_id → agent_url`). The only difference is how they are provisioned and how they are reaped.

```python
async def route(self, customer_id: str, request: Request) -> Response:
    if agent_url := await self.registry.get(customer_id):
        return await forward(agent_url, request)

    customer = await db.get_customer(customer_id)

    if customer.tier == "enterprise":
        agent_url = await provision_ec2_agent(customer_id)   # ~90s
    else:
        agent_url = await warm_pool.acquire_and_init(customer_id)  # <1s

    await self.registry.set(customer_id, agent_url)
    return await forward(agent_url, request)
```

### Decision record

| Option considered | Verdict | Reason |
|---|---|---|
| EC2 for all agents | Rejected | 90s cold start breaks on-demand model; 20x more expensive |
| Pod for all agents | Accepted for standard tier | Fast, cheap, fits existing K8s infra |
| EC2 for enterprise, pod for standard | Accepted | EC2 isolation story + native Docker worth premium price |
| Fargate tasks as agents | Rejected | 30–60s cold start, no persistent Docker daemon for test mode |

---

## 14. Service Layer

This section documents every service that needs to be built or configured to run the platform. Services are grouped by domain. Each entry includes its responsibility, tech stack, port, and key API surface.

---

### 14.1 Core Platform Services

These four services form the business logic backbone of the platform. They are built and owned by your team.

#### API gateway

| Field | Value |
|---|---|
| Purpose | Single entry point for all customer traffic |
| Tech | Kong (self-hosted on EKS) or AWS API Gateway |
| Port | `443` |
| Responsibilities | TLS termination, JWT forwarding, per-customer rate limiting, request ID injection, routing to downstream services |

Routes:
- `POST /api/v1/auth/*` → Auth service
- `GET|POST /api/v1/tenants/*` → Tenant service
- `ALL /proxy/{customer_id}/*` → Control plane router
- `POST /api/v1/dags/*` → DAG service

---

#### Auth service

| Field | Value |
|---|---|
| Purpose | Issues and validates platform JWTs, manages SSO and API keys |
| Tech | FastAPI + Keycloak (identity provider) |
| Port | `8001` |
| Key endpoints | `POST /token`, `POST /verify`, `GET /users/{id}/permissions` |

Responsibilities: issues short-lived JWTs (RS256, 15-min expiry) with 7-day refresh tokens, API key management (stored hashed in Postgres), SSO via SAML/OIDC for enterprise plans, per-customer RBAC roles.

---

#### Tenant service

| Field | Value |
|---|---|
| Purpose | Source of truth for customer accounts, plans, and settings |
| Tech | FastAPI + Postgres |
| Port | `8002` |
| Key endpoints | `POST /tenants`, `GET /tenants/{id}`, `PATCH /tenants/{id}/plan`, `GET /tenants/{id}/config` |

Owns: `customer_id`, `tier` (starter/standard/enterprise), `airflow_version`, `feature_flags`, `git_repo_url`, `idle_timeout_override`. Emits `TenantCreated` event to SNS on signup — this triggers the provisioning service.

---

#### Provisioning service

| Field | Value |
|---|---|
| Purpose | Creates, upgrades, and destroys Airflow environments |
| Tech | FastAPI + Python K8s SDK + Helm SDK |
| Port | `8003` |
| Key endpoints | `POST /environments`, `DELETE /environments/{id}`, `POST /environments/{id}/upgrade` |

Provisioning sequence (target: < 3 minutes end-to-end):

```
1. Create Vault secret paths for customer
2. Create K8s namespace + RBAC + NetworkPolicy
3. Deploy CloudNativePG cluster (Postgres)
4. Deploy Redis (standalone)
5. Deploy Airflow Helm release (scheduler, webserver, workers, triggerer, git-sync)
6. Create NGINX Ingress rule → cust-{id}.airflow.platform.com
7. Create cert-manager Certificate CRD (TLS)
8. Emit EnvironmentReady event to SNS
```

---

#### Billing service

| Field | Value |
|---|---|
| Purpose | Tracks usage and manages Stripe metered billing |
| Tech | FastAPI + Stripe SDK |
| Port | `8004` |
| Key endpoints | `POST /usage`, `GET /invoices`, `POST /plans/{id}/subscribe`, `POST /webhooks/stripe` |

Metering model: standard tier bills per request (Stripe Meters API). Enterprise tier bills per session-hour for EC2 agents. Listens to SNS events: `AgentSessionStarted`, `AgentSessionEnded`, `RequestProcessed`. Stripe webhooks handle: `invoice.paid`, `payment_failed`, `customer.subscription.updated`.

---

### 14.2 Agent Services

These services form the agent routing and lifecycle layer — the core of the on-demand architecture.

#### Control plane router

| Field | Value |
|---|---|
| Purpose | Routes all customer API requests to the correct agent |
| Tech | FastAPI |
| Port | `8010` |
| Key endpoints | `ALL /proxy/{customer_id}/{path}`, `GET /agents/{customer_id}/status`, `GET /pool/status` |

Hot path (every request): Redis GET `agent:registry:{customer_id}` < 1ms. On hit: refresh TTL, forward. On miss: acquire from pool (standard) or provision EC2 (enterprise), set Redis key, forward. On 503 from agent: clear registry, re-provision once.

Background task: idle reaper runs every 60 seconds — queries DB for agents idle > `IDLE_TIMEOUT_MINUTES`, calls `POST /drain`, removes from registry, releases resource back to pool.

---

#### Warm pool controller

| Field | Value |
|---|---|
| Purpose | Manages the K8s pod warm pool for standard tier agents |
| Tech | Python + kubernetes SDK |
| Port | `8011` |
| Key endpoints | `POST /acquire` (called by router), `GET /status` |

Reconciliation loop (every 30 seconds):

```python
available = count_pods(label="pool=warm,assigned=false", phase="Running")
if available < WARM_POOL_MIN_SIZE:
    spawn_pods(WARM_POOL_TARGET_SIZE - available)
elif available > WARM_POOL_MAX_SIZE:
    drain_pods(available - WARM_POOL_TARGET_SIZE)
```

`acquire_and_init(customer_id)`: atomically patches pod labels (`assigned=true`, `customer-id=X`), calls `POST /initialize` on the agent, returns agent URL. If pool is empty, cold-starts a new pod (target: < 15 seconds). Autoscales via KEDA Prometheus trigger: `pool_size / acquisitions_per_minute > 2`.

---

#### EC2 provisioner

| Field | Value |
|---|---|
| Purpose | Manages enterprise EC2 agent lifecycle via AWS ASG warm pool |
| Tech | FastAPI + boto3 |
| Port | `8012` |
| Key endpoints | `POST /provision/{customer_id}`, `POST /release/{instance_id}`, `GET /pool/status` |

`provision(customer_id)`: writes customer_id to SSM Parameter Store → calls `set_desired_capacity(+1)` on ASG → polls Redis for agent self-registration (120s timeout). `release(instance_id)`: calls `terminate_instance_in_auto_scaling_group(ShouldDecrementDesiredCapacity=True)` — ASG reuse policy returns instance to Stopped state. On startup: calls `put_warm_pool(Stopped, min=5, max=20, ReuseOnScaleIn=true)` (idempotent).

---

#### Customer agent

| Field | Value |
|---|---|
| Purpose | One per active customer session — proxies requests, manages test deployments |
| Tech | Python or Go |
| Port | `9000` |
| Key endpoints | `POST /initialize`, `POST /drain`, `GET /healthz`, `ALL /{path}` (proxy) |

`POST /initialize`: Vault AppRole login → fetch secrets bundle → create namespace-scoped K8s client → register URL in Redis (`SETEX agent:registry:{id} 3600 {url}`).

`ALL /{path}` (hot path): token bucket rate limit check → inject `X-Platform-Customer` + `X-Trace-ID` headers → strip customer JWT → add Airflow Basic Auth → `httpx` forward to `http://airflow-webserver.cust-{id}.svc:8080` → log audit entry → return response.

`POST /drain`: `docker compose down` (if test mode active) → Redis DEL → deregister from control plane.

---

#### DinD sidecar

| Field | Value |
|---|---|
| Purpose | Provides an isolated Docker daemon for test deployments |
| Image | `docker:24-dind` |
| Port | `2375` (internal to pod only) |
| Security | Privileged container — runs on dedicated tainted node pool (`agent=dind:NoSchedule`) |

Agent connects via `DOCKER_HOST=tcp://localhost:2375`. Test stack: `scheduler` + `webserver` + SQLite DB (LocalExecutor) with the customer's DAG bundle mounted at `/opt/airflow/dags`. TTL enforced by the agent — calls `compose down` on expiry or `DELETE /test-deployments`.

---

### 14.3 Supporting Services

These services handle specific platform features. They can be built incrementally after the core is stable.

#### DAG service

| Field | Value |
|---|---|
| Purpose | DAG bundle upload, validation, versioning, and test→prod promotion |
| Tech | FastAPI + boto3 + subprocess (DAG validation) |
| Port | `8020` |
| Key endpoints | `POST /bundles` (upload), `POST /bundles/{id}/promote`, `GET /bundles/{customer_id}` |

Upload flow: accept ZIP → unzip → `python -c "import <dag_file>"` syntax check → store in `s3://platform-dags/{customer_id}/{version}/` → record in DB → trigger git-sync refresh on customer's namespace. Promotion: copy bundle to prod S3 path → update DB `promoted_at` → notify upgrade service.

---

#### Secret service

| Field | Value |
|---|---|
| Purpose | Thin CRUD wrapper over Vault for customer connections and variables |
| Tech | FastAPI + hvac (Vault Python client) |
| Port | `8021` |
| Key endpoints | `POST /secrets/{customer_id}/connections`, `GET /secrets/{customer_id}/connections`, `DELETE /secrets/{customer_id}/connections/{name}` |

Never returns raw secret values to non-agent callers — only writes. Agents fetch directly from Vault using their own scoped AppRole credentials. Manages Vault path lifecycle: creates paths on tenant provisioning, rotates fernet key on request, cleans up on tenant deletion.

---

#### Notification service

| Field | Value |
|---|---|
| Purpose | Alerts for DAG failures, SLA misses, and environment events |
| Tech | FastAPI + AWS SES + Slack SDK + PagerDuty |
| Port | `8022` |
| Key endpoints | `POST /notification-rules`, `GET /notification-rules/{customer_id}`, `POST /webhooks/events` |

Subscribes to SNS topics: `dag-events` (DAGRunFailed, SLAMiss), `environment-events` (EnvironmentUnhealthy, ProvisionFailed, UpgradeCompleted). Customer-configurable rules: channel (email/Slack/PagerDuty), severity threshold, DAG filter.

---

#### Upgrade service

| Field | Value |
|---|---|
| Purpose | Orchestrates rolling Airflow version upgrades across the fleet |
| Tech | FastAPI + Helm SDK |
| Port | `8023` |
| Key endpoints | `POST /upgrade-jobs` (trigger batch), `GET /upgrade-jobs/{id}`, `POST /upgrade-jobs/{id}/rollback` |

Cohort-based rollout: 10-tenant canary group → 24h observation → 50 tenants/day for the remainder. Post-upgrade health checks (5 minutes): Airflow webserver responds, scheduler heartbeat present, no failed task runs. Auto-rollback on failure: `helm rollback airflow-{id}` → emit `UpgradeRolledBack` event.

---

### 14.4 Data Stores

#### Control plane database

| Field | Value |
|---|---|
| Tech | RDS Aurora Postgres 15 (2 AZ, 1 read replica) |
| Port | `5432` |
| Used by | Auth, Tenant, Provisioning, Billing, Router (idle reaper), Upgrade |

Schema:

```sql
-- Core tables
customers       (customer_id, tier, airflow_version, git_repo_url,
                 idle_timeout_override, feature_flags, created_at)

agents          (customer_id PK, state, agent_url, pod_name,
                 instance_id, test_mode_active, last_request_at,
                 created_at, updated_at)

environments    (id, customer_id, namespace, helm_release,
                 airflow_version, status, created_at)

dag_bundles     (id, customer_id, s3_key, version, checksum,
                 promoted_at, created_at)

upgrade_jobs    (id, customer_id, from_version, to_version,
                 state, started_at, completed_at, rollback_at)

audit_log       (ts, customer_id, path, method, status_code,
                 latency_ms, request_id, agent_url)
```

---

#### Redis registry

| Field | Value |
|---|---|
| Tech | ElastiCache Redis 7 (3-node Sentinel for HA) |
| Port | `6379` |
| Used by | Control plane router (hot path), warm pool controller, rate limiter |

Key patterns:

```
agent:registry:{customer_id}  →  agent_url        TTL: 3600s (refreshed per request)
agent:state:{customer_id}     →  dormant|warming|active|idle  TTL: 7200s
ratelimit:{customer_id}       →  token bucket state           TTL: 60s
session:{token_hash}          →  customer_id                  TTL: 900s
```

Rule: Redis is never the source of truth. All state has a TTL. The DB is always authoritative.

---

#### Vault

| Field | Value |
|---|---|
| Tech | HashiCorp Vault 1.16 (HA, Raft integrated storage, 3 nodes) |
| Port | `8200` |
| Used by | All agents, provisioning service, secret service |

Secret paths per customer:

```
secret/platform/customers/{id}/fernet-key        Airflow fernet encryption key
secret/platform/customers/{id}/db-password       Tenant Postgres password
secret/platform/customers/{id}/git-credentials   Git deploy key or PAT
secret/platform/customers/{id}/airflow-basic-auth  Webserver basic auth
```

Auth: AppRole per agent — each agent gets a `role_id` (stored in K8s secret) and fetches its own `secret_id` from Vault on boot using instance metadata. Vault audit log captures every secret read. Dynamic DB credentials via the database secrets engine prevent password sprawl.

---

#### Per-tenant data stores (inside each K8s namespace)

| Store | Tech | Purpose |
|---|---|---|
| Tenant Postgres | CloudNativePG 1.23 (1 primary + 1 replica) | Airflow metadata DB — DAG runs, task instances, connections, variables |
| Tenant Redis | Redis 7 standalone | Celery broker + result backend (if CeleryExecutor). Not needed for KubernetesExecutor |
| Task log storage | S3 (`platform-logs/{customer_id}/`) | Airflow workers write task logs via S3RemoteLogging |

CloudNativePG config: automated backups to S3 every 6 hours, PITR (7-day window), PgBouncer connection pooler sidecar. Resources: Starter: 0.5 CPU / 1 GB. Standard: 1 CPU / 2 GB. Enterprise: 2 CPU / 4 GB.

---

#### S3 buckets

| Bucket | Purpose |
|---|---|
| `platform-dags-{env}` | DAG bundles uploaded by DAG service, versioned |
| `platform-logs-{env}` | Airflow task logs (S3RemoteLogging) |
| `platform-backups` | CloudNativePG WAL archive + base backups |
| `platform-tf-state` | Terraform state (DynamoDB lock table for concurrency) |
| `platform-artifacts` | Docker build cache, Helm chart repo |

---

### 14.5 Observability Stack

All components deployed via `kube-prometheus-stack` Helm chart + Loki + Tempo.

| Service | Tech | Port | Purpose |
|---|---|---|---|
| Prometheus | Prometheus 2.x + Alertmanager | `9090` | Scrapes metrics from all services and agents |
| Loki | Grafana Loki | `3100` | Log aggregation (Fluent Bit DaemonSet ships logs) |
| Grafana | Grafana 10.x | `3000` | Dashboards for ops team + customer-facing panels |
| Tempo | Grafana Tempo | `3200` | Distributed tracing via `X-Trace-ID` propagation |
| Fluent Bit | DaemonSet on every node | — | Ships structured JSON logs from all pods to Loki |

Key metrics exposed by the platform:

```
platform_agent_state{customer_id, state}              gauge
platform_warm_pool_size                               gauge
platform_warm_pool_acquisitions_total                 counter
platform_request_latency_seconds{customer_id, path}  histogram
platform_agent_idle_seconds{customer_id}              gauge
platform_test_deployments_active                      gauge
platform_ec2_warm_pool_size{state}                    gauge
airflow_dag_run_duration_seconds{customer_id, dag_id} histogram
airflow_scheduler_heartbeat{customer_id}              gauge
```

Alertmanager routing: P1 (agent registry down, DB unreachable, warm pool empty) → PagerDuty. P2 (pool below min, upgrade cohort stalled, EC2 provision timeout) → Slack `#platform-alerts`.

---

### 14.6 Infrastructure & Ops

#### Kubernetes (EKS)

| Node pool | Instance type | Scheduling | Purpose |
|---|---|---|---|
| system | m5.large, on-demand | — | Control plane services, Vault, observability |
| agents | m5.xlarge, on-demand | Taint: `agent=dind:NoSchedule` | Standard agent pods + DinD sidecars |
| workers | c5.2xlarge, Spot | — | Airflow task pods (KubernetesExecutor) |

Karpenter manages dynamic node provisioning for the workers pool — scales to zero when no tasks are running, provisions within 30 seconds when tasks are queued.

---

#### ArgoCD (GitOps)

App of Apps pattern — one root ArgoCD Application per environment watches `platform-infra` Git repo:

```
platform-infra/
├── apps/
│   ├── control-plane-services/    Auth, Tenant, Provisioning, Billing, Router
│   ├── agent-pool/                Pool controller, warm pods
│   ├── tenant-namespaces/         Generated per customer (Helm chart)
│   ├── observability/             kube-prometheus-stack, Loki, Tempo
│   ├── cert-manager/
│   └── karpenter/
├── helm/                          Airflow chart + per-tier values overrides
├── k8s/                           NetworkPolicy, RBAC, PodDisruptionBudget
└── packer/                        EC2 agent AMI definition
```

Auto-sync on merge to `main`. Manual sync gate for prod. Sync wave annotations ensure order: Vault → DB → services → agent pool.

---

#### NGINX ingress controller

Wildcard routing: `*.airflow.platform.com → NGINX → tenant webserver`. The provisioning service auto-creates an `Ingress` object per tenant:

```yaml
rules:
  - host: cust-001.airflow.platform.com
    http:
      paths:
        - path: /
          backend:
            service:
              name: airflow-webserver
              namespace: cust-001
              port: 8080
```

---

#### cert-manager

`ClusterIssuer: letsencrypt-prod` using Route53 DNS01 challenge. Wildcard cert covers `*.airflow.platform.com`. `Certificate` CRDs created by provisioning service per tenant. Auto-renews 30 days before expiry. Cert stored as K8s secret in the tenant namespace.

---

#### EC2 ASG warm pool (enterprise tier)

```bash
aws autoscaling put-warm-pool \
  --auto-scaling-group-name enterprise-agent-asg \
  --pool-state Stopped \
  --min-size 5 \
  --max-group-prepared-capacity 20 \
  --instance-reuse-policy ReuseOnScaleIn=true
```

Launch template: agent AMI (see Packer pipeline), `t3.medium`, private subnet, `enterprise-agent-ec2` IAM role, user data reads customer_id from SSM and starts the agent systemd service.

---

#### IAM roles (IRSA — pod-level)

| Role | Permissions | Attached to |
|---|---|---|
| `platform-control-plane` | EKS, RDS, ElastiCache, SSM read/write | Control plane service pods |
| `platform-agent-pod` | Vault AppRole auth, S3 DAG read | Standard agent pods |
| `enterprise-agent-ec2` | SSM GetParameter, Vault AppRole auth, S3 read | EC2 agent instances |
| `karpenter-controller` | EC2 full (node provisioning) | Karpenter pod |
| `cloudnativepg` | S3 write (backup), S3 read (restore) | CloudNativePG pods |

---

#### Packer AMI pipeline

Triggered on every agent release tag in GitHub Actions:

```json
{
  "builders": [{ "type": "amazon-ebs", "instance_type": "t3.small",
    "source_ami_filter": { "filters": { "name": "ubuntu/images/hvm-ssd/ubuntu-22.04-*" }}
  }],
  "provisioners": [
    { "type": "shell", "script": "scripts/install-docker.sh"   },
    { "type": "shell", "script": "scripts/install-agent.sh"    },
    { "type": "shell", "script": "scripts/configure-vault.sh"  },
    { "type": "shell", "script": "scripts/configure-systemd.sh"}
  ]
}
```

Target: AMI boot-to-serving time < 90 seconds.

---

#### CI/CD pipeline (GitHub Actions)

| Trigger | Pipeline | Actions |
|---|---|---|
| PR opened | `ci-check` | pytest, mypy, docker build (no push) |
| Merge to `main` | `ci-deploy` | Push ECR, trigger ArgoCD sync (dev/staging) |
| Release tag | `ci-release` | Helm chart publish, Packer AMI bake, ArgoCD sync (prod, manual gate) |
| Nightly | `ci-security` | Trivy image scan, dep audit, integration tests against dev cluster |
| PR with `terraform/` changes | Atlantis | `terraform plan` on PR, `terraform apply` on merge |

---

### 14.7 Service Dependency Map

```
Customer request
  └─► API gateway
        ├─► Auth service        (Postgres, Redis session cache)
        ├─► Tenant service      (Postgres)
        ├─► Control plane router
        │     ├─► Redis registry          (hot path lookup)
        │     ├─► Warm pool controller    (standard tier miss)
        │     │     └─► K8s API server
        │     ├─► EC2 provisioner         (enterprise tier miss)
        │     │     └─► AWS EC2 ASG + SSM
        │     └─► Customer agent
        │           ├─► Vault             (secrets on init)
        │           ├─► DinD sidecar      (test mode)
        │           └─► Airflow webserver (prod proxy)
        ├─► DAG service         (S3, Postgres, git-sync trigger)
        ├─► Billing service     (Postgres, Stripe API, SNS)
        └─► Notification svc    (SES, Slack, SNS subscriber)

Provisioning service  (K8s API, Helm, Vault, CloudNativePG operator, cert-manager)
Upgrade service       (Helm, Postgres, SNS)
Observability         (Prometheus ← all services, Loki ← Fluent Bit, Tempo ← X-Trace-ID)
```

---

### 14.8 Event Bus

All async communication between services goes through SNS topics + SQS queues. This decouples services so a slow billing write never blocks a provisioning call.

| Topic | Event | Published by | Consumed by |
|---|---|---|---|
| `tenant-events` | `TenantCreated` | Tenant service | Provisioning service |
| `tenant-events` | `TenantDeleted` | Tenant service | Provisioning service, Billing service |
| `environment-events` | `EnvironmentReady` | Provisioning service | Billing service, Notification service |
| `environment-events` | `EnvironmentUnhealthy` | Agent (health check) | Notification service |
| `agent-events` | `AgentSessionStarted` | Control plane router | Billing service |
| `agent-events` | `AgentSessionEnded` | Idle reaper | Billing service |
| `dag-events` | `DAGRunFailed` | Airflow (webhook) | Notification service |
| `dag-events` | `SLAMiss` | Airflow (webhook) | Notification service |
| `upgrade-events` | `UpgradeCompleted` | Upgrade service | Notification service |
| `upgrade-events` | `UpgradeRolledBack` | Upgrade service | Notification service, on-call |

---

## 15. Security

### Secret management

The agent never stores secrets in environment variables or files on disk. All secrets are fetched from Vault on initialization and held in process memory only.

```
Boot sequence:
  Agent → Vault (AppRole auth)
        ← customer-specific secret bundle
  Agent → holds in memory for session duration
  Agent → zeroes memory on drain()
```

Vault paths follow the pattern:

```
secret/platform/customers/{customer_id}/airflow-fernet-key
secret/platform/customers/{customer_id}/db-password
secret/platform/customers/{customer_id}/git-credentials
```

### Network isolation

The agent runs in the `platform-agents` namespace, separated from tenant namespaces by Kubernetes `NetworkPolicy`.

```yaml
# Agent can only reach its own customer's namespace
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agent-egress-policy
  namespace: platform-agents
spec:
  podSelector:
    matchLabels:
      customer-id: cust-001
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              customer-id: cust-001    # only its own tenant NS
      ports:
        - port: 8080                   # Airflow webserver only
```

### DinD security

Docker-in-Docker requires a privileged container. To contain the blast radius:

- DinD runs in a separate container with its own cgroup namespace.
- The DinD container has no access to host mounts other than its own `emptyDir` volume.
- Images pulled by the DinD daemon are restricted by a registry allowlist enforced via Docker daemon config.
- Test containers have no egress to the internet — only the internal artifact registry.

### Authentication flow

```
Customer SDK/CLI
  → Bearer token (platform-issued JWT)
  → Control Plane (validates JWT, extracts customer_id)
  → Agent (verifies customer_id matches, injects Airflow credentials)
  → Airflow Webserver (Basic auth, never exposed externally)
```

---

## 16. Observability

### Metrics (Prometheus)

Each agent exposes the following metrics on `:9000/metrics`:

```
platform_agent_state{customer_id, state}          gauge
platform_agent_requests_total{customer_id, path}  counter
platform_agent_request_latency_seconds{customer_id, path, quantile} histogram
platform_agent_idle_seconds{customer_id}          gauge
platform_agent_test_deployments_active            gauge
platform_warm_pool_size                           gauge
platform_warm_pool_acquisitions_total             counter
```

### Logs (structured JSON → Loki)

Every request the agent handles is logged as a structured JSON line:

```json
{
  "ts": "2026-03-22T10:34:12Z",
  "level": "info",
  "event": "request_proxied",
  "customer_id": "cust-001",
  "method": "POST",
  "path": "/api/v1/dags/my_dag/dagRuns",
  "status": 200,
  "latency_ms": 42,
  "mode": "prod",
  "request_id": "req_abc123"
}
```

### Distributed tracing

The agent propagates `X-Trace-ID` headers through to the Airflow webserver. Traces are collected by Tempo and visible in Grafana alongside logs and metrics.

### Agent health dashboard (Grafana)

Key panels to include:

- Warm pool size over time
- Active agents by customer tier
- P95 routing latency (dormant → first response)
- Test deployment count and TTL distribution
- Agent reap rate per hour
- DinD container error rate

---

## 17. Failure Modes & Mitigations

| Failure | Impact | Mitigation |
|---|---|---|
| Agent pod crashes | Customer requests fail until re-warmed | Control plane detects 503 → re-routes to new warm pod within < 5s |
| EC2 agent instance terminated unexpectedly | Enterprise customer session lost | Control plane detects deregistration → re-provisions EC2; notify customer |
| EC2 provision timeout (> 120s) | Enterprise customer sees slow login | Retry with different AZ; fall back to pod agent temporarily |
| Warm pool exhausted | Cold start latency spikes to 15s | Alert on pool < min_size; autoscale pool; queue requests briefly |
| Vault unreachable | Agent cannot initialize | Cached read from Vault Agent sidecar with short TTL; alert page |
| DinD crash during test | Test deployment lost | Detect via health check; return error with instructions to retry |
| Control plane registry lost | All routing breaks | Redis Sentinel / cluster for HA; agents re-register on heartbeat |
| Airflow namespace unhealthy | Proxy returns 502 | Agent detects and surfaces clear error; does not mask K8s issue |
| Agent memory leak | Pod OOM killed | Memory limit enforced; liveness probe restarts pod; pool refills |

### Agent liveness probe

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 9000
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

The `/healthz` endpoint returns 200 only if the agent's core loop is functional. An unhealthy agent is killed and the warm pool controller spawns a replacement.

---

## 18. Cost Model

### Standard tier — pod agent infrastructure (AWS us-east-1 estimate)

| Component | Count | Spec | Monthly cost |
|---|---|---|---|
| Warm pool pods | 25 | 0.1 vCPU / 128 MB | ~$8 |
| Active agent pods | 75 avg | 0.2 vCPU / 256 MB | ~$60 |
| DinD storage (emptyDir) | 25 active | 2 GB ephemeral | ~$0 |
| Pool controller | 1 | 0.1 vCPU / 128 MB | ~$3 |
| **Total standard agent overhead** | | | **~$71/month** |

### Enterprise tier — EC2 agent cost model

EC2 agents are provisioned on demand and billed per session. Cost is passed through to the customer as a usage charge.

| Component | Spec | Hourly cost | Notes |
|---|---|---|---|
| EC2 instance | t3.medium (2 vCPU / 4 GB) | ~$0.047/hr | On-demand; use Spot for non-critical |
| EBS root volume | 20 GB gp3 | ~$0.002/hr | Terminated with instance |
| Data transfer | varies | ~$0.01/GB | Outbound only |
| **Typical 4-hour session** | | **~$0.25** | Well within enterprise pricing |

A typical enterprise customer using the agent for 20 hours/month costs ~$1.00 in EC2 compute — easily absorbed into an enterprise plan.

### Comparison: on-demand pod model vs always-on EC2

```
EC2 always-on (1,000 agents):    1,000 × t3.small × $15/mo  = $15,000/mo
EC2 on-demand (100 active):        100 × t3.small × $15/mo  =  $1,500/mo

Pod always-on (1,000 agents):    1,000 × 128MB pod           =    $100/mo
Pod on-demand warm pool model:      25 warm + 75 active pods  =     $71/mo
```

The pod on-demand model is **200x cheaper** than EC2 always-on.

---

## 19. Implementation Phases

### Phase 1 — Sidecar MVP (weeks 1–6)

- Deploy agent as a sidecar in every Airflow webserver pod.
- Agent is always-on but lightweight (64 MB idle).
- Implements: prod proxy, health check, structured logging.
- No test mode, no warm pool.
- Goal: prove routing model and auth flow work end-to-end.

### Phase 2 — Test Mode (weeks 7–12)

- Add DinD sidecar to agent pod.
- Implement `start_test_deployment`, `stop_test_deployment`.
- Expose test endpoint in platform API.
- Add TTL enforcement and auto-teardown.
- Goal: customers can validate DAGs before promoting to prod.

### Phase 3 — On-Demand with Warm Pool (weeks 13–20)

- Remove always-on sidecar from Airflow webserver pods.
- Build pool controller and warm pool.
- Build agent registry in Redis.
- Build control plane router with dormant → warm → active flow.
- Add idle reaper cron.
- Goal: 10x reduction in agent pod count at steady state.

### Phase 4 — Hardening + Enterprise EC2 (weeks 21–30)

- Vault integration for secret injection.
- Per-customer NetworkPolicy enforcement.
- Full Prometheus metrics + Grafana dashboards.
- Load test: 1,000 customers, 200 concurrent active sessions.
- Chaos engineering: kill warm pool pods, Vault outage, DinD crash.
- **EC2 enterprise agent**: Packer AMI pipeline, EC2 provisioning API, idle reaper for instances.
- **EC2 billing integration**: per-session-hour metering via Stripe usage records.
- **AMI freshness**: automated weekly AMI rebuilds triggered by agent image releases.
- Goal: production-ready, SLA-backed, enterprise tier live.

---

## 20. Open Questions

| Question | Owner | Target date |
|---|---|---|
| Should warm pool pods run in a dedicated node pool (to avoid noisy neighbor on DinD)? | Infra team | Phase 3 kickoff |
| What is the right idle timeout? 5 min (lower cost) vs 15 min (lower cold start rate)? | Product | Phase 3 kickoff |
| Should we offer test mode for Starter tier customers, or only Standard and above? | Product | Phase 2 kickoff |
| Is Podman-rootless a viable alternative to DinD for security-sensitive customers? | Security | Phase 2 |
| Should the agent be written in Go (lower memory) or Python (faster iteration)? | Platform team | Phase 1 kickoff |
| How do we handle agent state during a rolling deploy of the agent image itself? | Platform team | Phase 3 |
| For EC2 enterprise agents — should we use Spot instances with fallback to on-demand? | Infra team | Phase 4 |
| What is the threshold for offering EC2 agent? Enterprise plan only, or a self-serve add-on? | Product | Phase 3 kickoff |
| Should EC2 agents use a shared AMI or customer-specific AMIs for stronger isolation? | Security | Phase 4 |

---

*End of document. For questions, open a discussion in #platform-infra or tag @platform-team in Notion.*
