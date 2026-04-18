# Airflow Test Deployment on On-Demand EC2 — Design Document

**Version:** 1.0  
**Date:** March 2026  
**Status:** Draft

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [Architecture](#3-architecture)
4. [Component Design](#4-component-design)
5. [EC2 On-Demand Launch](#5-ec2-on-demand-launch)
6. [Docker Compose Setup](#6-docker-compose-setup)
7. [Session Lifecycle Management](#7-session-lifecycle-management)
8. [API Layer](#8-api-layer)
9. [Security](#9-security)
10. [Cost Optimization](#10-cost-optimization)
11. [Deployment Guide](#11-deployment-guide)
12. [Monitoring & Observability](#12-monitoring--observability)

---

## 1. Overview

This document describes the design for an **on-demand, per-session Airflow test deployment system** for customers. Inspired by Astronomer's ephemeral test deployment feature, this system launches a fresh EC2 instance running Airflow inside Docker Compose for each test session, and tears it down automatically when the session ends.

### Key Principle

> One session = One EC2 instance = One isolated Airflow environment

Each customer gets a **clean, isolated, short-lived** Airflow environment — no shared infrastructure, no state bleed between sessions.

---

## 2. Goals & Non-Goals

### Goals
- Launch a fully functional Airflow environment on-demand per customer session
- Automatically terminate the EC2 instance after session timeout or explicit end
- Provide the customer with a URL to access the Airflow UI
- Keep the environment clean — no state from previous sessions
- Minimize cost by using EC2 spot/on-demand only during active sessions

### Non-Goals
- This is NOT a production Airflow environment
- No auto-scaling within a session (single EC2 per session is sufficient)
- No persistent DAG storage between sessions (customers must re-upload DAGs each session)
- No multi-user concurrent access within a single session

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Customer                              │
│                    (Browser / API Client)                    │
└─────────────────────┬───────────────────────────────────────┘
                      │  POST /sessions/start
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                   Session Manager API                        │
│                (FastAPI on EC2/ECS/Lambda)                   │
│                                                             │
│  - Validates customer request                               │
│  - Calls AWS SDK to launch EC2                              │
│  - Tracks session state in DynamoDB                         │
│  - Returns Airflow URL + session ID to customer             │
└─────────────────────┬───────────────────────────────────────┘
                      │  AWS SDK (boto3)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                     AWS EC2 (On-Demand)                      │
│                    t3.large per session                      │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Docker Compose Stack                    │   │
│  │                                                     │   │
│  │  ┌──────────────┐  ┌──────────────┐                │   │
│  │  │  Webserver   │  │  Scheduler   │                │   │
│  │  │  :8080       │  │              │                │   │
│  │  └──────────────┘  └──────────────┘                │   │
│  │  ┌──────────────┐  ┌──────────────┐                │   │
│  │  │   Worker     │  │  PostgreSQL  │                │   │
│  │  │  (CeleryExec)│  │  (Metadata)  │                │   │
│  │  └──────────────┘  └──────────────┘                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  - TTL watchdog: auto docker compose down + instance stop   │
└─────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                      DynamoDB                               │
│         Session State: {session_id, instance_id,            │
│          airflow_url, status, created_at, ttl}              │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Component Design

| Component | Technology | Purpose |
|---|---|---|
| Session Manager API | FastAPI (Python) | Orchestrates session lifecycle |
| EC2 Launch | AWS boto3 SDK | Provisions on-demand EC2 per session |
| Airflow Runtime | Docker Compose on EC2 | Runs isolated Airflow stack |
| Session State | AWS DynamoDB | Tracks session metadata and TTL |
| Secrets | AWS Secrets Manager | Stores Airflow fernet key, DB passwords |
| Networking | AWS Security Groups | Controls inbound access to Airflow UI |
| TTL Watchdog | Bash script on EC2 | Auto-terminates idle sessions |

---

## 5. EC2 On-Demand Launch

### 5.1 EC2 Specification

| Parameter | Value | Notes |
|---|---|---|
| Instance Type | `t3.large` | 2 vCPU, 8 GB RAM — sufficient for single-user Airflow |
| AMI | `ami-ubuntu-22.04-latest` | Use latest Ubuntu 22.04 LTS |
| Storage | 20 GB gp3 EBS | Enough for Docker images + logs |
| Lifecycle | On-Demand | Predictable, no interruption risk |
| Region | Same as customer | Minimize latency |

> **Why not Spot?** Spot instances can be interrupted mid-session. For test environments where a customer is actively working, interruptions are unacceptable. Use On-Demand for reliability.

### 5.2 EC2 Launch Code (boto3)

```python
# launch_instance.py
import boto3
import base64
import json
import time
from datetime import datetime, timezone

def launch_airflow_instance(session_id: str, customer_id: str, ttl_minutes: int = 60) -> dict:
    """
    Launch an on-demand EC2 instance with Airflow running via Docker Compose.
    Returns instance details including the public DNS for Airflow UI access.
    """
    ec2 = boto3.client("ec2", region_name="us-east-1")

    user_data_script = generate_user_data(session_id, customer_id, ttl_minutes)
    user_data_encoded = base64.b64encode(user_data_script.encode()).decode()

    response = ec2.run_instances(
        ImageId="ami-0c7217cdde317cfec",   # Ubuntu 22.04 LTS (us-east-1)
        InstanceType="t3.large",
        MinCount=1,
        MaxCount=1,
        KeyName="airflow-test-keypair",    # Your keypair name
        SecurityGroupIds=["sg-airflow-test"],  # See Section 9 for rules
        IamInstanceProfile={"Name": "AirflowTestInstanceProfile"},
        UserData=user_data_encoded,
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": 20,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,  # Auto-cleanup on termination
                },
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"airflow-test-{session_id}"},
                    {"Key": "SessionId", "Value": session_id},
                    {"Key": "CustomerId", "Value": customer_id},
                    {"Key": "Purpose", "Value": "airflow-test-deployment"},
                    {"Key": "CreatedAt", "Value": datetime.now(timezone.utc).isoformat()},
                ],
            }
        ],
        InstanceInitiatedShutdownBehavior="terminate",  # Auto-terminate on shutdown
    )

    instance = response["Instances"][0]
    instance_id = instance["InstanceId"]

    # Wait for instance to be running and get public DNS
    print(f"Waiting for instance {instance_id} to be running...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])

    # Fetch public DNS
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    public_dns = desc["Reservations"][0]["Instances"][0]["PublicDnsName"]
    public_ip = desc["Reservations"][0]["Instances"][0]["PublicIpAddress"]

    return {
        "instance_id": instance_id,
        "public_dns": public_dns,
        "public_ip": public_ip,
        "airflow_url": f"http://{public_dns}:8080",
        "session_id": session_id,
    }


def terminate_instance(instance_id: str):
    """Terminate a specific EC2 instance."""
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.terminate_instances(InstanceIds=[instance_id])
    print(f"Terminated instance: {instance_id}")
```

### 5.3 User Data Script Generator

The EC2 User Data script runs at boot and sets up Docker + Airflow automatically:

```python
# user_data.py
def generate_user_data(session_id: str, customer_id: str, ttl_minutes: int) -> str:
    """
    Generate the EC2 User Data shell script that bootstraps Docker and Airflow.
    This runs automatically when the EC2 instance first boots.
    """
    return f"""#!/bin/bash
set -e
exec > /var/log/airflow-setup.log 2>&1

echo "=== Airflow Test Deployment Bootstrap ==="
echo "Session ID: {session_id}"
echo "Customer ID: {customer_id}"
echo "TTL: {ttl_minutes} minutes"
echo "Started at: $(date)"

# ── 1. System updates & Docker install ──────────────────────────
apt-get update -y
apt-get install -y apt-transport-https ca-certificates curl software-properties-common

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -
add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu focal stable"
apt-get update -y
apt-get install -y docker-ce docker-compose-plugin

systemctl start docker
systemctl enable docker

# ── 2. Create working directory ──────────────────────────────────
mkdir -p /opt/airflow/dags
mkdir -p /opt/airflow/logs
mkdir -p /opt/airflow/plugins

# Set permissions for Airflow UID 50000
chown -R 50000:0 /opt/airflow

# ── 3. Write Docker Compose file ────────────────────────────────
cat > /opt/airflow/docker-compose.yml << 'COMPOSE_EOF'
{generate_docker_compose()}
COMPOSE_EOF

# ── 4. Write .env file ──────────────────────────────────────────
cat > /opt/airflow/.env << ENV_EOF
AIRFLOW_UID=50000
SESSION_ID={session_id}
CUSTOMER_ID={customer_id}
ENV_EOF

# ── 5. Start Airflow ────────────────────────────────────────────
cd /opt/airflow
docker compose up airflow-init
docker compose up -d
echo "Airflow started successfully at $(date)"

# ── 6. Wait for Airflow webserver to be healthy ──────────────────
echo "Waiting for Airflow webserver to be ready..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8080/health | grep -q "healthy"; then
        echo "Airflow is ready!"
        break
    fi
    echo "Attempt $i/30 - not ready yet, waiting 10s..."
    sleep 10
done

# ── 7. Write session metadata ────────────────────────────────────
cat > /opt/airflow/session.json << META_EOF
{{
  "session_id": "{session_id}",
  "customer_id": "{customer_id}",
  "ttl_minutes": {ttl_minutes},
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}}
META_EOF

# ── 8. Install TTL watchdog ──────────────────────────────────────
cat > /opt/airflow/watchdog.sh << 'WATCHDOG_EOF'
#!/bin/bash
TTL_SECONDS=$(({ttl_minutes} * 60))
echo "Watchdog started. Will shutdown in $TTL_SECONDS seconds."
sleep $TTL_SECONDS
echo "TTL expired. Shutting down Airflow and terminating instance..."
cd /opt/airflow
docker compose down -v  # -v removes volumes for clean state
shutdown -h now          # Triggers EC2 termination (InstanceInitiatedShutdownBehavior=terminate)
WATCHDOG_EOF

chmod +x /opt/airflow/watchdog.sh
nohup /opt/airflow/watchdog.sh > /var/log/airflow-watchdog.log 2>&1 &

echo "=== Bootstrap complete at $(date) ==="
"""
```

---

## 6. Docker Compose Setup

### 6.1 Docker Compose File

```python
# docker_compose_template.py
def generate_docker_compose() -> str:
    return """
x-airflow-common: &airflow-common
  image: apache/airflow:2.9.2
  environment:
    AIRFLOW__CORE__EXECUTOR: LocalExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CORE__FERNET_KEY: ""
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: "true"
    AIRFLOW__CORE__LOAD_EXAMPLES: "false"
    AIRFLOW__WEBSERVER__EXPOSE_CONFIG: "true"
    # Disable scheduled runs — test sessions trigger DAGs manually
    AIRFLOW__SCHEDULER__USE_JOB_SCHEDULE: "false"
    AIRFLOW__SCHEDULER__MIN_FILE_PROCESS_INTERVAL: "10"
  volumes:
    - ./dags:/opt/airflow/dags
    - ./logs:/opt/airflow/logs
    - ./plugins:/opt/airflow/plugins
  depends_on:
    postgres:
      condition: service_healthy

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 10s
      retries: 5
      start_period: 5s
    restart: always

  airflow-init:
    <<: *airflow-common
    entrypoint: /bin/bash
    command:
      - -c
      - |
        airflow db migrate
        airflow users create \\
          --username admin \\
          --password admin \\
          --firstname Test \\
          --lastname User \\
          --role Admin \\
          --email admin@example.com
    restart: "no"

  airflow-webserver:
    <<: *airflow-common
    command: webserver
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    restart: always

  airflow-scheduler:
    <<: *airflow-common
    command: scheduler
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8974/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    restart: always

volumes:
  postgres_data:
"""
```

### 6.2 Default Airflow Configuration Overrides

The following settings are intentionally different from production — they are optimized for test sessions:

| Setting | Test Value | Production Default | Reason |
|---|---|---|---|
| `USE_JOB_SCHEDULE` | `false` | `true` | Prevent auto-scheduling; only manual triggers |
| `LOAD_EXAMPLES` | `false` | `false` | No noise from example DAGs |
| `EXECUTOR` | `LocalExecutor` | `CeleryExecutor` | Simpler, no Redis/Celery needed for single-user |
| `EXPOSE_CONFIG` | `true` | `false` | Transparency for customer debugging |
| `DAGS_ARE_PAUSED_AT_CREATION` | `true` | `true` | Require explicit DAG enable |

---

## 7. Session Lifecycle Management

### 7.1 Session States

```
REQUESTED → LAUNCHING → READY → ACTIVE → TERMINATING → TERMINATED
                                    ↑
                              (TTL reset on activity)
```

| State | Description |
|---|---|
| `REQUESTED` | Customer called API, EC2 launch initiated |
| `LAUNCHING` | EC2 booting, Docker Compose starting |
| `READY` | Airflow UI accessible, URL returned to customer |
| `ACTIVE` | Customer is using the session |
| `TERMINATING` | TTL expired or customer ended session |
| `TERMINATED` | EC2 terminated, all resources cleaned up |

### 7.2 Session Manager (DynamoDB)

```python
# session_store.py
import boto3
from datetime import datetime, timezone
from typing import Optional

TABLE_NAME = "airflow-test-sessions"

class SessionStore:
    def __init__(self):
        self.dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        self.table = self.dynamodb.Table(TABLE_NAME)

    def create_session(self, session_id: str, customer_id: str, ttl_minutes: int) -> dict:
        now = datetime.now(timezone.utc)
        ttl_epoch = int(now.timestamp()) + (ttl_minutes * 60)
        
        item = {
            "session_id": session_id,
            "customer_id": customer_id,
            "status": "REQUESTED",
            "created_at": now.isoformat(),
            "ttl": ttl_epoch,          # DynamoDB TTL auto-deletes expired records
            "instance_id": None,
            "airflow_url": None,
        }
        self.table.put_item(Item=item)
        return item

    def update_session(self, session_id: str, updates: dict):
        update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
        expr_names = {f"#{k}": k for k in updates}
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
                ":s1": "LAUNCHING",
                ":s2": "READY",
                ":s3": "ACTIVE",
            },
        )
        return response.get("Items", [])
```

### 7.3 TTL Watchdog (On EC2)

This script runs on the EC2 instance itself and is the last line of defense for cleanup:

```bash
#!/bin/bash
# /opt/airflow/watchdog.sh
# Runs as a background process from User Data on boot

SESSION_ID=$(cat /opt/airflow/session.json | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
TTL_MINUTES=$(cat /opt/airflow/session.json | python3 -c "import sys,json; print(json.load(sys.stdin)['ttl_minutes'])")
TTL_SECONDS=$((TTL_MINUTES * 60))

echo "[watchdog] Session $SESSION_ID — TTL is ${TTL_MINUTES} minutes"
echo "[watchdog] Will terminate at: $(date -d "+${TTL_MINUTES} minutes")"

sleep $TTL_SECONDS

echo "[watchdog] TTL expired at $(date). Starting cleanup..."

# Step 1: Stop Airflow containers and remove volumes
cd /opt/airflow
docker compose down -v
echo "[watchdog] Docker containers stopped and volumes removed"

# Step 2: Notify session manager API (optional, best-effort)
curl -s -X POST "https://your-api.example.com/sessions/${SESSION_ID}/terminated" \
  -H "Content-Type: application/json" \
  -d '{"reason": "ttl_expired"}' || true

# Step 3: Shutdown the EC2 instance
# EC2 is configured with InstanceInitiatedShutdownBehavior=terminate
# so this will fully terminate (not just stop) the instance
echo "[watchdog] Shutting down instance..."
shutdown -h now
```

---

## 8. API Layer

### 8.1 FastAPI Session Manager

```python
# main.py
import uuid
import asyncio
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from launch_instance import launch_airflow_instance, terminate_instance
from session_store import SessionStore

app = FastAPI(title="Airflow Test Session Manager")
store = SessionStore()


class StartSessionRequest(BaseModel):
    customer_id: str
    ttl_minutes: int = 60          # Default: 1 hour session


class SessionResponse(BaseModel):
    session_id: str
    airflow_url: str
    status: str
    ttl_minutes: int
    credentials: dict


@app.post("/sessions/start", response_model=SessionResponse)
async def start_session(request: StartSessionRequest):
    """
    Launch a new on-demand EC2 instance with Airflow for the customer.
    Returns the Airflow URL once the instance is ready.
    """
    # Enforce: one active session per customer
    active = store.list_active_sessions(request.customer_id)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Customer already has an active session: {active[0]['session_id']}"
        )

    session_id = str(uuid.uuid4())
    store.create_session(session_id, request.customer_id, request.ttl_minutes)

    # Launch EC2 asynchronously
    try:
        store.update_session(session_id, {"status": "LAUNCHING"})
        
        instance = await asyncio.to_thread(
            launch_airflow_instance,
            session_id,
            request.customer_id,
            request.ttl_minutes,
        )

        # Poll until Airflow webserver is healthy (max 5 min)
        airflow_url = instance["airflow_url"]
        await wait_for_airflow(airflow_url, timeout=300)

        store.update_session(session_id, {
            "status": "READY",
            "instance_id": instance["instance_id"],
            "airflow_url": airflow_url,
        })

        return SessionResponse(
            session_id=session_id,
            airflow_url=airflow_url,
            status="READY",
            ttl_minutes=request.ttl_minutes,
            credentials={"username": "admin", "password": "admin"},
        )

    except Exception as e:
        store.update_session(session_id, {"status": "FAILED"})
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/sessions/{session_id}")
async def end_session(session_id: str):
    """Manually terminate a session before TTL expires."""
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("instance_id"):
        store.update_session(session_id, {"status": "TERMINATING"})
        await asyncio.to_thread(terminate_instance, session["instance_id"])
        store.update_session(session_id, {"status": "TERMINATED"})

    return {"message": "Session terminated", "session_id": session_id}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get current status of a session."""
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def wait_for_airflow(url: str, timeout: int = 300):
    """Poll Airflow health endpoint until it responds or timeout."""
    import httpx
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{url}/health", timeout=5)
                if resp.status_code == 200:
                    return
        except Exception:
            pass
        await asyncio.sleep(10)
    raise TimeoutError(f"Airflow at {url} did not become healthy within {timeout}s")
```

### 8.2 API Flow Sequence

```
Customer          Session Manager API         AWS EC2         DynamoDB
    │                     │                      │                │
    │  POST /sessions/start│                      │                │
    ├────────────────────►│                      │                │
    │                     │  run_instances()      │                │
    │                     ├─────────────────────►│                │
    │                     │  create_session()     │                │
    │                     ├────────────────────────────────────►  │
    │                     │                      │  boot + setup  │
    │                     │◄ poll /health ───────┤                │
    │                     │  (up to 5 min)        │                │
    │                     │  update_session(READY)│                │
    │                     ├────────────────────────────────────►  │
    │  {airflow_url, creds}│                      │                │
    │◄────────────────────┤                      │                │
    │                     │                      │                │
    │  [Customer uses Airflow UI directly]        │                │
    │                     │                      │ watchdog timer  │
    │                     │                      │ (TTL expires)   │
    │                     │                      │ docker down -v  │
    │                     │                      │ shutdown -h now │
    │                     │                      ├──────────────►  │
    │                     │  POST /terminated     │  TERMINATED     │
    │                     │◄─────────────────────┤                │
```

---

## 9. Security

### 9.1 EC2 Security Group Rules

```python
# security_group.py
def create_airflow_security_group(vpc_id: str) -> str:
    """
    Create a security group for Airflow test EC2 instances.
    Only allows inbound on port 8080 from customer IP (pass dynamically).
    """
    ec2 = boto3.client("ec2", region_name="us-east-1")
    
    sg = ec2.create_security_group(
        GroupName="airflow-test-sg",
        Description="Security group for Airflow test deployments",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]

    # Allow Airflow UI access (port 8080) — restrict to customer IP in production
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 8080,
                "ToPort": 8080,
                "IpRanges": [
                    {"CidrIp": "0.0.0.0/0", "Description": "Airflow UI — lock down in prod"}
                ],
            },
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [
                    {"CidrIp": "10.0.0.0/8", "Description": "SSH from VPC only"}
                ],
            },
        ],
    )
    return sg_id
```

### 9.2 IAM Role for EC2 Instance

The EC2 instance needs minimal permissions — only enough to call back to the Session Manager:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:log-group:/airflow-test/*"
    }
  ]
}
```

### 9.3 Security Best Practices

- **Rotate credentials per session** — generate a unique Fernet key and DB password for each Docker Compose session:

```python
# secrets.py
import secrets
import base64
from cryptography.fernet import Fernet

def generate_session_secrets() -> dict:
    return {
        "fernet_key": Fernet.generate_key().decode(),
        "db_password": secrets.token_urlsafe(24),
        "airflow_admin_password": secrets.token_urlsafe(16),
    }
```

- **No persistent public IP** — use the EC2 public DNS which changes every session
- **EBS volume encrypted** — add `"Encrypted": True` to `BlockDeviceMappings`
- **Terminate on shutdown** — `InstanceInitiatedShutdownBehavior=terminate` ensures the instance is fully deleted, not just stopped, preventing accidental data persistence

---

## 10. Cost Optimization

### 10.1 Cost Estimate per Session

| Resource | Hourly Rate | 1-hour Session | Notes |
|---|---|---|---|
| `t3.large` EC2 | ~$0.083/hr | ~$0.083 | On-Demand, us-east-1 |
| 20 GB gp3 EBS | ~$0.002/hr | ~$0.002 | Deleted after session |
| Data transfer | ~$0.001/hr | ~$0.001 | Minimal for Airflow UI |
| **Total** | | **~$0.09/session** | Per 1-hour session |

### 10.2 Cost Controls

```python
# cost_controls.py

# 1. Hard cap on session TTL
MAX_TTL_MINUTES = 120  # Never allow sessions longer than 2 hours

# 2. Idle timeout — terminate if no Airflow activity for 20 min
IDLE_TIMEOUT_MINUTES = 20

# 3. Enforce one session per customer — no parallel sessions
MAX_SESSIONS_PER_CUSTOMER = 1

# 4. Auto-terminate orphaned instances with Lambda
# Schedule this Lambda every 15 minutes to clean up stuck instances
def cleanup_orphaned_instances():
    ec2 = boto3.client("ec2")
    instances = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Purpose", "Values": ["airflow-test-deployment"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    for reservation in instances["Reservations"]:
        for instance in reservation["Instances"]:
            launch_time = instance["LaunchTime"]
            age_hours = (datetime.now(timezone.utc) - launch_time).seconds / 3600
            if age_hours > 3:  # Kill anything running over 3 hours
                instance_id = instance["InstanceId"]
                print(f"Terminating orphaned instance: {instance_id}")
                ec2.terminate_instances(InstanceIds=[instance_id])
```

---

## 11. Deployment Guide

### 11.1 Prerequisites

```bash
# Install dependencies
pip install fastapi uvicorn boto3 httpx pydantic

# Configure AWS credentials
aws configure

# Create DynamoDB table
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
      "KeySchema": [{"AttributeName": "customer_id", "KeyType": "HASH"}],
      "Projection": {"ProjectionType": "ALL"}
    }
  ]'

# Enable DynamoDB TTL
aws dynamodb update-time-to-live \
  --table-name airflow-test-sessions \
  --time-to-live-specification Enabled=true,AttributeName=ttl
```

### 11.2 Running the API Locally

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 11.3 Example API Usage

```bash
# Start a session
curl -X POST http://localhost:8000/sessions/start \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "cust_123", "ttl_minutes": 60}'

# Response:
# {
#   "session_id": "a1b2c3d4-...",
#   "airflow_url": "http://ec2-1-2-3-4.compute-1.amazonaws.com:8080",
#   "status": "READY",
#   "ttl_minutes": 60,
#   "credentials": {"username": "admin", "password": "admin"}
# }

# Check session status
curl http://localhost:8000/sessions/a1b2c3d4-...

# End session early
curl -X DELETE http://localhost:8000/sessions/a1b2c3d4-...
```

### 11.4 Project Structure

```
airflow-test-deployment/
├── main.py                  # FastAPI app + endpoints
├── launch_instance.py       # EC2 launch/terminate logic
├── user_data.py             # EC2 User Data script generator
├── docker_compose_template.py  # Docker Compose YAML generator
├── session_store.py         # DynamoDB session state
├── secrets.py               # Per-session credential generation
├── cost_controls.py         # Orphan cleanup Lambda
├── security_group.py        # Security group provisioning
├── requirements.txt
└── README.md
```

---

## 12. Monitoring & Observability

### 12.1 CloudWatch Metrics to Track

```python
# monitoring.py
import boto3

def publish_session_metric(metric_name: str, value: float, unit: str = "Count"):
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    cw.put_metric_data(
        Namespace="AirflowTestDeployments",
        MetricData=[{
            "MetricName": metric_name,
            "Value": value,
            "Unit": unit,
        }]
    )

# Usage:
publish_session_metric("SessionsStarted", 1)
publish_session_metric("SessionDurationMinutes", 47, "Count")
publish_session_metric("SessionStartupTimeSeconds", 180, "Seconds")
publish_session_metric("OrphanedInstancesTerminated", 1)
```

### 12.2 Key Metrics to Monitor

| Metric | Alert Threshold | Action |
|---|---|---|
| Session startup time | > 5 minutes | Check EC2/Docker pull times |
| Active sessions | > 20 | Review cost spend |
| Orphaned instances | > 0 | Immediate cleanup + investigation |
| Session failures | > 5% | Review CloudWatch logs |

### 12.3 CloudWatch Log Streaming from EC2

Add to User Data script to stream bootstrap logs to CloudWatch:

```bash
# Install CloudWatch agent
apt-get install -y amazon-cloudwatch-agent

# Configure log streaming
cat > /opt/aws/amazon-cloudwatch-agent/etc/config.json << EOF
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/airflow-setup.log",
            "log_group_name": "/airflow-test/setup",
            "log_stream_name": "{session_id}"
          },
          {
            "file_path": "/var/log/airflow-watchdog.log",
            "log_group_name": "/airflow-test/watchdog",
            "log_stream_name": "{session_id}"
          }
        ]
      }
    }
  }
}
EOF
systemctl start amazon-cloudwatch-agent
```

---

## Appendix: Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| EC2 vs ECS Fargate | EC2 | Docker Compose works natively; simpler for single-session use case |
| On-Demand vs Spot | On-Demand | Spot interruptions unacceptable during active customer session |
| LocalExecutor vs CeleryExecutor | LocalExecutor | No Redis/Celery overhead needed for single-user sessions |
| Session state store | DynamoDB | Serverless, TTL built-in, no DB to manage |
| Cleanup mechanism | Watchdog on EC2 + Lambda orphan cleaner | Defense in depth — two independent cleanup paths |
| Instance type | t3.large | 2 vCPU / 8 GB — sufficient for scheduler + webserver + worker |
| EBS Delete on Terminate | true | Prevents data leakage between sessions |
