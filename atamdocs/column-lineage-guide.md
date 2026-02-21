# Column Lineage in Apache Airflow - Comprehensive Guide

## Table of Contents
1. [Overview](#overview)
2. [How Column Lineage Works](#how-column-lineage-works)
3. [Architecture](#architecture)
4. [Implementation Examples](#implementation-examples)
5. [Provider-Specific Examples](#provider-specific-examples)
6. [Custom Operator Implementation](#custom-operator-implementation)
7. [Best Practices](#best-practices)
8. [Limitations and Future Improvements](#limitations-and-future-improvements)

---

## Overview

Column lineage in Apache Airflow tracks the flow of data at the **column level** across your data pipelines. It answers questions like:
- Which source columns contribute to a target column?
- What transformations are applied?
- How does data flow through multiple datasets?

Airflow implements column lineage through **OpenLineage**, an open standard for data lineage.

### Key Benefits
- **Data Governance**: Track data origins for compliance
- **Impact Analysis**: Understand downstream effects of schema changes
- **Debugging**: Trace data quality issues to source columns
- **Documentation**: Auto-generate data flow documentation

---

## How Column Lineage Works

### Basic Flow

```
Source Table (orders)          Target Table (daily_summary)
├── order_id                   ├── order_date
├── order_date         ───────>├── total_orders
├── customer_id                └── total_amount
└── amount            ────────>
```

Column lineage tracks:
1. **Input Fields**: Source table columns (`orders.order_date`, `orders.amount`)
2. **Output Fields**: Target table columns (`daily_summary.order_date`, `daily_summary.total_amount`)
3. **Transformation**: How data is transformed (IDENTITY, AGGREGATE, CUSTOM)

---

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────┐
│                    Airflow Operator                     │
│  ┌───────────────────────────────────────────────────┐  │
│  │  get_openlineage_facets_on_complete()             │  │
│  │  - Extracts column lineage                        │  │
│  │  - Creates ColumnLineageDatasetFacet              │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              OpenLineage Client                         │
│  - Collects lineage facets                             │
│  - Sends to OpenLineage backend                        │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│         Lineage Backend (Marquez, Datahub, etc.)        │
│  - Stores column lineage graph                         │
│  - Provides visualization and query APIs               │
└─────────────────────────────────────────────────────────┐
```

### Key Classes

1. **ColumnLineageDatasetFacet**: Main class for column lineage metadata
2. **InputField**: Represents a source column
3. **Fields**: Maps output columns to their input columns
4. **Dataset**: Represents a table/dataset with namespace and name

---

## Implementation Examples

### Example 1: Simple Identity Transformation

This is the most common case - copying columns from source to destination without transformation.

```python
from airflow.providers.openlineage.plugins.facets import (
    ColumnLineageDatasetFacet,
    Fields,
    InputField,
    Dataset,
)

def get_identity_column_lineage_facet(
    dest_field_names: list[str],
    input_datasets: list[Dataset],
) -> dict:
    """
    Creates column lineage for identity transformations.
    Example: Copying customer_id from source_table to dest_table
    """
    column_lineage_facet = ColumnLineageDatasetFacet(
        fields={
            field_name: Fields(
                inputFields=[
                    InputField(
                        namespace=dataset.namespace,
                        name=dataset.name,
                        field=field_name  # Same column name in source and dest
                    )
                    for dataset in input_datasets
                ],
                transformationType="IDENTITY",
                transformationDescription="Direct copy without transformation",
            )
            for field_name in dest_field_names
        }
    )
    return {"columnLineage": column_lineage_facet}


# Usage Example
input_datasets = [
    Dataset(namespace="bigquery://project", name="dataset.orders")
]
dest_columns = ["order_id", "customer_id", "amount"]

facets = get_identity_column_lineage_facet(dest_columns, input_datasets)
```

**Result**: Each destination column (`order_id`, `customer_id`, `amount`) is linked to the same-named column in `dataset.orders`.

---

### Example 2: SQL-Based Column Lineage

Airflow can automatically parse SQL queries to extract column lineage.

```python
from airflow.providers.openlineage.sqlparser import SQLParser

# Example SQL Query
sql_query = """
    INSERT INTO daily_summary (order_date, total_orders, total_amount)
    SELECT
        DATE(order_placed_on) as order_date,
        COUNT(*) as total_orders,
        SUM(amount) as total_amount
    FROM orders
    GROUP BY DATE(order_placed_on)
"""

# Parse SQL to extract column lineage
parser = SQLParser()
parse_result = parser.parse(sql_query, database="production")

# The parser identifies:
# - daily_summary.order_date <- orders.order_placed_on (with DATE transformation)
# - daily_summary.total_orders <- orders.* (COUNT aggregation)
# - daily_summary.total_amount <- orders.amount (SUM aggregation)

# Attach lineage to output datasets
def attach_column_lineage(outputs, parse_result):
    for dataset in outputs:
        dataset.facets["columnLineage"] = ColumnLineageDatasetFacet(
            fields={
                col_lineage.descendant.name: Fields(
                    inputFields=[
                        InputField(
                            namespace="bigquery://production",
                            name=col_meta.origin.name,
                            field=col_meta.name
                        )
                        for col_meta in col_lineage.lineage
                    ],
                    transformationType="AGGREGATE" if "COUNT" in sql_query or "SUM" in sql_query else "TRANSFORM",
                    transformationDescription=f"Derived from {col_lineage.lineage[0].name if col_lineage.lineage else 'multiple columns'}"
                )
                for col_lineage in parse_result.column_lineage
            }
        )
```

---

### Example 3: Complex Multi-Source Lineage

When joining multiple tables, output columns may depend on columns from different sources.

```python
from airflow.providers.openlineage.plugins.facets import (
    ColumnLineageDatasetFacet,
    Fields,
    InputField,
)

def get_join_column_lineage():
    """
    Example: JOIN query
    SELECT
        o.order_id,
        o.order_date,
        c.customer_name,
        c.customer_email
    FROM orders o
    JOIN customers c ON o.customer_id = c.customer_id
    """

    column_lineage_facet = ColumnLineageDatasetFacet(
        fields={
            # order_id comes from orders table
            "order_id": Fields(
                inputFields=[
                    InputField(
                        namespace="bigquery://myproject",
                        name="dataset.orders",
                        field="order_id"
                    )
                ],
                transformationType="IDENTITY",
                transformationDescription="Direct from orders.order_id"
            ),
            # order_date comes from orders table
            "order_date": Fields(
                inputFields=[
                    InputField(
                        namespace="bigquery://myproject",
                        name="dataset.orders",
                        field="order_date"
                    )
                ],
                transformationType="IDENTITY",
                transformationDescription="Direct from orders.order_date"
            ),
            # customer_name comes from customers table
            "customer_name": Fields(
                inputFields=[
                    InputField(
                        namespace="bigquery://myproject",
                        name="dataset.customers",
                        field="customer_name"
                    )
                ],
                transformationType="IDENTITY",
                transformationDescription="Direct from customers.customer_name"
            ),
            # customer_email comes from customers table
            "customer_email": Fields(
                inputFields=[
                    InputField(
                        namespace="bigquery://myproject",
                        name="dataset.customers",
                        field="customer_email"
                    )
                ],
                transformationType="IDENTITY",
                transformationDescription="Direct from customers.customer_email"
            ),
        }
    )

    return {"columnLineage": column_lineage_facet}
```

---

## Provider-Specific Examples

### BigQuery Example

```python
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator
from airflow.providers.google.cloud.openlineage.utils import get_identity_column_lineage_facet
from airflow.providers.openlineage.extractors.base import OperatorLineage

class BigQueryOperatorWithLineage(BigQueryInsertJobOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        """Extract column lineage after task completes"""

        # Define input datasets
        input_datasets = [
            Dataset(
                namespace="bigquery://my-gcp-project",
                name="source_dataset.orders"
            )
        ]

        # Define output dataset
        output_dataset = Dataset(
            namespace="bigquery://my-gcp-project",
            name="target_dataset.daily_orders"
        )

        # Define columns being transferred
        dest_columns = ["order_id", "customer_id", "order_date", "amount"]

        # Get column lineage facet
        column_lineage = get_identity_column_lineage_facet(
            dest_field_names=dest_columns,
            input_datasets=input_datasets
        )

        # Attach to output dataset
        output_dataset.facets.update(column_lineage)

        return OperatorLineage(
            inputs=input_datasets,
            outputs=[output_dataset]
        )


# DAG Usage
with DAG("bigquery_lineage_example", start_date=datetime(2024, 1, 1)):
    transfer_data = BigQueryOperatorWithLineage(
        task_id="transfer_orders",
        configuration={
            "query": {
                "query": "SELECT order_id, customer_id, order_date, amount FROM source_dataset.orders",
                "destinationTable": {
                    "projectId": "my-gcp-project",
                    "datasetId": "target_dataset",
                    "tableId": "daily_orders"
                }
            }
        }
    )
```

### Snowflake Example

```python
from airflow.providers.snowflake.operators.snowflake import SnowflakeOperator

class SnowflakeOperatorWithLineage(SnowflakeOperator):
    def __init__(self, source_table, target_table, columns, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_table = source_table
        self.target_table = target_table
        self.columns = columns

    def get_openlineage_facets_on_complete(self, task_instance):
        # Parse Snowflake namespace
        namespace = f"snowflake://{self.snowflake_conn_id}"

        input_dataset = Dataset(
            namespace=namespace,
            name=self.source_table
        )

        output_dataset = Dataset(
            namespace=namespace,
            name=self.target_table
        )

        # Create column lineage
        column_lineage_facet = ColumnLineageDatasetFacet(
            fields={
                col: Fields(
                    inputFields=[
                        InputField(
                            namespace=namespace,
                            name=self.source_table,
                            field=col
                        )
                    ],
                    transformationType="IDENTITY",
                    transformationDescription=f"Copied from {self.source_table}.{col}"
                )
                for col in self.columns
            }
        )

        output_dataset.facets["columnLineage"] = column_lineage_facet

        return OperatorLineage(
            inputs=[input_dataset],
            outputs=[output_dataset]
        )


# DAG Usage
with DAG("snowflake_lineage_example", start_date=datetime(2024, 1, 1)):
    copy_data = SnowflakeOperatorWithLineage(
        task_id="copy_customer_data",
        sql="INSERT INTO target_customers SELECT * FROM source_customers",
        source_table="DB.SCHEMA.source_customers",
        target_table="DB.SCHEMA.target_customers",
        columns=["customer_id", "name", "email", "created_at"],
        snowflake_conn_id="snowflake_default"
    )
```

---

## Custom Operator Implementation

### Step-by-Step Guide

```python
from airflow.models import BaseOperator
from airflow.providers.openlineage.extractors.base import OperatorLineage
from airflow.providers.openlineage.plugins.facets import (
    ColumnLineageDatasetFacet,
    Fields,
    InputField,
    Dataset,
)

class CustomDataTransformOperator(BaseOperator):
    """
    Custom operator that transforms data with column lineage tracking
    """

    def __init__(
        self,
        source_table: str,
        target_table: str,
        column_mapping: dict[str, list[str]],  # output_col -> [input_cols]
        transformation_type: str = "CUSTOM",
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.source_table = source_table
        self.target_table = target_table
        self.column_mapping = column_mapping
        self.transformation_type = transformation_type

    def execute(self, context):
        """Your transformation logic here"""
        self.log.info(f"Transforming data from {self.source_table} to {self.target_table}")
        # ... actual data transformation code ...

    def get_openlineage_facets_on_complete(self, task_instance):
        """
        Called after execute() completes successfully.
        Returns lineage information including column-level details.
        """

        # Define input dataset
        input_dataset = Dataset(
            namespace="postgres://production",
            name=self.source_table
        )

        # Define output dataset
        output_dataset = Dataset(
            namespace="postgres://production",
            name=self.target_table
        )

        # Build column lineage based on mapping
        column_lineage_fields = {}

        for output_col, input_cols in self.column_mapping.items():
            column_lineage_fields[output_col] = Fields(
                inputFields=[
                    InputField(
                        namespace="postgres://production",
                        name=self.source_table,
                        field=input_col
                    )
                    for input_col in input_cols
                ],
                transformationType=self.transformation_type,
                transformationDescription=f"Derived from {', '.join(input_cols)}"
            )

        # Create column lineage facet
        column_lineage_facet = ColumnLineageDatasetFacet(
            fields=column_lineage_fields
        )

        # Attach to output dataset
        output_dataset.facets["columnLineage"] = column_lineage_facet

        return OperatorLineage(
            inputs=[input_dataset],
            outputs=[output_dataset]
        )


# DAG Usage
with DAG("custom_operator_lineage", start_date=datetime(2024, 1, 1)):
    transform = CustomDataTransformOperator(
        task_id="calculate_customer_metrics",
        source_table="raw.customer_orders",
        target_table="analytics.customer_summary",
        column_mapping={
            "customer_id": ["customer_id"],  # Simple copy
            "total_orders": ["order_id"],     # COUNT(order_id)
            "total_spent": ["amount"],        # SUM(amount)
            "avg_order_value": ["amount", "order_id"],  # SUM(amount) / COUNT(order_id)
            "first_order_date": ["order_date"],  # MIN(order_date)
        },
        transformation_type="AGGREGATE"
    )
```

---

## Best Practices

### 1. Always Specify Transformation Types

```python
# Good - Clear transformation type
Fields(
    inputFields=[...],
    transformationType="AGGREGATE",  # or "IDENTITY", "PROJECTION", "CUSTOM"
    transformationDescription="SUM of all order amounts grouped by customer"
)

# Bad - Missing transformation context
Fields(inputFields=[...])
```

### 2. Use Descriptive Namespaces

```python
# Good - Full context
namespace = "bigquery://my-project-prod"
namespace = "snowflake://account.region/database/schema"
namespace = "postgres://production-db:5432/analytics"

# Bad - Ambiguous
namespace = "database"
namespace = "prod"
```

### 3. Handle Multiple Input Sources

```python
# Example: Joining multiple tables
def get_multi_source_lineage(output_columns: dict):
    """
    output_columns = {
        "order_id": [("orders", "order_id")],
        "customer_name": [("customers", "first_name"), ("customers", "last_name")],
        "product_name": [("products", "name")]
    }
    """
    fields = {}
    for output_col, sources in output_columns.items():
        fields[output_col] = Fields(
            inputFields=[
                InputField(
                    namespace="postgres://prod",
                    name=table,
                    field=column
                )
                for table, column in sources
            ],
            transformationType="PROJECTION" if len(sources) == 1 else "CUSTOM",
            transformationDescription=f"Combined from {len(sources)} source column(s)"
        )
    return ColumnLineageDatasetFacet(fields=fields)
```

### 4. Test Your Lineage

```python
def test_column_lineage():
    """Unit test for column lineage"""
    operator = CustomDataTransformOperator(
        task_id="test",
        source_table="source",
        target_table="target",
        column_mapping={"output_col": ["input_col"]}
    )

    lineage = operator.get_openlineage_facets_on_complete(None)

    assert len(lineage.outputs) == 1
    output_dataset = lineage.outputs[0]
    assert "columnLineage" in output_dataset.facets

    col_lineage = output_dataset.facets["columnLineage"]
    assert "output_col" in col_lineage.fields
    assert len(col_lineage.fields["output_col"].inputFields) == 1
    assert col_lineage.fields["output_col"].inputFields[0].field == "input_col"
```

---

## Limitations and Future Improvements

### Current Limitations

1. **Complex Transformations**
   - UDFs (User Defined Functions) are not automatically traced
   - Window functions may not capture all dependencies
   - Nested subqueries can be challenging to parse

2. **Provider Coverage**
   - Not all providers implement column lineage
   - Custom operators require manual implementation
   - Quality varies by provider maturity

3. **SQL Parsing Limitations**
   - Dynamic SQL is difficult to parse at DAG definition time
   - Dialect-specific syntax may not be fully supported
   - Complex CTEs might not be fully traced

4. **Performance**
   - Parsing large SQL queries can be expensive
   - Lineage metadata adds overhead to task execution

### Potential Improvements

#### 1. Enhanced SQL Parsing

```python
# Future improvement: Handle UDFs
def parse_udf_lineage(sql_with_udfs):
    """
    Track column lineage through custom functions
    Example: SELECT calculate_discount(price, category) as final_price
    """
    # Register UDF signature
    register_udf("calculate_discount", inputs=["price", "category"], output="final_price")
    # Parser can now trace: final_price <- [price, category]
```

#### 2. Runtime Lineage Capture

```python
# Instead of static parsing, capture actual query execution
def capture_runtime_lineage(query_execution_metadata):
    """
    Use database query plans to extract actual column usage
    Works with Snowflake query_history, BigQuery INFORMATION_SCHEMA, etc.
    """
    pass
```

#### 3. ML Pipeline Lineage

```python
# Track feature engineering lineage
def get_ml_feature_lineage(feature_transformations):
    """
    Track how raw columns become ML features
    Example:
      - age_bucket <- age (binning)
      - is_weekend <- order_date (extraction)
      - customer_segment <- [total_orders, avg_order_value] (clustering)
    """
    pass
```

#### 4. Lineage Validation

```python
def validate_lineage_completeness(dag):
    """
    Ensure all operators in DAG have lineage information
    Warn about missing column lineage
    """
    for task in dag.tasks:
        if not hasattr(task, 'get_openlineage_facets_on_complete'):
            log.warning(f"Task {task.task_id} missing lineage extraction")
```

#### 5. Visual Lineage UI

```
Future: Built-in Airflow UI for column lineage visualization
- Interactive graph showing column-to-column flow
- Clickable columns to see transformations
- Impact analysis: "What breaks if I change this column?"
```

---

## Configuration

### Enable OpenLineage in Airflow

```python
# airflow.cfg or environment variables
[openlineage]
namespace = my_airflow_instance
transport = {"type": "http", "url": "http://marquez:5000"}

# Or use environment variables
AIRFLOW__OPENLINEAGE__NAMESPACE=my_airflow_instance
AIRFLOW__OPENLINEAGE__TRANSPORT='{"type": "http", "url": "http://marquez:5000"}'
```

### Supported Backends

- **Marquez**: OpenLineage reference implementation
- **DataHub**: LinkedIn's metadata platform
- **Egeria**: ODPI metadata standard
- **Custom**: Implement your own OpenLineage consumer

---

## Resources

### Key Files in Airflow Codebase
- `providers/openlineage/src/airflow/providers/openlineage/sqlparser.py` - SQL parsing logic
- `providers/openlineage/src/airflow/providers/openlineage/plugins/facets.py` - Lineage facet definitions
- `providers/google/src/airflow/providers/google/cloud/openlineage/utils.py` - BigQuery lineage utilities
- `providers/amazon/src/airflow/providers/amazon/aws/utils/openlineage.py` - AWS lineage utilities

### External Resources
- [OpenLineage Specification](https://openlineage.io/)
- [Marquez - OpenLineage Backend](https://marquezproject.ai/)
- [Airflow OpenLineage Provider Docs](https://airflow.apache.org/docs/apache-airflow-providers-openlineage/)

---

## Quick Reference

### Transformation Types

| Type | Description | Example |
|------|-------------|---------|
| `IDENTITY` | Direct copy, no transformation | `target.id = source.id` |
| `PROJECTION` | Select specific columns | `SELECT col1, col2` |
| `AGGREGATE` | Aggregation functions | `SUM()`, `COUNT()`, `AVG()` |
| `FILTER` | Filtering rows | `WHERE clause` |
| `JOIN` | Combining tables | `JOIN operations` |
| `CUSTOM` | Complex transformation | `UDFs`, `CASE statements` |

### Common Patterns

```python
# Pattern 1: Simple copy
get_identity_column_lineage_facet(columns, [source_dataset])

# Pattern 2: SQL parsing
parser.parse(sql_query, database="prod")

# Pattern 3: Manual construction
ColumnLineageDatasetFacet(fields={...})
```

---

**Last Updated**: February 2026
**Author**: Atam Agrawal
**Version**: 1.0
