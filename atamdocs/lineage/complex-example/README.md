# Example 1: PostgreSQL Column Lineage

This example demonstrates automatic column-level lineage tracking for PostgreSQL operations in Apache Airflow using OpenLineage.

## Overview

This example shows how to:
- Set up PostgreSQL with Docker
- Configure OpenLineage with Marquez backend
- Create a DAG that automatically tracks column lineage for:
  - Table creation
  - Data insertion
  - SELECT queries with JOINs
  - Aggregations (COUNT, SUM, AVG, MIN, MAX)
  - Complex transformations (CONCAT, CASE statements)
  - INSERT INTO ... SELECT operations

## What You'll Learn

1. **Automatic Lineage**: How Airflow automatically parses SQL and generates column lineage
2. **Transformation Types**: Different types of transformations (IDENTITY, AGGREGATE, CUSTOM)
3. **Multi-table Operations**: Tracking lineage across JOINs
4. **Complex SQL**: Handling aggregations, CASE statements, and functions
5. **Visualization**: Viewing lineage in Marquez Web UI

## Architecture

```
┌─────────────────────────────────────────┐
│   Airflow DAG                           │
│   (postgres_column_lineage_dag.py)     │
└─────────────┬───────────────────────────┘
              │
              │ Executes SQL
              ▼
┌─────────────────────────────────────────┐
│   SQLExecuteQueryOperator               │
│   - Parses SQL automatically            │
│   - Extracts column lineage             │
└─────────────┬───────────────────────────┘
              │
              │ Sends OpenLineage events
              ▼
┌─────────────────────────────────────────┐
│   Marquez (OpenLineage Backend)         │
│   - Stores lineage metadata             │
│   - Builds lineage graph                │
│   - Serves Web UI                       │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│   Marquez Web UI                        │
│   http://localhost:3000                 │
└─────────────────────────────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `postgres_column_lineage_dag.py` | Main DAG with PostgreSQL operations |
| `docker-compose.yml` | Docker Compose for PostgreSQL + Marquez |
| `setup.sh` | Automated setup script |
| `README.md` | This file |

## Quick Start

### Option 1: Automated Setup

```bash
# Make setup script executable
chmod +x setup.sh

# Run setup
./setup.sh
```

The script will:
- Start PostgreSQL and Marquez containers
- Install required Python packages
- Configure Airflow connection
- Copy DAG file to your DAGs folder

### Option 2: Manual Setup

#### 1. Start Docker Services

```bash
docker-compose up -d
```

This starts:
- PostgreSQL (port 5432) - for your data
- Marquez API (port 5000) - OpenLineage backend
- Marquez Web UI (port 3000) - visualization
- Marquez DB (port 5433) - Marquez metadata store

#### 2. Install Python Packages

```bash
pip install apache-airflow-providers-openlineage \
            apache-airflow-providers-postgres \
            apache-airflow-providers-common-sql
```

#### 3. Configure Airflow Connection

```bash
airflow connections add 'postgres_default' \
    --conn-type 'postgres' \
    --conn-host 'localhost' \
    --conn-schema 'airflow_db' \
    --conn-login 'airflow' \
    --conn-password 'airflow' \
    --conn-port 5432
```

#### 4. Configure OpenLineage

Add to `airflow.cfg`:

```ini
[openlineage]
namespace = my_airflow_instance
transport = {"type": "http", "url": "http://localhost:5000"}
```

Or set environment variables:

```bash
export AIRFLOW__OPENLINEAGE__NAMESPACE=my_airflow_instance
export AIRFLOW__OPENLINEAGE__TRANSPORT='{"type": "http", "url": "http://localhost:5000"}'
```

#### 5. Copy DAG File

```bash
# Copy to your Airflow DAGs folder
cp postgres_column_lineage_dag.py ~/airflow/dags/
```

#### 6. Restart Airflow

```bash
# Restart scheduler and webserver to pick up new config
airflow scheduler &
airflow webserver &
```

#### 7. Run the DAG

```bash
# Trigger the DAG
airflow dags trigger postgres_column_lineage_example
```

Or trigger via Web UI at http://localhost:8080

#### 8. View Lineage

Open Marquez Web UI at http://localhost:3000

## DAG Overview

The DAG creates the following tables and tracks lineage:

### Source Tables

1. **orders** - Raw order data
   - order_id, customer_id, product_name, order_date, amount, quantity, status

2. **customers** - Customer information
   - customer_id, first_name, last_name, email, signup_date

### Derived Tables

3. **customer_summary** - Aggregated customer metrics (JOIN + aggregations)
   - customer_id, customer_name, customer_email, total_orders, total_spent, etc.

4. **daily_orders** - Daily order statistics
   - order_date, total_orders, total_revenue, avg_order_value, total_quantity

5. **product_summary** - Product performance with categorization
   - product_name, order_count, total_revenue, revenue_category, popularity_tier

6. **high_value_orders** - Filtered orders with conditional logic
   - order_id, customer_id, product_name, amount, order_date, value_tier

7. **customer_product_matrix** - Customer purchase patterns per product
   - customer_id, customer_name, product_name, purchase_count, buyer_type

## Column Lineage Examples

### Example 1: Identity Transformation

**SQL:**
```sql
SELECT customer_id FROM orders
```

**Lineage:**
- `output.customer_id` ← `orders.customer_id` (IDENTITY)

### Example 2: Aggregation

**SQL:**
```sql
SELECT COUNT(order_id) AS total_orders FROM orders
```

**Lineage:**
- `output.total_orders` ← `orders.order_id` (AGGREGATE - COUNT)

### Example 3: Complex Transformation

**SQL:**
```sql
SELECT CONCAT(first_name, ' ', last_name) AS customer_name
FROM customers
```

**Lineage:**
- `output.customer_name` ← `customers.first_name` (CUSTOM)
- `output.customer_name` ← `customers.last_name` (CUSTOM)

### Example 4: JOIN

**SQL:**
```sql
SELECT c.email, SUM(o.amount) AS total_spent
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
GROUP BY c.email
```

**Lineage:**
- `output.email` ← `customers.email` (IDENTITY)
- `output.total_spent` ← `orders.amount` (AGGREGATE - SUM)

### Example 5: CASE Statement

**SQL:**
```sql
SELECT
    CASE
        WHEN amount > 1000 THEN 'Premium'
        ELSE 'Standard'
    END AS tier
FROM orders
```

**Lineage:**
- `output.tier` ← `orders.amount` (CUSTOM - conditional)

## Viewing Lineage in Marquez

### Via Web UI

1. Open http://localhost:3000
2. Click "Datasets" in the sidebar
3. Find and click on a table (e.g., `customer_summary`)
4. Click the "Lineage" tab
5. Explore the interactive graph showing:
   - Input datasets (orders, customers)
   - Output dataset (customer_summary)
   - Column-level dependencies

### Via API

```bash
# List all datasets
curl http://localhost:5000/api/v1/namespaces/my_airflow_instance/datasets | jq

# Get specific dataset
curl http://localhost:5000/api/v1/namespaces/my_airflow_instance/datasets/customer_summary | jq

# Query lineage graph
curl "http://localhost:5000/api/v1/lineage?nodeId=dataset:my_airflow_instance:customer_summary" | jq
```

### Example Lineage JSON

```json
{
  "outputs": [{
    "namespace": "postgres://localhost:5432",
    "name": "public.customer_summary",
    "facets": {
      "columnLineage": {
        "fields": {
          "customer_id": {
            "inputFields": [{
              "namespace": "postgres://localhost:5432",
              "name": "public.customers",
              "field": "customer_id"
            }],
            "transformationType": "IDENTITY"
          },
          "customer_name": {
            "inputFields": [
              {
                "namespace": "postgres://localhost:5432",
                "name": "public.customers",
                "field": "first_name"
              },
              {
                "namespace": "postgres://localhost:5432",
                "name": "public.customers",
                "field": "last_name"
              }
            ],
            "transformationType": "CUSTOM",
            "transformationDescription": "CONCAT transformation"
          },
          "total_orders": {
            "inputFields": [{
              "namespace": "postgres://localhost:5432",
              "name": "public.orders",
              "field": "order_id"
            }],
            "transformationType": "AGGREGATE",
            "transformationDescription": "COUNT aggregation"
          }
        }
      }
    }
  }]
}
```

## Transformation Types

| Type | Description | Example |
|------|-------------|---------|
| **IDENTITY** | Direct copy | `customer_id` from source to target |
| **AGGREGATE** | Aggregation | `COUNT(order_id)`, `SUM(amount)` |
| **CUSTOM** | Complex transformation | `CONCAT(first_name, last_name)` |
| **TRANSFORM** | Function application | `DATE(order_date)` |

## Troubleshooting

### No lineage appearing?

**Check OpenLineage configuration:**
```bash
airflow config get-value openlineage namespace
airflow config get-value openlineage transport
```

**Check Marquez is running:**
```bash
docker ps | grep marquez
curl http://localhost:5000/api/v1/namespaces
```

**Check task logs:**
```bash
airflow tasks logs postgres_column_lineage_example create_customer_summary <execution_date>
```

### PostgreSQL connection fails?

**Test connection:**
```bash
airflow connections test postgres_default
```

**Check PostgreSQL is running:**
```bash
docker ps | grep postgres
```

**Connect manually:**
```bash
psql -h localhost -U airflow -d airflow_db -p 5432
# Password: airflow
```

### DAG not appearing?

**Check for errors:**
```bash
airflow dags list-import-errors
```

**Verify file location:**
```bash
ls -la ~/airflow/dags/postgres_column_lineage_dag.py
```

**Test DAG syntax:**
```bash
python postgres_column_lineage_dag.py
```

## Useful Commands

```bash
# Docker Commands
docker-compose up -d              # Start services
docker-compose down               # Stop services
docker-compose logs -f marquez-api # View Marquez logs
docker-compose ps                 # Check status

# Airflow Commands
airflow dags trigger postgres_column_lineage_example  # Trigger DAG
airflow dags list-runs -d postgres_column_lineage_example  # Check runs
airflow tasks list postgres_column_lineage_example  # List tasks

# PostgreSQL Commands
psql -h localhost -U airflow -d airflow_db -p 5432  # Connect to DB
docker exec -it postgres-lineage psql -U airflow -d airflow_db  # Via Docker

# Marquez Commands
curl http://localhost:5000/api/v1/namespaces  # List namespaces
curl http://localhost:5000/api/v1/namespaces/my_airflow_instance/datasets  # List datasets
```

## Cleanup

### Stop services
```bash
docker-compose down
```

### Remove all data (including volumes)
```bash
docker-compose down -v
```

### Remove DAG
```bash
rm ~/airflow/dags/postgres_column_lineage_dag.py
```

## Next Steps

1. **Explore the DAG**: Review the SQL queries and understand how lineage is tracked
2. **Modify queries**: Change the SQL to see how lineage adapts
3. **Add more transformations**: Experiment with different SQL operations
4. **Integrate with your DAGs**: Apply these patterns to your production pipelines
5. **Explore Marquez**: Use the Web UI to visualize and understand data flow

## Related Documentation

- [Column Lineage Guide](../column-lineage-guide.md) - Comprehensive column lineage documentation
- [Python Column Lineage](../column-lineage-python.md) - Python-specific lineage implementation
- [OpenLineage Documentation](https://openlineage.io/) - OpenLineage specification
- [Marquez Documentation](https://marquezproject.ai/) - Marquez user guide

## Benefits

### Data Governance
- Track data origins for compliance
- Document data transformations automatically
- Maintain audit trails

### Impact Analysis
- Understand downstream effects of changes
- Plan migrations safely
- Validate refactoring

### Debugging
- Trace data quality issues to source
- Understand transformation logic
- Validate data flow

## Support

For issues or questions:
- Check logs: `docker-compose logs -f`
- Review Airflow logs: `~/airflow/logs/`
- Visit [OpenLineage Slack](https://openlineage.io/community/)

---

**Author**: Atam Agrawal
**Created**: February 2026
**Last Updated**: February 2026
