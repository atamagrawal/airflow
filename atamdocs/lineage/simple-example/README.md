# Simple PostgreSQL Column Lineage Example

A minimal example showing how column lineage works with just 4 simple tasks.

## What This Shows

This example demonstrates the **core concept** of column lineage with minimal complexity:

1. **Create source table** with sample data
2. **Aggregate data** (COUNT, SUM) into a summary table
3. **Read data** (shows even SELECT generates lineage)
4. **Transform with CASE** statement for categorization

## Quick Start (5 minutes)

### Step 1: Start PostgreSQL

```bash
docker run -d \
  --name postgres-simple \
  -e POSTGRES_PASSWORD=airflow \
  -e POSTGRES_USER=airflow \
  -e POSTGRES_DB=mydb \
  -p 5432:5432 \
  postgres:15
```

### Step 2: Install Packages

```bash
pip install apache-airflow-providers-openlineage apache-airflow-providers-postgres
```

### Step 3: Configure Airflow Connection

```bash
airflow connections add postgres_default \
  --conn-type postgres \
  --conn-host localhost \
  --conn-port 5432 \
  --conn-login airflow \
  --conn-password airflow \
  --conn-schema mydb
```

### Step 4: Configure OpenLineage

Add to `airflow.cfg`:

```ini
[openlineage]
namespace = my_airflow
transport = {"type": "file", "log_file_path": "/tmp/lineage.json"}
```

Or set environment variable:

```bash
export AIRFLOW__OPENLINEAGE__NAMESPACE=my_airflow
export AIRFLOW__OPENLINEAGE__TRANSPORT='{"type": "file", "log_file_path": "/tmp/lineage.json"}'
```

### Step 5: Copy DAG and Run

```bash
# Copy DAG file
cp postgres_lineage_simple.py ~/airflow/dags/

# Trigger the DAG
airflow dags trigger postgres_lineage_simple
```

### Step 6: View Lineage

```bash
# View the lineage output
cat /tmp/lineage.json | jq '.outputs[].facets.columnLineage'
```

## What You'll See

The lineage JSON shows column-to-column mappings:

```json
{
  "fields": {
    "customer_id": {
      "inputFields": [{
        "namespace": "postgres://localhost:5432",
        "name": "public.orders",
        "field": "customer_id"
      }],
      "transformationType": "IDENTITY"
    },
    "order_count": {
      "inputFields": [{
        "namespace": "postgres://localhost:5432",
        "name": "public.orders",
        "field": "order_id"
      }],
      "transformationType": "AGGREGATE"
    },
    "total_amount": {
      "inputFields": [{
        "namespace": "postgres://localhost:5432",
        "name": "public.orders",
        "field": "amount"
      }],
      "transformationType": "AGGREGATE"
    }
  }
}
```

## What This Means

### Task: create_summary

**SQL:**
```sql
SELECT
    customer_id,
    COUNT(order_id) as order_count,
    SUM(amount) as total_amount
FROM orders
GROUP BY customer_id;
```

**Column Lineage Automatically Tracked:**
- `customer_summary.customer_id` ← `orders.customer_id` (direct copy)
- `customer_summary.order_count` ← `orders.order_id` (COUNT aggregation)
- `customer_summary.total_amount` ← `orders.amount` (SUM aggregation)

### Task: create_tier

**SQL:**
```sql
SELECT
    customer_id,
    CASE
        WHEN total_amount > 200 THEN 'Gold'
        WHEN total_amount > 100 THEN 'Silver'
        ELSE 'Bronze'
    END as tier
FROM customer_summary;
```

**Column Lineage Automatically Tracked:**
- `customer_tier.customer_id` ← `customer_summary.customer_id`
- `customer_tier.tier` ← `customer_summary.total_amount` (CASE transformation)

## Key Takeaways

1. **Automatic**: You don't need to write any extra code - lineage is extracted from SQL automatically
2. **Transformation Types**: Different operations get different labels (IDENTITY, AGGREGATE, CUSTOM)
3. **Complete Chain**: You can trace `customer_tier.tier` all the way back to `orders.amount`
4. **All Operations**: Even SELECT queries generate lineage information

## Data Flow

```
orders table
├── customer_id  ──────────────┐
├── order_id  (COUNT) ─────┐   │
└── amount  (SUM) ──────┐   │   │
                        │   │   │
                        ▼   ▼   ▼
              customer_summary table
              ├── customer_id ─────────┐
              ├── order_count          │
              └── total_amount (CASE)──┼───┐
                                       │   │
                                       ▼   ▼
                              customer_tier table
                              ├── customer_id
                              └── tier
```

## Troubleshooting

### DAG not appearing?
```bash
# Check for errors
airflow dags list-import-errors

# Verify file
python ~/airflow/dags/postgres_lineage_simple.py
```

### No lineage in file?
```bash
# Check if file exists
ls -la /tmp/lineage.json

# View all events
cat /tmp/lineage.json | jq '.'

# Check Airflow logs
airflow tasks logs postgres_lineage_simple create_summary <execution_date>
```

### PostgreSQL connection fails?
```bash
# Test connection
airflow connections test postgres_default

# Check PostgreSQL is running
docker ps | grep postgres

# Connect manually
psql -h localhost -U airflow -d mydb
```

## Next Steps

1. **Modify the SQL**: Change the queries and see how lineage adapts
2. **Add more tables**: Try joining multiple tables
3. **Complex transformations**: Add more CASE statements, functions
4. **Use Marquez**: Set up Marquez for visual lineage exploration (see example1)

## Compare with Complex Example

| Feature | Simple Example | Complex Example (example1) |
|---------|---------------|---------------------------|
| Tasks | 4 | 10 |
| Tables | 3 | 7 |
| Setup | Docker run | Docker Compose |
| Visualization | JSON file | Marquez Web UI |
| Focus | Core concept | Production-ready patterns |
| Time | 5 minutes | 30 minutes |

For a production-ready setup with visual UI, see [example1](../example1/).

## Files

- `postgres_lineage_simple.py` - Simple DAG (70 lines)
- `README.md` - This file

---

**Author**: Atam Agrawal
**Created**: February 2026
