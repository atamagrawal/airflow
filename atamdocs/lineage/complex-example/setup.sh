#!/bin/bash

# PostgreSQL Column Lineage Setup Script
# This script automates the setup of PostgreSQL and Marquez for column lineage tracking

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=====================================${NC}"
echo -e "${GREEN}PostgreSQL Column Lineage Setup${NC}"
echo -e "${GREEN}=====================================${NC}"
echo ""

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to wait for service to be ready
wait_for_service() {
    local url=$1
    local service_name=$2
    local max_attempts=30
    local attempt=0

    echo -e "${YELLOW}Waiting for ${service_name} to be ready...${NC}"

    while [ $attempt -lt $max_attempts ]; do
        if curl -s -f "$url" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ ${service_name} is ready!${NC}"
            return 0
        fi
        attempt=$((attempt + 1))
        echo -n "."
        sleep 2
    done

    echo -e "${RED}✗ ${service_name} failed to start${NC}"
    return 1
}

# Step 1: Check prerequisites
echo -e "${YELLOW}Step 1: Checking prerequisites...${NC}"

if ! command_exists docker; then
    echo -e "${RED}✗ Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker found${NC}"

if ! command_exists docker-compose && ! docker compose version >/dev/null 2>&1; then
    echo -e "${RED}✗ Docker Compose is not installed. Please install Docker Compose first.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker Compose found${NC}"

if ! command_exists airflow; then
    echo -e "${YELLOW}⚠ Airflow CLI not found. Make sure Airflow is installed and in your PATH.${NC}"
else
    echo -e "${GREEN}✓ Airflow CLI found${NC}"
fi

echo ""

# Step 2: Start Docker services
echo -e "${YELLOW}Step 2: Starting Docker services (PostgreSQL + Marquez)...${NC}"

if [ -f "docker-compose.yml" ]; then
    # Try docker-compose first, then fallback to docker compose
    if command_exists docker-compose; then
        docker-compose up -d
    else
        docker compose up -d
    fi
    echo -e "${GREEN}✓ Docker services started${NC}"
else
    echo -e "${RED}✗ docker-compose.yml not found${NC}"
    exit 1
fi

echo ""

# Step 3: Wait for services to be ready
echo -e "${YELLOW}Step 3: Waiting for services to be ready...${NC}"

# Wait for PostgreSQL
echo -e "${YELLOW}Checking PostgreSQL...${NC}"
sleep 5  # Give PostgreSQL time to start
for i in {1..30}; do
    if docker exec postgres-lineage pg_isready -U airflow > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PostgreSQL is ready${NC}"
        break
    fi
    echo -n "."
    sleep 2
done

# Wait for Marquez API
wait_for_service "http://localhost:5000/api/v1/namespaces" "Marquez API"

# Wait for Marquez Web
wait_for_service "http://localhost:3000" "Marquez Web UI"

echo ""

# Step 4: Install Python packages
echo -e "${YELLOW}Step 4: Installing required Python packages...${NC}"

if command_exists pip; then
    echo "Installing packages..."
    pip install -q apache-airflow-providers-openlineage \
                   apache-airflow-providers-postgres \
                   apache-airflow-providers-common-sql
    echo -e "${GREEN}✓ Python packages installed${NC}"
else
    echo -e "${YELLOW}⚠ pip not found. Please install packages manually:${NC}"
    echo "  pip install apache-airflow-providers-openlineage"
    echo "  pip install apache-airflow-providers-postgres"
    echo "  pip install apache-airflow-providers-common-sql"
fi

echo ""

# Step 5: Configure Airflow connection
echo -e "${YELLOW}Step 5: Configuring Airflow connection...${NC}"

if command_exists airflow; then
    if airflow connections get postgres_default >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠ Connection 'postgres_default' already exists${NC}"
        read -p "Do you want to update it? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            airflow connections delete postgres_default
            airflow connections add 'postgres_default' \
                --conn-type 'postgres' \
                --conn-host 'localhost' \
                --conn-schema 'airflow_db' \
                --conn-login 'airflow' \
                --conn-password 'airflow' \
                --conn-port 5432
            echo -e "${GREEN}✓ Connection updated${NC}"
        fi
    else
        airflow connections add 'postgres_default' \
            --conn-type 'postgres' \
            --conn-host 'localhost' \
            --conn-schema 'airflow_db' \
            --conn-login 'airflow' \
            --conn-password 'airflow' \
            --conn-port 5432
        echo -e "${GREEN}✓ Connection created${NC}"
    fi

    # Test connection
    echo -e "${YELLOW}Testing connection...${NC}"
    if airflow connections test postgres_default; then
        echo -e "${GREEN}✓ Connection test successful${NC}"
    else
        echo -e "${RED}✗ Connection test failed${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Please add connection manually (see README.md)${NC}"
fi

echo ""

# Step 6: Configure OpenLineage
echo -e "${YELLOW}Step 6: Configuring OpenLineage...${NC}"

echo -e "${YELLOW}Add to your airflow.cfg:${NC}"
echo ""
echo "[openlineage]"
echo "namespace = my_airflow_instance"
echo "transport = {\"type\": \"http\", \"url\": \"http://localhost:5000\"}"
echo ""

# Step 7: Copy DAG file
echo -e "${YELLOW}Step 7: Setting up DAG file...${NC}"

if command_exists airflow; then
    DAGS_FOLDER=$(airflow config get-value core dags_folder 2>/dev/null || echo "$HOME/airflow/dags")

    if [ -d "$DAGS_FOLDER" ]; then
        if [ -f "postgres_column_lineage_dag.py" ]; then
            cp postgres_column_lineage_dag.py "$DAGS_FOLDER/"
            echo -e "${GREEN}✓ DAG file copied to $DAGS_FOLDER${NC}"
        else
            echo -e "${YELLOW}⚠ Please copy postgres_column_lineage_dag.py to $DAGS_FOLDER manually${NC}"
        fi
    else
        echo -e "${YELLOW}⚠ Please copy postgres_column_lineage_dag.py to your DAGs folder${NC}"
    fi
fi

echo ""

# Summary
echo -e "${GREEN}=====================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "${GREEN}=====================================${NC}"
echo ""
echo -e "${GREEN}✓ PostgreSQL: localhost:5432${NC}"
echo -e "${GREEN}✓ Marquez API: http://localhost:5000${NC}"
echo -e "${GREEN}✓ Marquez Web UI: http://localhost:3000${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Configure OpenLineage (see above)"
echo "2. Restart Airflow"
echo "3. Trigger the DAG: airflow dags trigger postgres_column_lineage_example"
echo "4. View lineage at http://localhost:3000"
echo ""
