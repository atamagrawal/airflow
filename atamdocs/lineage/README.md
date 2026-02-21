# Column Lineage Examples

This directory contains practical examples demonstrating column-level lineage tracking in Apache Airflow using OpenLineage.

## Overview

Column lineage tracks how data flows through your pipelines at the column level, answering questions like:
- Which source columns contribute to a target column?
- What transformations are applied?
- How does data flow through multiple datasets?

## Available Examples

### [Example 1: PostgreSQL Column Lineage](./example1/)

**What it demonstrates:**
- Automatic column lineage for PostgreSQL operations
- Table creation, insertion, and transformations
- JOINs, aggregations, and complex SQL
- Complete Docker setup with Marquez (OpenLineage backend)

**Covers:**
- ✓ Identity transformations (direct column copy)
- ✓ Aggregations (COUNT, SUM, AVG, MIN, MAX)
- ✓ Complex transformations (CONCAT, CASE statements)
- ✓ Multi-table JOINs
- ✓ INSERT INTO ... SELECT operations
- ✓ Conditional logic and categorization

**Level:** Beginner to Intermediate

**Time to complete:** 15-30 minutes

**[Go to Example 1 →](./example1/README.md)**

## Getting Started

Each example is self-contained with:
- DAG file demonstrating the pattern
- Docker Compose setup for dependencies
- Setup script for automation
- Comprehensive documentation

### Prerequisites

- Docker and Docker Compose
- Apache Airflow 2.8+
- Python 3.8+
- Basic understanding of SQL

### Quick Start

1. Choose an example
2. Navigate to the example directory
3. Run the setup script:
   ```bash
   cd example1
   chmod +x setup.sh
   ./setup.sh
   ```
4. Follow the example-specific instructions

## How Column Lineage Works in Airflow

### For SQL-based Operators

Airflow automatically parses SQL queries to extract column lineage:

```python
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

# This automatically generates column lineage
task = SQLExecuteQueryOperator(
    task_id="transform_data",
    conn_id="postgres_default",
    sql="""
        INSERT INTO summary (customer_id, total_spent)
        SELECT customer_id, SUM(amount)
        FROM orders
        GROUP BY customer_id
    """
)
```

**Lineage tracked automatically:**
- `summary.customer_id` ← `orders.customer_id` (IDENTITY)
- `summary.total_spent` ← `orders.amount` (AGGREGATE - SUM)

### For Python-based Operators

Python transformations require manual lineage implementation:

```python
class PythonOperatorWithLineage(BaseOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        # Manually specify column lineage
        return OperatorLineage(
            inputs=[...],
            outputs=[...]
        )
```

See [Python Column Lineage Guide](../column-lineage-python.md) for details.

## Architecture

```
┌─────────────────────────────────┐
│   Airflow DAG                   │
│   - SQL Operators               │
│   - Python Operators            │
└────────────┬────────────────────┘
             │
             │ Executes transformations
             ▼
┌─────────────────────────────────┐
│   OpenLineage Integration       │
│   - Parses SQL                  │
│   - Extracts lineage            │
│   - Formats events              │
└────────────┬────────────────────┘
             │
             │ HTTP POST
             ▼
┌─────────────────────────────────┐
│   Lineage Backend               │
│   - Marquez                     │
│   - DataHub                     │
│   - Custom                      │
└────────────┬────────────────────┘
             │
             │ Visualization
             ▼
┌─────────────────────────────────┐
│   Web UI / API                  │
│   - Explore lineage             │
│   - Impact analysis             │
└─────────────────────────────────┘
```

## Supported Operators

### Operators with Automatic Lineage

✓ **SQLExecuteQueryOperator** (Common SQL)
✓ **BigQueryInsertJobOperator** (Google Cloud)
✓ **SnowflakeOperator** (Snowflake)
✓ **RedshiftDataOperator** (AWS Redshift)
✓ **PostgresOperator** (PostgreSQL)
✓ **MySqlOperator** (MySQL)

### Operators Requiring Manual Implementation

⚠ **PythonOperator**
⚠ **KubernetesPodOperator**
⚠ **BashOperator** (with SQL scripts)
⚠ Custom operators

## Transformation Types

| Type | Description | Example SQL |
|------|-------------|-------------|
| **IDENTITY** | Direct column copy | `SELECT customer_id FROM orders` |
| **AGGREGATE** | Aggregation functions | `SELECT COUNT(*) FROM orders` |
| **CUSTOM** | Complex transformations | `SELECT CONCAT(first, last)` |
| **TRANSFORM** | Function application | `SELECT DATE(timestamp)` |
| **JOIN** | Combining tables | `SELECT * FROM a JOIN b` |
| **FILTER** | Filtering rows | `SELECT * WHERE status='active'` |

## Benefits

### Data Governance
- **Compliance**: Track data origins for GDPR, CCPA
- **Auditing**: Maintain complete audit trails
- **Documentation**: Auto-generate data flow docs

### Impact Analysis
- **Change Management**: Understand downstream effects
- **Migration Planning**: Plan schema changes safely
- **Refactoring**: Validate transformation changes

### Debugging
- **Data Quality**: Trace issues to source columns
- **Validation**: Verify transformation logic
- **Troubleshooting**: Understand data flow

### Collaboration
- **Knowledge Sharing**: Visualize data dependencies
- **Onboarding**: Help new team members understand pipelines
- **Communication**: Share data flow with stakeholders

## OpenLineage Backends

### Marquez (Recommended for Getting Started)

- Official OpenLineage reference implementation
- Easy Docker setup
- Built-in Web UI
- REST API

**Setup:**
```bash
docker-compose up -d
```

### DataHub

- LinkedIn's metadata platform
- Rich UI and features
- Strong community support
- Enterprise-ready

**Setup:**
```bash
datahub docker quickstart
```

### Custom Backend

Implement your own OpenLineage consumer:
- Parse OpenLineage events
- Store in your database
- Build custom visualizations

## Configuration

### Enable OpenLineage in Airflow

**Option 1: airflow.cfg**
```ini
[openlineage]
namespace = my_airflow_instance
transport = {"type": "http", "url": "http://localhost:5000"}
```

**Option 2: Environment Variables**
```bash
export AIRFLOW__OPENLINEAGE__NAMESPACE=my_airflow_instance
export AIRFLOW__OPENLINEAGE__TRANSPORT='{"type": "http", "url": "http://localhost:5000"}'
```

**Option 3: File Transport (Testing)**
```ini
[openlineage]
transport = {"type": "file", "log_file_path": "/tmp/lineage.json"}
```

## Example Scenarios

### Scenario 1: Simple ETL Pipeline
```python
# Extract from source
extract = SQLExecuteQueryOperator(
    task_id="extract",
    sql="SELECT * FROM source_table"
)

# Transform data
transform = SQLExecuteQueryOperator(
    task_id="transform",
    sql="INSERT INTO target SELECT col1, SUM(col2) FROM staging GROUP BY col1"
)

extract >> transform
```

**Lineage tracked:**
- Extract: `staging.col1` ← `source_table.col1`
- Transform: `target.col1` ← `staging.col1` (IDENTITY)
- Transform: `target.col2` ← `staging.col2` (AGGREGATE)

### Scenario 2: Multi-Source Integration
```python
# Join multiple sources
join = SQLExecuteQueryOperator(
    task_id="join_sources",
    sql="""
        INSERT INTO customer_orders
        SELECT c.id, c.name, o.amount
        FROM customers c
        JOIN orders o ON c.id = o.customer_id
    """
)
```

**Lineage tracked:**
- `customer_orders.id` ← `customers.id`
- `customer_orders.name` ← `customers.name`
- `customer_orders.amount` ← `orders.amount`

### Scenario 3: Complex Transformations
```python
# Aggregations and calculations
analytics = SQLExecuteQueryOperator(
    task_id="build_analytics",
    sql="""
        INSERT INTO analytics
        SELECT
            product_id,
            COUNT(*) as order_count,
            SUM(amount) as revenue,
            AVG(amount) as avg_order_value,
            CASE
                WHEN SUM(amount) > 10000 THEN 'High'
                ELSE 'Low'
            END as revenue_tier
        FROM orders
        GROUP BY product_id
    """
)
```

**Lineage tracked:**
- `analytics.product_id` ← `orders.product_id` (IDENTITY)
- `analytics.order_count` ← `orders.*` (AGGREGATE - COUNT)
- `analytics.revenue` ← `orders.amount` (AGGREGATE - SUM)
- `analytics.avg_order_value` ← `orders.amount` (AGGREGATE - AVG)
- `analytics.revenue_tier` ← `orders.amount` (CUSTOM - CASE)

## Best Practices

### 1. Use Descriptive Namespaces
```python
# Good - Clear environment context
namespace = "prod_data_warehouse"
namespace = "dev_analytics"

# Bad - Ambiguous
namespace = "airflow"
namespace = "prod"
```

### 2. Test Lineage Extraction
```python
def test_operator_lineage():
    operator = MyOperator(...)
    lineage = operator.get_openlineage_facets_on_complete(None)
    assert len(lineage.outputs) > 0
    assert "columnLineage" in lineage.outputs[0].facets
```

### 3. Document Transformation Logic
```python
SQLExecuteQueryOperator(
    task_id="complex_transform",
    sql="...",
    doc_md="""
    ### Customer Summary Transformation

    **Column Lineage:**
    - customer_name ← first_name, last_name (CONCAT)
    - total_orders ← order_id (COUNT)
    """
)
```

### 4. Handle Complex SQL Carefully
- Break down very complex queries into steps
- Some UDFs may not be tracked automatically
- Consider manual lineage for dynamic SQL

### 5. Monitor Lineage Extraction
```python
[openlineage]
# Enable debug logging
logging_level = DEBUG

# Check for extraction errors
disabled_for_operators = problematic.Operator
```

## Troubleshooting

### Common Issues

**No lineage appearing?**
- Check OpenLineage config
- Verify backend is running
- Enable debug logging
- Check task logs for errors

**Incomplete lineage?**
- Complex SQL may not parse fully
- UDFs not captured
- Dynamic SQL limitations
- Consider manual implementation

**Performance issues?**
- Large SQL queries slow to parse
- Consider caching parsed results
- Use file transport for testing

## Resources

### Documentation
- [Column Lineage Guide](../column-lineage-guide.md) - Comprehensive guide
- [Python Column Lineage](../column-lineage-python.md) - Python-specific patterns
- [Airflow Implementation Docs](../airflow-implementation.md) - Implementation details

### External Resources
- [OpenLineage Specification](https://openlineage.io/)
- [Marquez Documentation](https://marquezproject.ai/)
- [Airflow OpenLineage Provider](https://airflow.apache.org/docs/apache-airflow-providers-openlineage/)

### Community
- [OpenLineage Slack](https://openlineage.io/community/)
- [Airflow Slack](https://apache-airflow-slack.herokuapp.com/) - #openlineage channel
- [GitHub Issues](https://github.com/apache/airflow/labels/area%3Aopenlineage)

## Contributing

Have an example to share? Contributions welcome!

1. Create a new directory: `example{N}/`
2. Include:
   - DAG file with clear comments
   - README with setup instructions
   - Docker Compose (if needed)
   - Setup script
3. Update this index
4. Submit a PR

## Future Examples

Coming soon:
- Example 2: Python Operator Column Lineage
- Example 3: BigQuery Column Lineage
- Example 4: Snowflake Column Lineage
- Example 5: Multi-Provider Lineage
- Example 6: Custom Operator Implementation

## Support

For questions or issues:
- Check example-specific README files
- Review comprehensive guides in `../`
- Visit OpenLineage community channels

---

**Author**: Atam Agrawal
**Last Updated**: February 2026
