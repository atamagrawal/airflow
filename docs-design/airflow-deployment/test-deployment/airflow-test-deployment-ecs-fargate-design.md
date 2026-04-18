# Airflow Test Deployment on AWS ECS Fargate — Design Document

**Version:** 1.0  
**Date:** March 2026  
**Status:** Draft

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [Why ECS Fargate Over EC2](#3-why-ecs-fargate-over-ec2)
4. [Architecture](#4-architecture)
5. [Infrastructure Setup](#5-infrastructure-setup)
6. [Container Design](#6-container-design)
7. [ECS Task Definition](#7-ecs-task-definition)
8. [Session Lifecycle Management](#8-session-lifecycle-management)
9. [API Layer](#9-api-layer)
10. [Networking & URL Routing](#10-networking--url-routing)
11. [Security](#11-security)
12. [Cost Optimization](#12-cost-optimization)
13. [Deployment Guide](#13-deployment-guide)
14. [Monitoring & Observability](#14-monitoring--observability)
15. [Appendix: Design Decisions](#15-appendix-design-decisions)

---

## 1. Overview

This document describes the design for an **on-demand, per-session Airflow test deployment system** built on **AWS ECS Fargate**. It is designed to support many customers creating isolated test sessions simultaneously — each customer gets a fresh, fully isolated Airflow environment that automatically tears down when the session ends.

### Key Principle

> One session = One ECS Task Group = One isolated Airflow environment

Each customer session runs as a set of ECS Fargate containers (webserver + scheduler + postgres) in complete isolation. There is no shared infrastructure between sessions.

### How It Differs from the EC2 Approach

| Dimension | EC2 Approach | ECS Fargate Approach |
|---|---|---|
| Startup time | 3–5 minutes | 15–30 seconds |
| Concurrent sessions | ~16 (vCPU limits) | Hundreds |
| Infrastructure management | You manage OS, Docker | AWS manages everything |
| Scaling | Manual EC2 quota increases | Automatic |
| Idle cost | EC2 running even if idle | Pay per second of actual use |
| Operational overhead | High | Low |

---

## 2. Goals & Non-Goals

### Goals
- Support many customers creating test sessions simultaneously
- Launch a fully functional Airflow environment in under 60 seconds
- Provide each customer with an isolated, clean Airflow environment
- Automatically terminate sessions after TTL expiry or explicit end
- Scale horizontally with zero infrastructure changes
- Minimize cost — pay only for active session runtime

### Non-Goals
- Not a production Airflow environment
- No persistent DAG storage between sessions
- No auto-scaling of workers within a single session
- No multi-region failover (single region deployment)

---

## 3. Why ECS Fargate Over EC2

### Problem with EC2 at Scale

When many customers create sessions simultaneously, EC2 has hard limits:

```
EC2 t3.large = 2 vCPU
AWS default vCPU quota = 32 per region
Max concurrent EC2 sessions = 32 / 2 = 16 sessions only
```

Beyond 16 concurrent sessions, new requests are rejected until quota is manually increased.

### ECS Fargate Solves This

- **No vCPU quota per instance type** — Fargate has a separate, much higher Fargate vCPU quota (default 256 vCPUs, easily increased)
- **Serverless** — AWS manages the underlying EC2 fleet, you only define tasks
- **Faster startup** — no OS boot, no Docker install — containers start in 15–30 seconds
- **Per-second billing** — no wasted spend on idle EC2 instances
- **Native ECS task isolation** — each session's containers run in their own network namespace

### Startup Time Comparison

```
EC2 Cold Start:
  ├── EC2 boot                  ~60 sec
  ├── apt-get update/install    ~90 sec
  ├── Docker pull (2 images)    ~60 sec
  └── Airflow init              ~60 sec
  Total:                        ~4–5 min

ECS Fargate Cold Start:
  ├── Task provisioning         ~10 sec
  ├── Image pull (cached ECR)   ~10 sec
  └── Airflow init              ~30 sec
  Total:                        ~50 sec
```

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Many Customers                                 │
│                   (Browser / API Clients)                             │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  POST /sessions/start
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Session Manager API                                │
│              (FastAPI on ECS Fargate — always-on)                    │
│                                                                      │
│  - Validates customer token                                          │
│  - Launches ECS task group per session                               │
│  - Registers session URL in DynamoDB                                 │
│  - Returns Airflow URL + session ID                                  │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  boto3 ECS SDK
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       AWS ECS Fargate Cluster                         │
│                                                                      │
│  ┌─────────────────────┐    ┌─────────────────────┐                 │
│  │  Session: cust_001  │    │  Session: cust_002   │  ...N sessions │
│  │                     │    │                      │                 │
│  │  ┌───────────────┐  │    │  ┌───────────────┐  │                 │
│  │  │   Webserver   │  │    │  │   Webserver   │  │                 │
│  │  │   :8080       │  │    │  │   :8080       │  │                 │
│  │  ├───────────────┤  │    │  ├───────────────┤  │                 │
│  │  │   Scheduler   │  │    │  │   Scheduler   │  │                 │
│  │  ├───────────────┤  │    │  ├───────────────┤  │                 │
│  │  │  PostgreSQL   │  │    │  │  PostgreSQL   │  │                 │
│  │  │  (sidecar)    │  │    │  │  (sidecar)    │  │                 │
│  │  └───────────────┘  │    │  └───────────────┘  │                 │
│  │  VPC: 10.0.x.x      │    │  VPC: 10.0.x.x      │                 │
│  └─────────────────────┘    └─────────────────────┘                 │
│                                                                      │
│  Each task group = isolated ECS task with shared network namespace   │
└──────────┬───────────────────────────┬───────────────────────────────┘
           │                           │
           ▼                           ▼
┌──────────────────┐       ┌──────────────────────────┐
│    DynamoDB       │       │   Application Load       │
│  Session State   │       │   Balancer (ALB)          │
│  TTL tracking    │       │   Path-based routing      │
│                  │       │   /session/{id}/*         │
└──────────────────┘       └──────────────────────────┘
           │                           │
           ▼                           ▼
┌──────────────────┐       ┌──────────────────────────┐
│  EventBridge     │       │   Amazon ECR             │
│  TTL expiry rule │       │   (Airflow Docker image) │
│  → Lambda cleanup│       │   Pre-pulled, cached     │
└──────────────────┘       └──────────────────────────┘
```

---

## 5. Infrastructure Setup

### 5.1 Terraform — Core Infrastructure

```hcl
# infrastructure/main.tf

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── VPC ─────────────────────────────────────────────────────────
resource "aws_vpc" "airflow_test" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "airflow-test-vpc" }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.airflow_test.id
  cidr_block        = "10.0.${count.index}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "airflow-test-public-${count.index}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.airflow_test.id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.airflow_test.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ── ECS Cluster ─────────────────────────────────────────────────
resource "aws_ecs_cluster" "airflow_test" {
  name = "airflow-test-cluster"

  configuration {
    execute_command_configuration {
      logging = "DEFAULT"
    }
  }

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# ── ECR Repository ───────────────────────────────────────────────
resource "aws_ecr_repository" "airflow_test" {
  name                 = "airflow-test"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ── ALB ─────────────────────────────────────────────────────────
resource "aws_lb" "airflow_test" {
  name               = "airflow-test-alb"
  internal           = false
  load_balancer_type = "application"
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.airflow_test.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Session not found"
      status_code  = "404"
    }
  }
}

# ── DynamoDB ─────────────────────────────────────────────────────
resource "aws_dynamodb_table" "sessions" {
  name         = "airflow-test-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  global_secondary_index {
    name            = "customer_id-index"
    hash_key        = "customer_id"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ── IAM Role for ECS Task Execution ─────────────────────────────
resource "aws_iam_role" "ecs_task_execution" {
  name = "airflow-test-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ── IAM Role for ECS Task (runtime permissions) ──────────────────
resource "aws_iam_role" "ecs_task" {
  name = "airflow-test-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task_policy" {
  name = "airflow-test-task-policy"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:log-group:/airflow-test/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:*:*:secret:airflow-test/*"
      }
    ]
  })
}
```

### 5.2 Security Groups

```hcl
# infrastructure/security_groups.tf

# ALB Security Group — accepts public traffic on port 80/443
resource "aws_security_group" "alb" {
  name   = "airflow-test-alb-sg"
  vpc_id = aws_vpc.airflow_test.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ECS Task Security Group — only accepts traffic from ALB
resource "aws_security_group" "ecs_tasks" {
  name   = "airflow-test-ecs-sg"
  vpc_id = aws_vpc.airflow_test.id

  # Allow Airflow webserver traffic only from ALB
  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # Allow inter-container communication within same task
  ingress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    self      = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

---

## 6. Container Design

### 6.1 Custom Airflow Docker Image

Build a custom image pre-baked with all dependencies — avoids pulling at session start time:

```dockerfile
# Dockerfile
FROM apache/airflow:2.9.2

USER root

# Install any additional system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# Pre-install common Python packages your customers may need
RUN pip install --no-cache-dir \
    apache-airflow-providers-amazon \
    apache-airflow-providers-postgres \
    apache-airflow-providers-http \
    apache-airflow-providers-slack \
    pandas \
    requests

# Copy default DAGs for testing (optional)
COPY --chown=airflow:root sample_dags/ /opt/airflow/dags/

# Airflow config optimized for test sessions
ENV AIRFLOW__CORE__EXECUTOR=LocalExecutor \
    AIRFLOW__CORE__LOAD_EXAMPLES=False \
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=True \
    AIRFLOW__SCHEDULER__USE_JOB_SCHEDULE=False \
    AIRFLOW__WEBSERVER__EXPOSE_CONFIG=True \
    AIRFLOW__WEBSERVER__BASE_URL=http://localhost:8080 \
    AIRFLOW__SCHEDULER__MIN_FILE_PROCESS_INTERVAL=10
```

```bash
# Build and push to ECR
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/airflow-test"

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_URI

docker build -t airflow-test:2.9.2 .
docker tag airflow-test:2.9.2 ${ECR_URI}:2.9.2
docker tag airflow-test:2.9.2 ${ECR_URI}:latest
docker push ${ECR_URI}:2.9.2
docker push ${ECR_URI}:latest
```

### 6.2 Container Architecture per Session

Each session runs as a **single ECS Task** with multiple containers sharing the same network namespace:

```
ECS Task (1 per session)
├── postgres        — metadata DB (sidecar, not exposed externally)
├── airflow-init    — runs db migrate + user create, then exits
├── airflow-webserver — serves Airflow UI on :8080
└── airflow-scheduler — runs DAG scheduling
```

> **Why PostgreSQL as sidecar (not RDS)?**
> For short-lived test sessions, a sidecar Postgres container is cheaper and simpler than spinning up an RDS instance per session. The data only needs to live for the session duration. When the task stops, the ephemeral storage is destroyed — guaranteeing clean state.

---

## 7. ECS Task Definition

### 7.1 Task Definition (Python / boto3)

```python
# task_definition.py
import boto3
import json

AWS_ACCOUNT_ID = "123456789012"
AWS_REGION     = "us-east-1"
ECR_IMAGE      = f"{AWS_ACCOUNT_ID}.dkr.ecr.{AWS_REGION}.amazonaws.com/airflow-test:2.9.2"
LOG_GROUP      = "/airflow-test/sessions"

def register_task_definition():
    ecs = boto3.client("ecs", region_name=AWS_REGION)

    response = ecs.register_task_definition(
        family="airflow-test-session",
        networkMode="awsvpc",          # Required for Fargate
        requiresCompatibilities=["FARGATE"],
        cpu="2048",                    # 2 vCPU for the full task
        memory="4096",                 # 4 GB RAM for the full task
        executionRoleArn=f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/airflow-test-ecs-execution-role",
        taskRoleArn=f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/airflow-test-ecs-task-role",

        volumes=[
            {
                "name": "airflow-dags",
                # Ephemeral storage — destroyed when task stops
            }
        ],

        containerDefinitions=[

            # ── 1. PostgreSQL sidecar ────────────────────────────────
            {
                "name": "postgres",
                "image": "postgres:15",
                "essential": True,
                "cpu": 512,
                "memory": 1024,
                "environment": [
                    {"name": "POSTGRES_USER",     "value": "airflow"},
                    {"name": "POSTGRES_PASSWORD", "value": "airflow"},
                    {"name": "POSTGRES_DB",       "value": "airflow"},
                ],
                "healthCheck": {
                    "command": ["CMD-SHELL", "pg_isready -U airflow"],
                    "interval": 10,
                    "timeout": 5,
                    "retries": 5,
                    "startPeriod": 10,
                },
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group":         LOG_GROUP,
                        "awslogs-region":        AWS_REGION,
                        "awslogs-stream-prefix": "postgres",
                    },
                },
            },

            # ── 2. Airflow Init (runs once, then exits) ──────────────
            {
                "name": "airflow-init",
                "image": ECR_IMAGE,
                "essential": False,   # Non-essential — exits after init
                "cpu": 256,
                "memory": 512,
                "command": [
                    "bash", "-c",
                    "airflow db migrate && "
                    "airflow users create "
                    "  --username admin "
                    "  --password admin "
                    "  --firstname Test "
                    "  --lastname User "
                    "  --role Admin "
                    "  --email admin@test.com"
                ],
                "environment": [
                    {
                        "name": "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN",
                        "value": "postgresql+psycopg2://airflow:airflow@localhost/airflow"
                    },
                ],
                "dependsOn": [
                    {"containerName": "postgres", "condition": "HEALTHY"}
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group":         LOG_GROUP,
                        "awslogs-region":        AWS_REGION,
                        "awslogs-stream-prefix": "airflow-init",
                    },
                },
            },

            # ── 3. Airflow Webserver ─────────────────────────────────
            {
                "name": "airflow-webserver",
                "image": ECR_IMAGE,
                "essential": True,
                "cpu": 512,
                "memory": 1024,
                "command": ["airflow", "webserver"],
                "portMappings": [
                    {"containerPort": 8080, "protocol": "tcp"}
                ],
                "environment": [
                    {
                        "name": "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN",
                        "value": "postgresql+psycopg2://airflow:airflow@localhost/airflow"
                    },
                ],
                "healthCheck": {
                    "command": ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"],
                    "interval": 15,
                    "timeout": 10,
                    "retries": 5,
                    "startPeriod": 30,
                },
                "dependsOn": [
                    {"containerName": "airflow-init", "condition": "COMPLETE"}
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group":         LOG_GROUP,
                        "awslogs-region":        AWS_REGION,
                        "awslogs-stream-prefix": "airflow-webserver",
                    },
                },
            },

            # ── 4. Airflow Scheduler ─────────────────────────────────
            {
                "name": "airflow-scheduler",
                "image": ECR_IMAGE,
                "essential": True,
                "cpu": 512,
                "memory": 1024,
                "command": ["airflow", "scheduler"],
                "environment": [
                    {
                        "name": "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN",
                        "value": "postgresql+psycopg2://airflow:airflow@localhost/airflow"
                    },
                ],
                "dependsOn": [
                    {"containerName": "airflow-init", "condition": "COMPLETE"}
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group":         LOG_GROUP,
                        "awslogs-region":        AWS_REGION,
                        "awslogs-stream-prefix": "airflow-scheduler",
                    },
                },
            },
        ],
    )

    return response["taskDefinition"]["taskDefinitionArn"]
```

---

## 8. Session Lifecycle Management

### 8.1 Session States

```
REQUESTED → LAUNCHING → READY → ACTIVE → TERMINATING → TERMINATED
                                    ↑
                              (TTL reset on customer activity)
```

| State | Description | Duration |
|---|---|---|
| `REQUESTED` | Customer called API, ECS task launch initiated | Seconds |
| `LAUNCHING` | ECS task starting, containers initializing | 15–60 sec |
| `READY` | Airflow UI accessible, URL returned to customer | — |
| `ACTIVE` | Customer is using the environment | Up to TTL |
| `TERMINATING` | TTL expired or customer ended session | Seconds |
| `TERMINATED` | ECS task stopped, all resources freed | — |

### 8.2 DynamoDB Session Store

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

    def create_session(
        self,
        session_id: str,
        customer_id: str,
        ttl_minutes: int
    ) -> dict:
        now = datetime.now(timezone.utc)
        ttl_epoch = int(now.timestamp()) + (ttl_minutes * 60)

        item = {
            "session_id":   session_id,
            "customer_id":  customer_id,
            "status":       "REQUESTED",
            "created_at":   now.isoformat(),
            "ttl":          ttl_epoch,
            "ttl_minutes":  ttl_minutes,
            "task_arn":     None,
            "airflow_url":  None,
            "target_group_arn": None,
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
                ":s1": "LAUNCHING",
                ":s2": "READY",
                ":s3": "ACTIVE",
            },
        )
        return response.get("Items", [])
```

### 8.3 TTL Expiry via EventBridge + Lambda

Unlike EC2 where the watchdog runs on the instance itself, ECS sessions are terminated by an external Lambda triggered by EventBridge:

```python
# lambda/ttl_cleanup/handler.py
import boto3
import json
from datetime import datetime, timezone

ecs      = boto3.client("ecs",      region_name="us-east-1")
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
elbv2    = boto3.client("elbv2",    region_name="us-east-1")

CLUSTER   = "airflow-test-cluster"
TABLE     = dynamodb.Table("airflow-test-sessions")

def handler(event, context):
    """
    Runs every 5 minutes via EventBridge.
    Finds sessions past their TTL and terminates them.
    """
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    # Scan for expired sessions still in active states
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
        task_arn   = session.get("task_arn")

        print(f"Terminating expired session: {session_id}")

        # Stop the ECS task
        if task_arn:
            try:
                ecs.stop_task(
                    cluster=CLUSTER,
                    task=task_arn,
                    reason=f"Session TTL expired for session {session_id}",
                )
            except Exception as e:
                print(f"Error stopping task {task_arn}: {e}")

        # Deregister ALB target group
        target_group_arn = session.get("target_group_arn")
        if target_group_arn:
            try:
                elbv2.delete_target_group(TargetGroupArn=target_group_arn)
            except Exception as e:
                print(f"Error deleting target group: {e}")

        # Update session state
        TABLE.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET #status = :s",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":s": "TERMINATED"},
        )

        terminated.append(session_id)

    print(f"Terminated {len(terminated)} sessions: {terminated}")
    return {"terminated": terminated}
```

```python
# EventBridge rule (Terraform)
resource "aws_cloudwatch_event_rule" "ttl_cleanup" {
  name                = "airflow-test-ttl-cleanup"
  description         = "Run TTL cleanup every 5 minutes"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "ttl_cleanup_lambda" {
  rule      = aws_cloudwatch_event_rule.ttl_cleanup.name
  target_id = "TtlCleanupLambda"
  arn       = aws_lambda_function.ttl_cleanup.arn
}
```

---

## 9. API Layer

### 9.1 ECS Task Launcher

```python
# ecs_launcher.py
import boto3
import uuid
from typing import Optional

AWS_ACCOUNT_ID  = "123456789012"
AWS_REGION      = "us-east-1"
CLUSTER         = "airflow-test-cluster"
TASK_DEFINITION = "airflow-test-session"
SUBNET_IDS      = ["subnet-abc123", "subnet-def456"]
SECURITY_GROUP  = "sg-airflow-test-ecs"
ALB_LISTENER_ARN = "arn:aws:elasticloadbalancing:..."

ecs   = boto3.client("ecs",   region_name=AWS_REGION)
elbv2 = boto3.client("elbv2", region_name=AWS_REGION)
ec2   = boto3.client("ec2",   region_name=AWS_REGION)

def launch_session_task(session_id: str, customer_id: str, ttl_minutes: int) -> dict:
    """
    Launch an ECS Fargate task for a new Airflow test session.
    Returns task ARN and Airflow URL.
    """

    # Step 1: Run ECS Fargate task
    response = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=TASK_DEFINITION,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets":       SUBNET_IDS,
                "securityGroups": [SECURITY_GROUP],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": "airflow-webserver",
                    "environment": [
                        {"name": "SESSION_ID",   "value": session_id},
                        {"name": "CUSTOMER_ID",  "value": customer_id},
                        {"name": "TTL_MINUTES",  "value": str(ttl_minutes)},
                    ],
                }
            ]
        },
        tags=[
            {"key": "SessionId",  "value": session_id},
            {"key": "CustomerId", "value": customer_id},
            {"key": "Purpose",    "value": "airflow-test"},
        ],
    )

    task_arn = response["tasks"][0]["taskArn"]

    # Step 2: Wait for task to get a private IP
    waiter = ecs.get_waiter("tasks_running")
    waiter.wait(cluster=CLUSTER, tasks=[task_arn])

    # Step 3: Get task's private IP for ALB registration
    task_detail = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
    private_ip  = task_detail["tasks"][0]["attachments"][0]["details"]
    private_ip  = next(d["value"] for d in private_ip if d["name"] == "privateIPv4Address")

    # Step 4: Create ALB target group for this session
    target_group = elbv2.create_target_group(
        Name=f"af-{session_id[:16]}",   # Max 32 chars
        Protocol="HTTP",
        Port=8080,
        VpcId=get_vpc_id(),
        TargetType="ip",
        HealthCheckPath="/health",
        HealthCheckIntervalSeconds=15,
    )
    tg_arn = target_group["TargetGroups"][0]["TargetGroupArn"]

    # Step 5: Register task IP with target group
    elbv2.register_targets(
        TargetGroupArn=tg_arn,
        Targets=[{"Id": private_ip, "Port": 8080}],
    )

    # Step 6: Add ALB listener rule for path-based routing
    elbv2.create_rule(
        ListenerArn=ALB_LISTENER_ARN,
        Conditions=[
            {
                "Field": "path-pattern",
                "Values": [f"/session/{session_id}/*"],
            }
        ],
        Priority=get_next_alb_priority(),
        Actions=[
            {
                "Type": "forward",
                "TargetGroupArn": tg_arn,
            }
        ],
    )

    alb_dns = get_alb_dns()
    airflow_url = f"http://{alb_dns}/session/{session_id}"

    return {
        "task_arn":         task_arn,
        "airflow_url":      airflow_url,
        "target_group_arn": tg_arn,
        "private_ip":       private_ip,
    }


def stop_session_task(task_arn: str, target_group_arn: Optional[str], session_id: str):
    """Stop ECS task and clean up ALB resources."""
    # Stop task
    ecs.stop_task(
        cluster=CLUSTER,
        task=task_arn,
        reason=f"Session {session_id} ended",
    )

    # Remove ALB listener rule
    rules = elbv2.describe_rules(ListenerArn=ALB_LISTENER_ARN)["Rules"]
    for rule in rules:
        for condition in rule.get("Conditions", []):
            if session_id in str(condition.get("Values", [])):
                elbv2.delete_rule(RuleArn=rule["RuleArn"])
                break

    # Delete target group
    if target_group_arn:
        elbv2.delete_target_group(TargetGroupArn=target_group_arn)


def get_vpc_id() -> str:
    response = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["airflow-test-vpc"]}])
    return response["Vpcs"][0]["VpcId"]


def get_alb_dns() -> str:
    response = elbv2.describe_load_balancers(Names=["airflow-test-alb"])
    return response["LoadBalancers"][0]["DNSName"]


def get_next_alb_priority() -> int:
    rules    = elbv2.describe_rules(ListenerArn=ALB_LISTENER_ARN)["Rules"]
    priorities = [int(r["Priority"]) for r in rules if r["Priority"] != "default"]
    return max(priorities, default=0) + 1
```

### 9.2 FastAPI Session Manager

```python
# main.py
import uuid
import asyncio
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from ecs_launcher import launch_session_task, stop_session_task
from session_store import SessionStore

app   = FastAPI(title="Airflow Test Session Manager — ECS Fargate")
store = SessionStore()

MAX_TTL_MINUTES         = 120
DEFAULT_TTL_MINUTES     = 60
MAX_SESSIONS_PER_CUSTOMER = 1


class StartSessionRequest(BaseModel):
    customer_id: str
    ttl_minutes: int = DEFAULT_TTL_MINUTES


class SessionResponse(BaseModel):
    session_id:       str
    airflow_url:      str
    status:           str
    ttl_minutes:      int
    credentials:      dict
    estimated_ready:  str


@app.post("/sessions/start", response_model=SessionResponse)
async def start_session(request: StartSessionRequest):
    """
    Launch a new ECS Fargate task with Airflow for the customer session.
    Returns the Airflow URL once the task is running.
    """
    # Enforce TTL cap
    ttl = min(request.ttl_minutes, MAX_TTL_MINUTES)

    # Enforce one active session per customer
    active = store.list_active_sessions(request.customer_id)
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "error":      "active_session_exists",
                "message":    "Customer already has an active session",
                "session_id": active[0]["session_id"],
                "airflow_url": active[0].get("airflow_url"),
            }
        )

    session_id = str(uuid.uuid4())
    store.create_session(session_id, request.customer_id, ttl)

    try:
        store.update_session(session_id, {"status": "LAUNCHING"})

        # Launch ECS Fargate task
        task = await asyncio.to_thread(
            launch_session_task,
            session_id,
            request.customer_id,
            ttl,
        )

        # Poll until Airflow webserver is healthy
        await wait_for_airflow(task["airflow_url"], timeout=120)

        store.update_session(session_id, {
            "status":           "READY",
            "task_arn":         task["task_arn"],
            "airflow_url":      task["airflow_url"],
            "target_group_arn": task["target_group_arn"],
        })

        return SessionResponse(
            session_id      = session_id,
            airflow_url     = task["airflow_url"],
            status          = "READY",
            ttl_minutes     = ttl,
            credentials     = {"username": "admin", "password": "admin"},
            estimated_ready = "Session is ready now",
        )

    except Exception as e:
        store.update_session(session_id, {"status": "FAILED"})
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get current status of a session."""
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.delete("/sessions/{session_id}")
async def end_session(session_id: str):
    """Manually terminate a session before its TTL expires."""
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") in ("TERMINATED", "TERMINATING"):
        return {"message": "Session already terminated", "session_id": session_id}

    store.update_session(session_id, {"status": "TERMINATING"})

    await asyncio.to_thread(
        stop_session_task,
        session["task_arn"],
        session.get("target_group_arn"),
        session_id,
    )

    store.update_session(session_id, {"status": "TERMINATED"})
    return {"message": "Session terminated successfully", "session_id": session_id}


@app.get("/sessions/{session_id}/status")
async def poll_session_status(session_id: str):
    """
    Lightweight polling endpoint for customers to check
    when their session transitions from LAUNCHING → READY.
    """
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id":  session_id,
        "status":      session["status"],
        "airflow_url": session.get("airflow_url"),
    }


async def wait_for_airflow(url: str, timeout: int = 120):
    """Poll Airflow /health endpoint until healthy or timeout."""
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
        await asyncio.sleep(5)
    raise TimeoutError(f"Airflow at {url} did not become healthy within {timeout}s")
```

### 9.3 Session API Flow

```
Customer          Session Manager          ECS Fargate          ALB             DynamoDB
    │                    │                     │                  │                  │
    │  POST /sessions/   │                     │                  │                  │
    │  start             │                     │                  │                  │
    ├───────────────────►│                     │                  │                  │
    │                    │  create_session()   │                  │                  │
    │                    ├──────────────────────────────────────────────────────────►│
    │                    │  run_task()         │                  │                  │
    │                    ├────────────────────►│                  │                  │
    │                    │  waiter: running    │                  │                  │
    │                    │◄────────────────────┤                  │                  │
    │                    │  create_target_group│                  │                  │
    │                    ├──────────────────────────────────────► │                  │
    │                    │  create_rule (path) │                  │                  │
    │                    ├──────────────────────────────────────► │                  │
    │                    │  poll /health (~30s)│                  │                  │
    │                    ├────────────────────────────────────────┤                  │
    │                    │  update: READY      │                  │                  │
    │                    ├──────────────────────────────────────────────────────────►│
    │  {airflow_url,     │                     │                  │                  │
    │   credentials}     │                     │                  │                  │
    │◄───────────────────┤                     │                  │                  │
    │                    │                     │                  │                  │
    │  [Customer uses Airflow UI via ALB URL]  │                  │                  │
    │────────────────────────────────────────────────────────────►│                  │
    │                    │                     │◄─────────────────┤                  │
    │                    │                     │                  │                  │
    │  [TTL expires — EventBridge triggers Lambda every 5 min]    │                  │
    │                    │  stop_task()        │                  │                  │
    │                    ├────────────────────►│                  │                  │
    │                    │  delete_rule()      │                  │                  │
    │                    ├──────────────────────────────────────► │                  │
    │                    │  update: TERMINATED │                  │                  │
    │                    ├──────────────────────────────────────────────────────────►│
```

---

## 10. Networking & URL Routing

### 10.1 Path-Based Routing via ALB

Each session gets a unique path prefix on a shared ALB — customers access their Airflow at:

```
https://test.yourdomain.com/session/{session_id}/
```

The ALB forwards requests to the correct ECS task's private IP based on the path:

```
ALB Listener Rules (evaluated in priority order):

Priority 1:  path /session/abc-123/* → Target Group abc-123 → Task IP 10.0.1.5:8080
Priority 2:  path /session/def-456/* → Target Group def-456 → Task IP 10.0.1.8:8080
Priority 3:  path /session/ghi-789/* → Target Group ghi-789 → Task IP 10.0.2.3:8080
...
Default:     fixed-response 404 "Session not found"
```

### 10.2 Airflow Base URL Configuration

Airflow must know its own base URL for redirects to work correctly behind the ALB:

```python
# Add to container environment in task definition
{
    "name": "AIRFLOW__WEBSERVER__BASE_URL",
    "value": f"https://test.yourdomain.com/session/{session_id}"
},
{
    "name": "AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX",
    "value": "True"
},
```

### 10.3 Route53 DNS (Optional)

For a clean URL, create a CNAME pointing to the ALB:

```hcl
resource "aws_route53_record" "airflow_test" {
  zone_id = var.hosted_zone_id
  name    = "test.yourdomain.com"
  type    = "CNAME"
  ttl     = 300
  records = [aws_lb.airflow_test.dns_name]
}
```

---

## 11. Security

### 11.1 Per-Session Credential Rotation

Never reuse credentials across sessions — generate unique passwords per session:

```python
# secrets.py
import secrets
from cryptography.fernet import Fernet

def generate_session_secrets() -> dict:
    return {
        "fernet_key":           Fernet.generate_key().decode(),
        "db_password":          secrets.token_urlsafe(24),
        "webserver_secret_key": secrets.token_urlsafe(32),
        "admin_password":       secrets.token_urlsafe(16),
    }

def store_session_secrets(session_id: str, secrets_dict: dict):
    """Store per-session secrets in AWS Secrets Manager."""
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    import json
    sm.create_secret(
        Name=f"airflow-test/{session_id}",
        SecretString=json.dumps(secrets_dict),
        Tags=[{"Key": "SessionId", "Value": session_id}],
    )

def delete_session_secrets(session_id: str):
    """Delete secrets when session ends."""
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    sm.delete_secret(
        SecretId=f"airflow-test/{session_id}",
        ForceDeleteWithoutRecovery=True,
    )
```

### 11.2 Network Isolation

- ECS tasks are only reachable via the ALB — no direct public IP exposure
- Security group on ECS tasks only allows inbound from ALB security group on port 8080
- Postgres is not exposed at all — only accessible within the task's shared network namespace
- All inter-container communication is over `localhost` (awsvpc mode, shared namespace)

### 11.3 Session Token Validation

```python
# auth.py
import boto3
import hashlib
import hmac

SECRET = "your-session-manager-secret"

def generate_customer_token(customer_id: str) -> str:
    """Generate a signed token for customer authentication."""
    return hmac.new(
        SECRET.encode(),
        customer_id.encode(),
        hashlib.sha256
    ).hexdigest()

def verify_customer_token(customer_id: str, token: str) -> bool:
    """Verify the customer token is valid."""
    expected = generate_customer_token(customer_id)
    return hmac.compare_digest(expected, token)

# FastAPI dependency
from fastapi import Header, HTTPException

async def verify_auth(
    x_customer_id: str    = Header(...),
    x_customer_token: str = Header(...)
):
    if not verify_customer_token(x_customer_id, x_customer_token):
        raise HTTPException(status_code=401, detail="Invalid customer token")
    return x_customer_id
```

---

## 12. Cost Optimization

### 12.1 Cost Per Session

| Resource | Rate | 1-hour Session | Notes |
|---|---|---|---|
| ECS Fargate vCPU | $0.04048/vCPU-hr | $0.081 | 2 vCPU per task |
| ECS Fargate Memory | $0.004445/GB-hr | $0.018 | 4 GB per task |
| ALB | $0.008/LCU-hr | ~$0.010 | Shared across sessions |
| Data transfer | $0.09/GB | ~$0.005 | Minimal for Airflow UI |
| **Total** | | **~$0.11/session** | Per 1-hour session |

> Fargate is ~20% more expensive per session than EC2 t3.large but scales to hundreds of concurrent sessions without quota issues or operational overhead.

### 12.2 Cost Controls

```python
# cost_controls.py

# 1. Hard TTL cap — never allow sessions longer than 2 hours
MAX_TTL_MINUTES = 120

# 2. One session per customer at a time
MAX_SESSIONS_PER_CUSTOMER = 1

# 3. Total concurrent session cap (protect against runaway costs)
MAX_CONCURRENT_SESSIONS = 100

def enforce_concurrent_session_cap():
    ecs = boto3.client("ecs", region_name="us-east-1")
    response = ecs.list_tasks(
        cluster="airflow-test-cluster",
        family="airflow-test-session",
        desiredStatus="RUNNING",
    )
    running_count = len(response["taskArns"])
    if running_count >= MAX_CONCURRENT_SESSIONS:
        raise Exception(
            f"Concurrent session limit reached ({MAX_CONCURRENT_SESSIONS}). "
            "Please try again later."
        )

# 4. Scheduled Lambda to terminate orphaned tasks (missed TTL cleanup)
def cleanup_orphaned_tasks():
    """Kill tasks running over 3 hours regardless of session state."""
    ecs    = boto3.client("ecs",    region_name="us-east-1")
    from datetime import datetime, timezone, timedelta

    tasks_response = ecs.list_tasks(
        cluster="airflow-test-cluster",
        family="airflow-test-session",
        desiredStatus="RUNNING",
    )
    if not tasks_response["taskArns"]:
        return

    tasks = ecs.describe_tasks(
        cluster="airflow-test-cluster",
        tasks=tasks_response["taskArns"]
    )["tasks"]

    now = datetime.now(timezone.utc)
    for task in tasks:
        age = now - task["startedAt"].replace(tzinfo=timezone.utc)
        if age > timedelta(hours=3):
            print(f"Orphaned task: {task['taskArn']} — age: {age}")
            ecs.stop_task(
                cluster="airflow-test-cluster",
                task=task["taskArn"],
                reason="Orphaned task — exceeded max age of 3 hours",
            )
```

---

## 13. Deployment Guide

### 13.1 Prerequisites

```bash
# Install tools
pip install boto3 fastapi uvicorn httpx pydantic
npm install -g aws-cdk   # Optional, for CDK deployment
terraform init           # If using Terraform

# Configure AWS credentials
aws configure

# Verify ECS Fargate quota
aws service-quotas get-service-quota \
  --service-code fargate \
  --quota-code L-790AF391   # Fargate On-Demand vCPU quota
```

### 13.2 Infrastructure Deployment

```bash
# Step 1: Deploy infrastructure via Terraform
cd infrastructure/
terraform init
terraform plan
terraform apply

# Step 2: Build and push Airflow Docker image
./scripts/build_and_push.sh

# Step 3: Register ECS task definition
python task_definition.py

# Step 4: Deploy Session Manager API
docker build -t session-manager .
# Push to ECR and update ECS service for the API itself
```

### 13.3 Example API Usage

```bash
# Start a session
curl -X POST https://api.yourdomain.com/sessions/start \
  -H "Content-Type: application/json" \
  -H "X-Customer-Id: cust_123" \
  -H "X-Customer-Token: <token>" \
  -d '{"customer_id": "cust_123", "ttl_minutes": 60}'

# Response:
# {
#   "session_id": "a1b2c3d4-e5f6-...",
#   "airflow_url": "https://test.yourdomain.com/session/a1b2c3d4-e5f6-...",
#   "status": "READY",
#   "ttl_minutes": 60,
#   "credentials": {"username": "admin", "password": "xK9mP2qR..."},
#   "estimated_ready": "Session is ready now"
# }

# Poll session status (during LAUNCHING phase)
curl https://api.yourdomain.com/sessions/a1b2c3d4-e5f6-.../status

# End session early
curl -X DELETE https://api.yourdomain.com/sessions/a1b2c3d4-e5f6-...
```

### 13.4 Project Structure

```
airflow-ecs-fargate/
├── api/
│   ├── main.py                  # FastAPI app + endpoints
│   ├── ecs_launcher.py          # ECS task launch/stop logic
│   ├── session_store.py         # DynamoDB session state
│   ├── secrets.py               # Per-session credential generation
│   ├── auth.py                  # Customer token validation
│   ├── cost_controls.py         # Concurrent session cap + orphan cleanup
│   └── requirements.txt
├── lambda/
│   └── ttl_cleanup/
│       └── handler.py           # EventBridge-triggered TTL cleanup
├── docker/
│   ├── Dockerfile               # Custom Airflow image
│   └── sample_dags/             # Pre-loaded sample DAGs
├── infrastructure/
│   ├── main.tf                  # VPC, ECS cluster, DynamoDB, ALB
│   ├── security_groups.tf       # ALB + ECS security groups
│   ├── iam.tf                   # Task execution + task roles
│   └── variables.tf
└── scripts/
    ├── build_and_push.sh        # ECR image build/push
    └── register_task_def.py     # ECS task definition registration
```

---

## 14. Monitoring & Observability

### 14.1 CloudWatch Dashboard Metrics

```python
# monitoring.py
import boto3

cw = boto3.client("cloudwatch", region_name="us-east-1")

def publish_metric(metric_name: str, value: float, unit: str = "Count", dimensions: list = None):
    cw.put_metric_data(
        Namespace="AirflowTestSessions",
        MetricData=[{
            "MetricName": metric_name,
            "Value":      value,
            "Unit":       unit,
            "Dimensions": dimensions or [],
        }]
    )

# Track these from the Session Manager API:
publish_metric("SessionsStarted",          1)
publish_metric("SessionStartupTimeSeconds", 45,  "Seconds")
publish_metric("ActiveSessions",            12,  "Count")
publish_metric("SessionDurationMinutes",    47,  "Count")
publish_metric("SessionsTerminatedByTTL",  1)
publish_metric("SessionsFailed",           0)
```

### 14.2 Key Metrics & Alerts

| Metric | Alert Threshold | Action |
|---|---|---|
| `SessionStartupTimeSeconds` | > 90 sec | Check ECR pull time / task placement |
| `ActiveSessions` | > 80 | Scale warning — approaching ALB rule limits |
| `SessionsFailed` | > 3 in 5 min | Page on-call — investigate ECS cluster health |
| ECS Task CPU | > 90% | Increase task CPU allocation |
| ALB 5xx errors | > 1% | Investigate unhealthy targets |
| `OrphanedTasksTerminated` | > 0 | Investigate TTL cleanup misses |

### 14.3 Structured Logging

```python
# logging_config.py
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "level":      record.levelname,
            "message":    record.getMessage(),
            "session_id": getattr(record, "session_id", None),
            "customer_id": getattr(record, "customer_id", None),
            "timestamp":  self.formatTime(record),
        })

# Usage:
logger = logging.getLogger("session-manager")
logger.info(
    "Session started",
    extra={"session_id": session_id, "customer_id": customer_id}
)
```

---

## 15. Appendix: Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| ECS Fargate vs EC2 | ECS Fargate | Faster startup, no instance management, scales to hundreds of concurrent sessions |
| Fargate vs EKS | ECS Fargate | No Kubernetes expertise required; EKS adds $150–300/month cluster baseline cost |
| PostgreSQL sidecar vs RDS | Sidecar container | Cheaper, simpler for ephemeral sessions; data doesn't outlive the task |
| LocalExecutor vs CeleryExecutor | LocalExecutor | No Redis/Celery overhead; single-user sessions don't need distributed workers |
| ALB path routing vs unique DNS per session | ALB path routing | Avoids DNS propagation delay; instant routing via ALB rule |
| TTL cleanup via Lambda vs in-task | EventBridge + Lambda | External cleanup is more reliable; in-task cleanup can't run if the task crashes |
| Session state in DynamoDB vs RDS | DynamoDB | Serverless, no cluster to manage, built-in TTL support |
| EBS ephemeral storage | Fargate ephemeral storage | Destroyed with the task — guarantees clean state per session |
| Image pre-baking in ECR | ECR with pre-installed providers | Reduces cold start time from >60 sec to <15 sec for image pull |
| Scheduler `USE_JOB_SCHEDULE=false` | Disabled | Prevents automatic DAG scheduling — test sessions should only run DAGs manually |
