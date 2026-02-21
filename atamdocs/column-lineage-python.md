# Column Lineage for Python Transformations in Airflow

## Table of Contents
1. [Overview](#overview)
2. [The Challenge with Python](#the-challenge-with-python)
3. [Manual Column Lineage Implementation](#manual-column-lineage-implementation)
4. [Pandas DataFrame Transformations](#pandas-dataframe-transformations)
5. [PySpark Transformations](#pyspark-transformations)
6. [Python to Database Transformations](#python-to-database-transformations)
7. [Advanced Patterns](#advanced-patterns)
8. [Best Practices](#best-practices)
9. [Automated Solutions and Future](#automated-solutions-and-future)

---

## Overview

**Key Point:** Unlike SQL-based operators, Python transformations in Airflow do NOT automatically generate column lineage. You must manually implement lineage tracking.

### Why This Matters

If your data pipelines use:
- PythonOperator with Pandas/NumPy
- PySpark transformations
- Custom Python data processing
- Machine learning feature engineering
- Python-based ETL logic

...then you need to **manually specify column lineage** to track data flow.

### Quick Comparison

| Feature | SQL Operators | Python Operators |
|---------|---------------|------------------|
| **Auto Column Lineage** | ✅ Automatic | ❌ Manual only |
| **Complexity** | Simple (parser reads SQL) | Complex (dynamic code) |
| **What's Tracked** | Queries, JOINs, aggregations | Nothing (by default) |
| **Solution** | Works out of box | Custom implementation required |

---

## The Challenge with Python

### Why Python Column Lineage is Hard

```python
# SQL - Easy to parse
sql = """
    SELECT
        customer_id,
        SUM(amount) as total_spent
    FROM orders
    GROUP BY customer_id
"""
# Parser can easily see: total_spent <- orders.amount
```

```python
# Python - Impossible to parse automatically
def transform(df):
    # Dynamic, runtime-dependent transformations
    df['total_spent'] = df['amount'].sum()

    # Conditional logic
    if config['use_discount']:
        df['final_amount'] = df['total_spent'] * 0.9

    # External function calls
    df['processed'] = df.apply(complex_function)

    # How does a parser know what columns are used? It can't!
    return df
```

**The Problem:**
- Python code is executed at runtime, not parse time
- Transformations can be conditional, dynamic, computed
- Column names can be variables, generated programmatically
- External functions and libraries add complexity

---

## Manual Column Lineage Implementation

### Basic Pattern

All manual implementations follow this pattern:

```python
from airflow.models import BaseOperator
from airflow.providers.openlineage.extractors.base import OperatorLineage
from airflow.providers.openlineage.plugins.facets import (
    ColumnLineageDatasetFacet,
    Fields,
    InputField,
    Dataset,
)

class PythonOperatorWithLineage(BaseOperator):
    """Base pattern for Python operators with column lineage"""

    def get_openlineage_facets_on_complete(self, task_instance):
        """Override this method to provide lineage"""

        # 1. Define input dataset(s)
        input_dataset = Dataset(
            namespace="your-namespace",
            name="input_table_or_file"
        )

        # 2. Define output dataset(s)
        output_dataset = Dataset(
            namespace="your-namespace",
            name="output_table_or_file"
        )

        # 3. Map output columns to input columns
        column_lineage = ColumnLineageDatasetFacet(
            fields={
                "output_col": Fields(
                    inputFields=[
                        InputField(
                            namespace="your-namespace",
                            name="input_table",
                            field="input_col"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="What you did"
                )
            }
        )

        # 4. Attach to output dataset
        output_dataset.facets["columnLineage"] = column_lineage

        # 5. Return lineage
        return OperatorLineage(
            inputs=[input_dataset],
            outputs=[output_dataset]
        )
```

---

## Pandas DataFrame Transformations

### Example 1: Simple Pandas ETL

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.openlineage.extractors.base import OperatorLineage
from airflow.providers.openlineage.plugins.facets import (
    ColumnLineageDatasetFacet, Fields, InputField, Dataset
)
from datetime import datetime
import pandas as pd

def transform_customer_data():
    """Transform customer data using Pandas"""
    # Read input
    df = pd.read_csv('/data/raw_customers.csv')

    # Transformations
    df['full_name'] = df['first_name'] + ' ' + df['last_name']
    df['age'] = (pd.Timestamp.now() - pd.to_datetime(df['birth_date'])).dt.days // 365
    df['total_purchases'] = df.groupby('customer_id')['purchase_amount'].transform('sum')
    df['avg_purchase'] = df.groupby('customer_id')['purchase_amount'].transform('mean')

    # Select output columns
    result = df[['customer_id', 'full_name', 'age', 'total_purchases', 'avg_purchase']]
    result.to_csv('/data/processed_customers.csv', index=False)

class PandasOperatorWithLineage(PythonOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        input_dataset = Dataset(
            namespace="file://local",
            name="/data/raw_customers.csv"
        )

        output_dataset = Dataset(
            namespace="file://local",
            name="/data/processed_customers.csv"
        )

        # Manually specify column lineage
        column_lineage = ColumnLineageDatasetFacet(
            fields={
                "customer_id": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="customer_id"
                        )
                    ],
                    transformationType="IDENTITY",
                    transformationDescription="Direct copy"
                ),
                "full_name": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="first_name"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="last_name"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="Concatenate first_name + ' ' + last_name"
                ),
                "age": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="birth_date"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="Calculate age from birth_date using pd.Timestamp"
                ),
                "total_purchases": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="purchase_amount"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="customer_id"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="SUM(purchase_amount) grouped by customer_id"
                ),
                "avg_purchase": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="purchase_amount"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_customers.csv",
                            field="customer_id"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="AVG(purchase_amount) grouped by customer_id"
                ),
            }
        )

        output_dataset.facets["columnLineage"] = column_lineage

        return OperatorLineage(
            inputs=[input_dataset],
            outputs=[output_dataset]
        )

# DAG Definition
with DAG(
    'pandas_with_lineage',
    start_date=datetime(2024, 1, 1),
    schedule='@daily'
) as dag:

    transform_task = PandasOperatorWithLineage(
        task_id='transform_customers',
        python_callable=transform_customer_data
    )
```

---

### Example 2: Pandas with Multiple Input Sources

```python
def merge_customer_and_orders():
    """Merge customers and orders data"""
    customers = pd.read_csv('/data/customers.csv')
    orders = pd.read_csv('/data/orders.csv')

    # Merge
    merged = customers.merge(orders, on='customer_id', how='left')

    # Aggregations
    result = merged.groupby(['customer_id', 'customer_name', 'email']).agg({
        'order_id': 'count',
        'order_amount': ['sum', 'mean']
    }).reset_index()

    result.columns = ['customer_id', 'customer_name', 'email',
                      'order_count', 'total_spent', 'avg_order']

    result.to_csv('/data/customer_summary.csv', index=False)

class PandasMergeOperatorWithLineage(PythonOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        # Two input datasets
        customers_dataset = Dataset(
            namespace="file://local",
            name="/data/customers.csv"
        )

        orders_dataset = Dataset(
            namespace="file://local",
            name="/data/orders.csv"
        )

        output_dataset = Dataset(
            namespace="file://local",
            name="/data/customer_summary.csv"
        )

        column_lineage = ColumnLineageDatasetFacet(
            fields={
                "customer_id": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/customers.csv",
                            field="customer_id"
                        )
                    ],
                    transformationType="IDENTITY",
                    transformationDescription="From customers.csv"
                ),
                "customer_name": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/customers.csv",
                            field="customer_name"
                        )
                    ],
                    transformationType="IDENTITY",
                    transformationDescription="From customers.csv"
                ),
                "email": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/customers.csv",
                            field="email"
                        )
                    ],
                    transformationType="IDENTITY",
                    transformationDescription="From customers.csv"
                ),
                "order_count": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/orders.csv",
                            field="order_id"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="COUNT(order_id) from orders.csv"
                ),
                "total_spent": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/orders.csv",
                            field="order_amount"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="SUM(order_amount) from orders.csv"
                ),
                "avg_order": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/orders.csv",
                            field="order_amount"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="AVG(order_amount) from orders.csv"
                ),
            }
        )

        output_dataset.facets["columnLineage"] = column_lineage

        return OperatorLineage(
            inputs=[customers_dataset, orders_dataset],
            outputs=[output_dataset]
        )
```

---

## PySpark Transformations

### Example 1: PySpark ETL with Column Lineage

```python
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

def spark_transform():
    """PySpark transformation with aggregations"""
    spark = SparkSession.builder.appName("ETL").getOrCreate()

    # Read data
    df = spark.read.parquet('/data/input/transactions')

    # Transformations
    result = df.groupBy('customer_id', 'product_category') \
        .agg(
            F.count('transaction_id').alias('transaction_count'),
            F.sum('amount').alias('total_amount'),
            F.avg('amount').alias('avg_amount'),
            F.max('transaction_date').alias('last_transaction_date')
        )

    result.write.parquet('/data/output/customer_product_summary')

class SparkOperatorWithLineage(PythonOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        input_dataset = Dataset(
            namespace="s3://my-bucket",
            name="data/input/transactions"
        )

        output_dataset = Dataset(
            namespace="s3://my-bucket",
            name="data/output/customer_product_summary"
        )

        column_lineage = ColumnLineageDatasetFacet(
            fields={
                "customer_id": Fields(
                    inputFields=[
                        InputField(
                            namespace="s3://my-bucket",
                            name="data/input/transactions",
                            field="customer_id"
                        )
                    ],
                    transformationType="IDENTITY",
                    transformationDescription="Grouping key"
                ),
                "product_category": Fields(
                    inputFields=[
                        InputField(
                            namespace="s3://my-bucket",
                            name="data/input/transactions",
                            field="product_category"
                        )
                    ],
                    transformationType="IDENTITY",
                    transformationDescription="Grouping key"
                ),
                "transaction_count": Fields(
                    inputFields=[
                        InputField(
                            namespace="s3://my-bucket",
                            name="data/input/transactions",
                            field="transaction_id"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="COUNT(transaction_id) using F.count()"
                ),
                "total_amount": Fields(
                    inputFields=[
                        InputField(
                            namespace="s3://my-bucket",
                            name="data/input/transactions",
                            field="amount"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="SUM(amount) using F.sum()"
                ),
                "avg_amount": Fields(
                    inputFields=[
                        InputField(
                            namespace="s3://my-bucket",
                            name="data/input/transactions",
                            field="amount"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="AVG(amount) using F.avg()"
                ),
                "last_transaction_date": Fields(
                    inputFields=[
                        InputField(
                            namespace="s3://my-bucket",
                            name="data/input/transactions",
                            field="transaction_date"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="MAX(transaction_date) using F.max()"
                ),
            }
        )

        output_dataset.facets["columnLineage"] = column_lineage

        return OperatorLineage(
            inputs=[input_dataset],
            outputs=[output_dataset]
        )

# DAG
with DAG('spark_with_lineage', start_date=datetime(2024, 1, 1)) as dag:
    spark_task = SparkOperatorWithLineage(
        task_id='spark_transform',
        python_callable=spark_transform
    )
```

---

### Example 2: PySpark with Complex Transformations

```python
def spark_feature_engineering():
    """Create ML features using PySpark"""
    spark = SparkSession.builder.appName("Features").getOrCreate()

    df = spark.read.parquet('/data/raw_events')

    # Feature engineering
    features = df \
        .withColumn('hour_of_day', F.hour('event_timestamp')) \
        .withColumn('day_of_week', F.dayofweek('event_timestamp')) \
        .withColumn('is_weekend',
            F.when(F.dayofweek('event_timestamp').isin([1, 7]), 1).otherwise(0)) \
        .withColumn('event_count_7d',
            F.count('*').over(Window.partitionBy('user_id').orderBy('event_timestamp')
                .rangeBetween(-7*86400, 0))) \
        .withColumn('user_tenure_days',
            F.datediff(F.current_date(), 'user_signup_date'))

    features.write.parquet('/data/ml_features')

class SparkFeatureOperatorWithLineage(PythonOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        input_dataset = Dataset(
            namespace="hdfs://cluster",
            name="data/raw_events"
        )

        output_dataset = Dataset(
            namespace="hdfs://cluster",
            name="data/ml_features"
        )

        column_lineage = ColumnLineageDatasetFacet(
            fields={
                "hour_of_day": Fields(
                    inputFields=[
                        InputField(
                            namespace="hdfs://cluster",
                            name="data/raw_events",
                            field="event_timestamp"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="Extract hour using F.hour(event_timestamp)"
                ),
                "day_of_week": Fields(
                    inputFields=[
                        InputField(
                            namespace="hdfs://cluster",
                            name="data/raw_events",
                            field="event_timestamp"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="Extract day of week using F.dayofweek()"
                ),
                "is_weekend": Fields(
                    inputFields=[
                        InputField(
                            namespace="hdfs://cluster",
                            name="data/raw_events",
                            field="event_timestamp"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="Boolean: 1 if dayofweek in [1,7], else 0"
                ),
                "event_count_7d": Fields(
                    inputFields=[
                        InputField(
                            namespace="hdfs://cluster",
                            name="data/raw_events",
                            field="event_timestamp"
                        ),
                        InputField(
                            namespace="hdfs://cluster",
                            name="data/raw_events",
                            field="user_id"
                        )
                    ],
                    transformationType="AGGREGATE",
                    transformationDescription="COUNT(*) over 7-day window partitioned by user_id"
                ),
                "user_tenure_days": Fields(
                    inputFields=[
                        InputField(
                            namespace="hdfs://cluster",
                            name="data/raw_events",
                            field="user_signup_date"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="datediff(current_date, user_signup_date)"
                ),
            }
        )

        output_dataset.facets["columnLineage"] = column_lineage

        return OperatorLineage(
            inputs=[input_dataset],
            outputs=[output_dataset]
        )
```

---

## Python to Database Transformations

### Example: Pandas to PostgreSQL

```python
from airflow.providers.postgres.hooks.postgres import PostgresHook

def pandas_to_postgres():
    """Transform in Pandas, write to PostgreSQL"""
    # Read from CSV
    df = pd.read_csv('/data/raw_sales.csv')

    # Transform
    df['sale_date'] = pd.to_datetime(df['timestamp']).dt.date
    df['revenue'] = df['quantity'] * df['unit_price']
    df['discount_amount'] = df['revenue'] * df['discount_pct']
    df['net_revenue'] = df['revenue'] - df['discount_amount']

    # Write to PostgreSQL
    hook = PostgresHook(postgres_conn_id='postgres_default')
    engine = hook.get_sqlalchemy_engine()
    df.to_sql('sales_summary', engine, if_exists='append', index=False)

class PandasToPostgresWithLineage(PythonOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        input_dataset = Dataset(
            namespace="file://local",
            name="/data/raw_sales.csv"
        )

        output_dataset = Dataset(
            namespace="postgresql://prod-db:5432",
            name="public.sales_summary"
        )

        column_lineage = ColumnLineageDatasetFacet(
            fields={
                "sale_date": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="timestamp"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="Convert timestamp to date using pd.to_datetime().dt.date"
                ),
                "revenue": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="quantity"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="unit_price"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="quantity * unit_price"
                ),
                "discount_amount": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="quantity"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="unit_price"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="discount_pct"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="(quantity * unit_price) * discount_pct"
                ),
                "net_revenue": Fields(
                    inputFields=[
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="quantity"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="unit_price"
                        ),
                        InputField(
                            namespace="file://local",
                            name="/data/raw_sales.csv",
                            field="discount_pct"
                        )
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="revenue - discount_amount"
                ),
            }
        )

        output_dataset.facets["columnLineage"] = column_lineage

        return OperatorLineage(
            inputs=[input_dataset],
            outputs=[output_dataset]
        )
```

---

## Advanced Patterns

### Pattern 1: Reusable Lineage Mixin

```python
class ColumnLineageMixin:
    """Mixin to add column lineage to any PythonOperator"""

    # Define these in your operator
    input_datasets: list = []
    output_datasets: list = []
    column_mapping: dict = {}  # output_col -> [(namespace, name, input_col), ...]

    def get_openlineage_facets_on_complete(self, task_instance):
        if not self.column_mapping:
            return None

        outputs = []
        for output_ds in self.output_datasets:
            fields = {}
            for output_col, input_cols in self.column_mapping.items():
                fields[output_col] = Fields(
                    inputFields=[
                        InputField(
                            namespace=ns,
                            name=name,
                            field=col
                        )
                        for ns, name, col in input_cols
                    ]
                )

            output_ds.facets["columnLineage"] = ColumnLineageDatasetFacet(fields=fields)
            outputs.append(output_ds)

        return OperatorLineage(
            inputs=[Dataset(namespace=ns, name=n)
                   for ns, n, _ in set(c for cols in self.column_mapping.values()
                                      for c in cols)],
            outputs=outputs
        )

# Usage
class MyTransformOperator(ColumnLineageMixin, PythonOperator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.column_mapping = {
            'full_name': [
                ('file://local', 'input.csv', 'first_name'),
                ('file://local', 'input.csv', 'last_name')
            ],
            'age': [('file://local', 'input.csv', 'birth_date')]
        }
```

---

### Pattern 2: Decorator-Based Lineage

```python
from functools import wraps

def track_lineage(input_cols, output_cols, transformations):
    """Decorator to add lineage tracking to Python functions"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            # Store lineage metadata for later extraction
            wrapper._lineage = {
                'inputs': input_cols,
                'outputs': output_cols,
                'transformations': transformations
            }
            return result
        return wrapper
    return decorator

# Usage
@track_lineage(
    input_cols={'input.csv': ['first_name', 'last_name', 'birth_date']},
    output_cols={'output.csv': ['full_name', 'age']},
    transformations={
        'full_name': "CONCAT(first_name, ' ', last_name)",
        'age': "YEAR(CURRENT_DATE) - YEAR(birth_date)"
    }
)
def my_transform():
    df = pd.read_csv('input.csv')
    df['full_name'] = df['first_name'] + ' ' + df['last_name']
    df['age'] = datetime.now().year - pd.to_datetime(df['birth_date']).dt.year
    df[['full_name', 'age']].to_csv('output.csv')
```

---

## Best Practices

### 1. Keep Column Mappings Close to Code

```python
# ✅ Good - Easy to maintain
def transform():
    """
    Column Lineage:
    - full_name <- first_name + last_name
    - age <- birth_date (calculated)
    - total_spent <- amount (SUM)
    """
    # Implementation here
    pass
```

### 2. Use Configuration for Complex Lineage

```python
# config/lineage.yaml
transforms:
  - task: customer_transform
    input: customers.csv
    output: customer_summary.csv
    columns:
      full_name:
        inputs: [first_name, last_name]
        type: CUSTOM
        description: "Concatenate names"
      age:
        inputs: [birth_date]
        type: CUSTOM
        description: "Calculate from birth_date"

# Then load in operator
import yaml

class ConfigDrivenLineageOperator(PythonOperator):
    def __init__(self, lineage_config_path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with open(lineage_config_path) as f:
            self.lineage_config = yaml.safe_load(f)
```

### 3. Create Lineage Tests

```python
def test_lineage_completeness():
    """Ensure all output columns have lineage"""
    operator = MyTransformOperator(task_id='test', python_callable=my_func)
    lineage = operator.get_openlineage_facets_on_complete(None)

    expected_outputs = ['customer_id', 'full_name', 'age']
    actual_outputs = lineage.outputs[0].facets['columnLineage'].fields.keys()

    assert set(expected_outputs) == set(actual_outputs), \
        f"Missing lineage for columns: {set(expected_outputs) - set(actual_outputs)}"
```

### 4. Document Transformation Logic

```python
column_lineage = ColumnLineageDatasetFacet(
    fields={
        "customer_lifetime_value": Fields(
            inputFields=[...],
            transformationType="CUSTOM",
            # Be specific about the transformation!
            transformationDescription=(
                "Calculate CLV using formula: "
                "SUM(purchase_amount) * avg_purchase_frequency * "
                "predicted_lifetime_months / 12"
            )
        )
    }
)
```

---

## Possible Solutions for Python Code Lineage

This section explores various approaches to automatically or semi-automatically track column lineage in Python transformations.

---

### Solution 1: AST (Abstract Syntax Tree) Parsing

**Approach:** Analyze Python source code statically to extract column operations.

**How it works:**
```python
import ast
import inspect

class ColumnLineageVisitor(ast.NodeVisitor):
    """Parse Python code to extract column lineage"""

    def __init__(self):
        self.column_lineage = {}
        self.current_df = None

    def visit_Subscript(self, node):
        """Track df['column'] operations"""
        if isinstance(node.slice, ast.Constant):
            column_name = node.slice.value
            # Track this column access
            self.record_column_usage(column_name)
        self.generic_visit(node)

    def visit_Assign(self, node):
        """Track df['new_col'] = df['old_col'] operations"""
        if isinstance(node.targets[0], ast.Subscript):
            target_col = node.targets[0].slice.value
            # Analyze right side to find source columns
            source_cols = self.extract_columns_from_expr(node.value)
            self.column_lineage[target_col] = source_cols
        self.generic_visit(node)

    def extract_columns_from_expr(self, node):
        """Extract all column references from an expression"""
        columns = []
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant):
                columns.append(child.slice.value)
        return columns

# Usage
def transform_function():
    df['full_name'] = df['first_name'] + ' ' + df['last_name']
    df['age'] = 2024 - df['birth_year']

# Parse the function
source = inspect.getsource(transform_function)
tree = ast.parse(source)
visitor = ColumnLineageVisitor()
visitor.visit(tree)

print(visitor.column_lineage)
# Output: {
#   'full_name': ['first_name', 'last_name'],
#   'age': ['birth_year']
# }
```

**Pros:**
- No runtime overhead
- Can analyze code before execution
- Works with static analysis tools

**Cons:**
- Cannot handle dynamic column names
- Misses complex operations (apply, lambda functions)
- Limited to syntactic analysis

**Implementation Example:**

```python
from airflow.operators.python import PythonOperator
import ast
import inspect

class ASTLineagePythonOperator(PythonOperator):
    """PythonOperator with AST-based lineage extraction"""

    def get_openlineage_facets_on_complete(self, task_instance):
        # Parse the callable's source code
        source = inspect.getsource(self.python_callable)
        tree = ast.parse(source)

        visitor = ColumnLineageVisitor()
        visitor.visit(tree)

        # Convert to OpenLineage format
        column_lineage = ColumnLineageDatasetFacet(
            fields={
                output_col: Fields(
                    inputFields=[
                        InputField(
                            namespace=self.input_namespace,
                            name=self.input_dataset,
                            field=input_col
                        )
                        for input_col in input_cols
                    ],
                    transformationType="CUSTOM",
                    transformationDescription=f"Parsed from source code"
                )
                for output_col, input_cols in visitor.column_lineage.items()
            }
        )

        # Return lineage
        output_dataset = Dataset(
            namespace=self.output_namespace,
            name=self.output_dataset
        )
        output_dataset.facets["columnLineage"] = column_lineage

        return OperatorLineage(
            inputs=[Dataset(namespace=self.input_namespace, name=self.input_dataset)],
            outputs=[output_dataset]
        )
```

---

### Solution 2: Runtime Instrumentation with Pandas Proxy

**Approach:** Wrap DataFrames to track all operations at runtime.

**How it works:**
```python
import pandas as pd
from typing import Dict, List, Set

class LineageTracker:
    """Global lineage tracker"""
    def __init__(self):
        self.column_lineage: Dict[str, Set[str]] = {}
        self.sources: Dict[str, str] = {}  # column -> source dataset

    def record_operation(self, output_col: str, input_cols: List[str]):
        if output_col not in self.column_lineage:
            self.column_lineage[output_col] = set()
        self.column_lineage[output_col].update(input_cols)

lineage_tracker = LineageTracker()

class TrackedDataFrame:
    """Wrapper around pandas DataFrame that tracks column operations"""

    def __init__(self, df: pd.DataFrame, source_name: str = None):
        self._df = df
        self._source = source_name

        # Track source for all columns
        if source_name:
            for col in df.columns:
                lineage_tracker.sources[col] = source_name

    def __getitem__(self, key):
        """Track column access"""
        result = self._df[key]
        if isinstance(key, str):
            # Single column access
            return TrackedSeries(result, source_cols=[key], parent=self)
        elif isinstance(key, list):
            # Multiple columns
            return TrackedDataFrame(result, source_name=self._source)
        return result

    def __setitem__(self, key, value):
        """Track column creation/modification"""
        if isinstance(value, TrackedSeries):
            # Track lineage from tracked series
            lineage_tracker.record_operation(key, value._source_cols)
        elif isinstance(value, pd.Series):
            # Regular series - cannot track lineage automatically
            pass

        self._df[key] = getattr(value, '_series', value)

    @property
    def columns(self):
        return self._df.columns

    def to_csv(self, *args, **kwargs):
        return self._df.to_csv(*args, **kwargs)

    @staticmethod
    def read_csv(filepath, *args, **kwargs):
        df = pd.read_csv(filepath, *args, **kwargs)
        return TrackedDataFrame(df, source_name=filepath)

class TrackedSeries:
    """Wrapper around pandas Series that tracks operations"""

    def __init__(self, series: pd.Series, source_cols: List[str], parent=None):
        self._series = series
        self._source_cols = source_cols
        self._parent = parent

    def __add__(self, other):
        """Track addition operations"""
        if isinstance(other, TrackedSeries):
            combined_sources = self._source_cols + other._source_cols
        else:
            combined_sources = self._source_cols

        result_series = self._series + getattr(other, '_series', other)
        return TrackedSeries(result_series, source_cols=combined_sources, parent=self._parent)

    def __mul__(self, other):
        """Track multiplication"""
        result_series = self._series * getattr(other, '_series', other)
        return TrackedSeries(result_series, source_cols=self._source_cols, parent=self._parent)

    # Implement other operators: __sub__, __div__, etc.

# Usage Example
def transform_with_tracking():
    # Use TrackedDataFrame instead of regular DataFrame
    df = TrackedDataFrame.read_csv('input.csv')

    # Operations are automatically tracked
    df['full_name'] = df['first_name'] + df['last_name']
    df['total_with_tax'] = df['amount'] * 1.1

    df.to_csv('output.csv')

    # Lineage is now available
    print(lineage_tracker.column_lineage)
    # Output: {
    #   'full_name': {'first_name', 'last_name'},
    #   'total_with_tax': {'amount'}
    # }

# Integration with Airflow
class TrackedPythonOperator(PythonOperator):
    def execute(self, context):
        # Reset tracker
        global lineage_tracker
        lineage_tracker = LineageTracker()

        # Execute the callable
        result = super().execute(context)

        # Store lineage for later retrieval
        self._captured_lineage = lineage_tracker.column_lineage
        return result

    def get_openlineage_facets_on_complete(self, task_instance):
        # Use captured lineage to build facets
        column_lineage = ColumnLineageDatasetFacet(
            fields={
                output_col: Fields(
                    inputFields=[
                        InputField(
                            namespace=self.input_namespace,
                            name=self.input_dataset,
                            field=input_col
                        )
                        for input_col in input_cols
                    ],
                    transformationType="CUSTOM",
                    transformationDescription="Automatically tracked at runtime"
                )
                for output_col, input_cols in self._captured_lineage.items()
            }
        )
        # ... return OperatorLineage
```

**Pros:**
- Captures actual runtime behavior
- Handles dynamic operations
- More accurate than static analysis

**Cons:**
- Runtime overhead
- Requires wrapping all DataFrame operations
- Complex to implement for all operations
- May break with Pandas updates

---

### Solution 3: PySpark Lineage via Query Plans

**Approach:** Extract lineage from Spark's logical/physical query plans.

**How it works:**
```python
from pyspark.sql import SparkSession

class SparkLineageExtractor:
    """Extract column lineage from Spark query plans"""

    def __init__(self, df):
        self.df = df

    def extract_column_lineage(self):
        """Parse Spark logical plan to extract column lineage"""
        logical_plan = self.df._jdf.queryExecution().logical().toString()

        # Parse the plan
        lineage = self._parse_plan(logical_plan)
        return lineage

    def _parse_plan(self, plan_str):
        """
        Parse Spark logical plan string.
        Example plan:
        Project [customer_id#0, (first_name#1 + last_name#2) AS full_name#3]
        +- Relation[customer_id#0,first_name#1,last_name#2] csv
        """
        column_lineage = {}

        # Look for Project operations
        import re
        project_pattern = r'Project \[(.*?)\]'
        matches = re.findall(project_pattern, plan_str)

        for match in matches:
            # Parse each column expression
            # full_name#3 depends on first_name#1, last_name#2
            # This is simplified - real parsing is more complex
            pass

        return column_lineage

# Usage
def spark_transform():
    spark = SparkSession.builder.getOrCreate()
    df = spark.read.parquet('input.parquet')

    result = df.withColumn('full_name',
        F.concat(F.col('first_name'), F.lit(' '), F.col('last_name')))

    # Extract lineage before writing
    extractor = SparkLineageExtractor(result)
    lineage = extractor.extract_column_lineage()

    result.write.parquet('output.parquet')
    return lineage

# Airflow integration
class SparkLineageOperator(PythonOperator):
    def execute(self, context):
        # Execute and capture lineage
        self._lineage = self.python_callable()
        return self._lineage

    def get_openlineage_facets_on_complete(self, task_instance):
        # Use self._lineage to build OpenLineage facets
        pass
```

**Pros:**
- Leverages Spark's built-in plan tracking
- Accurate for Spark transformations
- Works with complex operations

**Cons:**
- Spark-specific (doesn't work for Pandas)
- Plan parsing is complex
- May vary across Spark versions

**Real-World Tool:** OpenLineage already has some support for Spark lineage extraction through the `openlineage-spark` library.

---

### Solution 4: Annotation-Based Lineage

**Approach:** Use Python decorators to manually annotate transformations.

**How it works:**
```python
from functools import wraps
from typing import Dict, List

class LineageRegistry:
    """Registry to store lineage annotations"""
    def __init__(self):
        self.lineage_map: Dict[str, Dict] = {}

    def register(self, func_name: str, lineage: Dict):
        self.lineage_map[func_name] = lineage

registry = LineageRegistry()

def lineage(column_mapping: Dict[str, List[str]]):
    """
    Decorator to annotate function with column lineage.

    Args:
        column_mapping: Dict mapping output columns to list of input columns

    Example:
        @lineage({
            'full_name': ['first_name', 'last_name'],
            'age': ['birth_date']
        })
        def transform(df):
            ...
    """
    def decorator(func):
        # Register lineage
        registry.register(func.__name__, column_mapping)

        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            # Attach lineage metadata to result
            if hasattr(result, '_lineage'):
                result._lineage = column_mapping
            return result
        return wrapper
    return decorator

# Usage
@lineage({
    'full_name': ['first_name', 'last_name'],
    'age': ['birth_date'],
    'total_spent': ['amount']
})
def transform_customers(df):
    df['full_name'] = df['first_name'] + ' ' + df['last_name']
    df['age'] = (pd.Timestamp.now() - pd.to_datetime(df['birth_date'])).dt.days // 365
    df['total_spent'] = df.groupby('customer_id')['amount'].transform('sum')
    return df

# Airflow integration
class AnnotatedPythonOperator(PythonOperator):
    def get_openlineage_facets_on_complete(self, task_instance):
        # Get lineage from registry
        func_name = self.python_callable.__name__
        lineage_map = registry.lineage_map.get(func_name, {})

        if not lineage_map:
            return None

        # Build column lineage facet
        column_lineage = ColumnLineageDatasetFacet(
            fields={
                output_col: Fields(
                    inputFields=[
                        InputField(
                            namespace=self.input_namespace,
                            name=self.input_dataset,
                            field=input_col
                        )
                        for input_col in input_cols
                    ],
                    transformationType="CUSTOM",
                    transformationDescription=f"Annotated in @lineage decorator"
                )
                for output_col, input_cols in lineage_map.items()
            }
        )

        # Return lineage
        output_dataset = Dataset(
            namespace=self.output_namespace,
            name=self.output_dataset
        )
        output_dataset.facets["columnLineage"] = column_lineage

        return OperatorLineage(
            inputs=[Dataset(namespace=self.input_namespace, name=self.input_dataset)],
            outputs=[output_dataset]
        )
```

**Pros:**
- Simple to use
- Explicit and clear
- Low overhead
- Easy to maintain

**Cons:**
- Still manual (just cleaner)
- Annotations can get out of sync with code
- Requires discipline to maintain

---

### Solution 5: Hybrid SQL Approach

**Approach:** Convert Python transformations to SQL when possible.

**How it works:**
```python
# Instead of this (no automatic lineage):
def pandas_transform():
    df = pd.read_sql('SELECT * FROM orders', engine)
    df['total_with_tax'] = df['amount'] * 1.1
    df.to_sql('orders_with_tax', engine)

# Do this (automatic lineage):
def sql_transform():
    sql = """
        INSERT INTO orders_with_tax (order_id, amount, total_with_tax)
        SELECT
            order_id,
            amount,
            amount * 1.1 AS total_with_tax
        FROM orders
    """
    engine.execute(sql)  # Column lineage automatically tracked!
```

**Tools that help:**
- **dbt (Data Build Tool)**: Transform data using SQL templates
- **SQLFrame**: Write Pandas-like code that compiles to SQL
- **Ibis**: DataFrame API that generates SQL

**Example with SQLFrame:**
```python
import sqlframe
from sqlframe.pandas import PandasSession

# Looks like Pandas, but generates SQL!
session = PandasSession()
df = session.read_table('orders')

# This generates SQL, so lineage can be tracked
result = df.assign(
    total_with_tax=df['amount'] * 1.1
)

# The actual SQL can be parsed for lineage
print(result.sql())
# Output: SELECT *, amount * 1.1 AS total_with_tax FROM orders
```

**Pros:**
- Leverages existing SQL lineage tracking
- No new infrastructure needed
- Often more performant than Pandas

**Cons:**
- Not all transformations can be expressed in SQL
- Requires refactoring existing code
- Learning curve for new tools

---

### Solution 6: Great Expectations Integration

**Approach:** Use data validation tools that track lineage.

**How it works:**
```python
import great_expectations as gx

# Great Expectations can track column dependencies
context = gx.get_context()

# Define expectations that implicitly describe lineage
expectation_suite = context.create_expectation_suite("orders_suite")

# These expectations describe column relationships
expectation_suite.add_expectation(
    gx.expectations.ExpectColumnValuesToMatchRegex(
        column="full_name",
        regex="^[A-Z][a-z]+ [A-Z][a-z]+$",
        meta={
            "lineage": {
                "derived_from": ["first_name", "last_name"],
                "transformation": "concatenation"
            }
        }
    )
)
```

**Pros:**
- Combines data quality with lineage
- Expectations document transformations
- Validation ensures lineage accuracy

**Cons:**
- Lineage is secondary feature
- Requires Great Expectations adoption
- Manual metadata still needed

---

### Solution 7: Using OpenLineage Python SDK (Future)

**Status:** Under development by OpenLineage community

**Planned features:**
```python
# Future API (not yet available)
from openlineage.pandas import track

@track  # Automatically tracks all DataFrame operations
def transform():
    df = pd.read_csv('input.csv')
    df['full_name'] = df['first_name'] + ' ' + df['last_name']
    df.to_csv('output.csv')
    # Lineage automatically captured and sent to OpenLineage backend

# Alternative: Context manager
from openlineage import lineage_context

with lineage_context(namespace="prod", job="transform"):
    df = pd.read_csv('input.csv')
    df['new_col'] = df['old_col'] * 2
    df.to_csv('output.csv')
    # Lineage automatically tracked within context
```

**Status:** Track progress at https://github.com/OpenLineage/OpenLineage/discussions

---

## Comparison of Solutions

| Solution | Complexity | Accuracy | Overhead | Maintenance |
|----------|------------|----------|----------|-------------|
| **AST Parsing** | Medium | Low | None | Low |
| **Runtime Proxy** | High | High | Medium | High |
| **Spark Plans** | Medium | High | Low | Medium |
| **Annotations** | Low | High | None | Medium |
| **Hybrid SQL** | Low | High | None | Low |
| **Great Expectations** | Medium | Medium | Low | Medium |
| **OpenLineage SDK** | Low | High | Low | Low |

---

## Recommended Approach: Hybrid Strategy

**Best practice:** Combine multiple solutions based on your needs.

```python
# 1. Use SQL for transformations when possible (automatic lineage)
def sql_heavy_transform():
    engine.execute("""
        INSERT INTO summary
        SELECT customer_id, SUM(amount) as total
        FROM orders GROUP BY customer_id
    """)

# 2. Use annotations for unavoidable Python code (semi-automatic)
@lineage({'full_name': ['first_name', 'last_name']})
def python_transform():
    df['full_name'] = df['first_name'] + ' ' + df['last_name']

# 3. Use AST parsing as fallback (automatic but less accurate)
# ... parse source code for basic operations

# 4. Document complex logic manually (last resort)
# For truly complex operations, write good documentation
```

---

## Call to Action

### What You Can Do Today

1. **Start with annotations** - Use the decorator pattern for immediate benefit
2. **Move logic to SQL** - Refactor transformations to SQL where feasible
3. **Document transformations** - Add comments describing column lineage
4. **Contribute to OpenLineage** - Help build automatic Python lineage tracking

### Future Work

The Airflow and OpenLineage communities are actively working on:
- Automatic Pandas lineage extraction
- PySpark query plan integration
- Standard APIs for Python lineage
- UI improvements for visualizing lineage

**Want to contribute?** Join the discussion:
- OpenLineage Slack: https://bit.ly/lineageslack
- OpenLineage GitHub: https://github.com/OpenLineage/OpenLineage
- Airflow Dev List: dev@airflow.apache.org

---

## Summary

### Key Takeaways

1. **Python lineage is manual** - No automatic extraction like SQL
2. **Override `get_openlineage_facets_on_complete()`** - Standard pattern
3. **Document transformations** - Clear descriptions are essential
4. **Test your lineage** - Ensure completeness
5. **Consider SQL alternatives** - If lineage is critical

### Decision Tree

```
Do you need column lineage?
├─ Yes → Is transformation in SQL?
│        ├─ Yes → Use SQL operators (automatic lineage) ✅
│        └─ No → Is it critical?
│                ├─ Yes → Manually implement lineage (this guide)
│                └─ No → Skip lineage, use task-level only
└─ No → Use regular operators
```

### When to Use What

| Scenario | Recommendation |
|----------|----------------|
| **SQL transformations** | Use SQL operators - automatic lineage |
| **Critical Python ETL** | Implement manual lineage (follow this guide) |
| **Ad-hoc Python scripts** | Skip column lineage, use task-level |
| **ML feature engineering** | Document in code, consider manual lineage |
| **Pandas/Spark analysis** | Manual lineage if part of production pipeline |

---

## Resources

### Helpful Links
- [OpenLineage Python Client](https://openlineage.io/docs/client/python/)
- [Airflow OpenLineage Provider](https://airflow.apache.org/docs/apache-airflow-providers-openlineage/)
- [Column Lineage Spec](https://openlineage.io/docs/spec/facets/dataset-facets/column-lineage)

### Related Documentation
- [SQL Column Lineage Guide](./column-lineage-guide.md) - For SQL-based lineage
- [OpenLineage Integration](https://github.com/OpenLineage/OpenLineage)

---

**Last Updated:** February 2026
**Author:** Atam Agrawal
**Version:** 1.0
