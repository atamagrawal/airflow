# Per-Customer Agent Architecture — Design Document

**Version:** 1.0  
**Status:** Draft  
**Authors:** Platform Team  
**Last Updated:** 2026-03-22

---

## Table of Contents

1. [Overview](#overview)
2. [Goals and Non-Goals](#goals-and-non-goals)
3. [Architecture Overview](#architecture-overview)
4. [Agent Lifecycle](#agent-lifecycle)
5. [On-Demand Agent Model](#on-demand-agent-model)
6. [Agent Internals](#agent-internals)
7. [Test Deployment Mode](#test-deployment-mode)
8. [Production Proxy Mode](#production-proxy-mode)
9. [Control Plane & Routing](#control-plane--routing)
10. [Warm Pool Design](#warm-pool-design)
11. [Sidecar Alternative](#sidecar-alternative)
12. [Security](#security)
13. [Observability](#observability)
14. [Failure Modes & Mitigations](#failure-modes--mitigations)
15. [Cost Model](#cost-model)
16. [Implementation Phases](#implementation-phases)
17. [Open Questions](#open-questions)

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

## 4. Agent Lifecycle

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

## 5. On-Demand Agent Model

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

## 6. Agent Internals

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

## 7. Test Deployment Mode

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

## 8. Production Proxy Mode

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

## 9. Control Plane & Routing

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

## 10. Warm Pool Design

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

## 11. Sidecar Alternative

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

## 12. Security

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

## 13. Observability

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

## 14. Failure Modes & Mitigations

| Failure | Impact | Mitigation |
|---|---|---|
| Agent pod crashes | Customer requests fail until re-warmed | Control plane detects 503 → re-routes to new warm pod within < 5s |
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

## 15. Cost Model

### Agent infrastructure cost (AWS us-east-1 estimate)

| Component | Count | Spec | Monthly cost |
|---|---|---|---|
| Warm pool pods | 25 | 0.1 vCPU / 128 MB | ~$8 |
| Active agent pods | 75 avg | 0.2 vCPU / 256 MB | ~$60 |
| DinD storage (emptyDir) | 25 active | 2 GB ephemeral | ~$0 |
| Pool controller | 1 | 0.1 vCPU / 128 MB | ~$3 |
| **Total agent overhead** | | | **~$71/month** |

This compares to the always-on model:
- 1,000 pods × $0.10/month (128 MB each) = **~$100+/month in compute alone**, with added K8s API server pressure.

The on-demand model pays for itself immediately at scale.

---

## 16. Implementation Phases

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

### Phase 4 — Hardening (weeks 21–26)

- Vault integration for secret injection.
- Per-customer NetworkPolicy enforcement.
- Full Prometheus metrics + Grafana dashboards.
- Load test: 1,000 customers, 200 concurrent active sessions.
- Chaos engineering: kill warm pool pods, Vault outage, DinD crash.
- Goal: production-ready, SLA-backed.

---

## 17. Open Questions

| Question | Owner | Target date |
|---|---|---|
| Should warm pool pods run in a dedicated node pool (to avoid noisy neighbor on DinD)? | Infra team | Phase 3 kickoff |
| What is the right idle timeout? 5 min (lower cost) vs 15 min (lower cold start rate)? | Product | Phase 3 kickoff |
| Should we offer test mode for Starter tier customers, or only Standard and above? | Product | Phase 2 kickoff |
| Is Podman-rootless a viable alternative to DinD for security-sensitive customers? | Security | Phase 2 |
| Should the agent be written in Go (lower memory) or Python (faster iteration)? | Platform team | Phase 1 kickoff |
| How do we handle agent state during a rolling deploy of the agent image itself? | Platform team | Phase 3 |

---

*End of document. For questions, open a discussion in #platform-infra or tag @platform-team in Notion.*
