# Data Transform Operator

A custom Airflow operator for data transformations that automatically emits lineage metadata, similar to how `SQLExecuteQueryOperator` works.

## Overview

This directory contains two versions of data transformation operators that emit lineage:

### Version 1: DataTransformOperator
Explicit lineage configuration - you specify both the transformation AND the lineage.

### Version 2: DataTransformOperatorV2 (Recommended)
**Automatic lineage inference** - you specify the transformation, lineage is inferred automatically!

Just like:
- `SQLExecuteQueryOperator` → executes SQL, **parses it** to extract lineage
- `DataTransformOperatorV2` → transforms data, **infers** lineage from transformation config

## Which Version to Use?

**Use V2** - it's more realistic and similar to how real operators work:
- ✅ Lineage inferred from work (like SQLExecuteQueryOperator)
- ✅ Less configuration required
- ✅ Lineage stays in sync with transformations

**V1** is kept for reference but requires explicit lineage configuration.

## Quick Start

### V2 - Automatic Lineage Inference (Recommended)

```python
from dev.atam.lineage.transform_operator_v2 import DataTransformOperatorV2

# Just specify WHAT to do - lineage is inferred automatically!
task = DataTransformOperatorV2(
    task_id="daily_summary",
    source_table="public.orders",
    dest_table="public.daily_summary",
    source_namespace="postgres://localhost:5432/prod",
    columns=[
        {"name": "order_date", "expr": "order_date"},       # Infers: IDENTITY
        {"name": "total_amount", "expr": "SUM(amount)"},    # Infers: AGGREGATE
        {"name": "order_count", "expr": "COUNT(order_id)"}, # Infers: AGGREGATE
    ],
    group_by_columns=["order_date"]
)
# Lineage automatically inferred:
# - Table: public.orders → public.daily_summary
# - Columns: order_date ← order_date (IDENTITY)
#            total_amount ← amount (AGGREGATE: SUM)
#            order_count ← order_id (AGGREGATE: COUNT)
```

### V1 - Explicit Lineage Configuration

```python
from dev.atam.lineage.transform_operator import DataTransformOperator

def my_transformation(**context):
    """Your actual data transformation logic."""
    print("Reading data...")
    print("Transforming data...")
    print("Writing results...")
    return {"rows_processed": 1000}

task = DataTransformOperator(
    task_id="transform_orders",
    transform_callable=my_transformation,
    source_dataset={
        "namespace": "postgres://localhost:5432/prod",
        "name": "public.orders"
    },
    dest_dataset={
        "namespace": "postgres://localhost:5432/prod",
        "name": "public.daily_summary"
    },
    column_lineage={  # ← Must explicitly specify lineage
        "total_amount": {
            "source": "amount",
            "type": "AGGREGATE",
            "description": "SUM of order amounts"
        }
    }
)
```

## Purpose

### Primary Purpose: Data Transformation
- Execute Python functions that transform data
- Read from source datasets
- Write to destination datasets
- Perform ETL operations

### Secondary Benefit: Automatic Lineage
- Table-level lineage (which tables are read/written)
- Column-level lineage (how columns are transformed)
- Sent to OpenLineage backend automatically

## Features

### 1. Data Transformation
The operator executes your transformation function:
```python
def aggregate_orders(**context):
    # Read from source
    orders = read_table("public.orders")

    # Transform
    daily_summary = orders.groupby("date").agg({
        "amount": "sum",
        "order_id": "count"
    })

    # Write to destination
    write_table(daily_summary, "public.daily_summary")

    return {"rows": len(daily_summary)}
```

### 2. Automatic Lineage Emission
The operator automatically tracks:
- **Table lineage**: `public.orders` → `public.daily_summary`
- **Column lineage**:
  - `total_amount` ← `amount` (AGGREGATE)
  - `order_count` ← `order_id` (AGGREGATE)

## How V2 Infers Lineage

`DataTransformOperatorV2` analyzes transformation expressions to infer lineage automatically:

### Expression Analysis

```python
# Expression: "order_id"
# Infers: IDENTITY transformation (direct copy)

# Expression: "SUM(amount)"
# Infers: AGGREGATE transformation, source column = amount

# Expression: "COUNT(order_id)"
# Infers: AGGREGATE transformation, source column = order_id

# Expression: "ROUND(amount, 2)"
# Infers: TRANSFORM transformation, source column = amount

# Expression: "DATE(created_at)"
# Infers: TRANSFORM transformation, source column = created_at
```

The operator parses expressions similar to how `SQLExecuteQueryOperator` parses SQL!

### Inference Logic

1. **Check for aggregation functions**: `SUM`, `COUNT`, `AVG`, `MAX`, `MIN`
   - Type: `AGGREGATE`
   - Extracts source column from function argument

2. **Check for transformation functions**: `DATE`, `ROUND`, `UPPER`, `LOWER`, `CAST`
   - Type: `TRANSFORM`
   - Extracts source column from function argument

3. **Default to identity**: If no function detected
   - Type: `IDENTITY`
   - Expression itself is the source column

## Comparison with SQLExecuteQueryOperator

| Aspect | SQLExecuteQueryOperator | DataTransformOperatorV2 | DataTransformOperator (V1) |
|--------|------------------------|------------------------|---------------------------|
| **Primary Purpose** | Execute SQL queries | Execute transformations | Execute transformations |
| **Lineage Source** | Parsed from SQL automatically | Inferred from config automatically | Configured explicitly |
| **User Provides** | SQL query | Transformation config | Transformation + lineage |
| **Lineage Method** | SQL parser analyzes query | Expression analyzer | Manual specification |
| **Use Case** | Database operations | Python/config transformations | Custom logic with manual lineage |
| **Lineage as** | Byproduct of SQL parsing | Byproduct of config analysis | Explicit input |
| **Most Similar To** | - | ✅ **SQLExecuteQueryOperator** | PythonOperator + lineage config |

## Configuration

### Basic Configuration

```python
DataTransformOperator(
    task_id="my_task",
    transform_callable=my_function,  # Required: your transformation
    source_dataset={...},             # Optional: for lineage
    dest_dataset={...},               # Optional: for lineage
    column_lineage={...}              # Optional: for lineage
)
```

### Column Lineage Format

```python
column_lineage = {
    "output_column": {
        "source": "input_column",      # Source column name
        "type": "IDENTITY",            # Transformation type
        "description": "..."           # Optional description
    }
}
```

### Transformation Types

- **`IDENTITY`** - Direct copy
- **`TRANSFORM`** - Custom transformation
- **`AGGREGATE`** - Aggregation (SUM, COUNT, AVG, etc.)

## Examples

### Example 1: Simple Data Copy

```python
def copy_data(**context):
    data = extract_from_source()
    load_to_destination(data)
    return {"rows": len(data)}

copy_task = DataTransformOperator(
    task_id="copy_orders",
    transform_callable=copy_data,
    source_dataset={
        "namespace": "postgres://localhost:5432/db",
        "name": "public.orders"
    },
    dest_dataset={
        "namespace": "postgres://localhost:5432/db",
        "name": "public.orders_backup"
    },
    column_lineage={
        "order_id": {"source": "order_id", "type": "IDENTITY"},
        "amount": {"source": "amount", "type": "IDENTITY"},
    }
)
```

### Example 2: Aggregation

```python
def daily_aggregation(**context):
    orders = read_orders()
    summary = orders.groupby("date").agg({
        "amount": ["sum", "mean"],
        "order_id": "count"
    })
    write_summary(summary)
    return summary

agg_task = DataTransformOperator(
    task_id="aggregate_daily",
    transform_callable=daily_aggregation,
    source_dataset={
        "namespace": "postgres://localhost:5432/db",
        "name": "public.orders"
    },
    dest_dataset={
        "namespace": "postgres://localhost:5432/db",
        "name": "public.daily_summary"
    },
    column_lineage={
        "date": {"source": "order_date", "type": "IDENTITY"},
        "total_amount": {
            "source": "amount",
            "type": "AGGREGATE",
            "description": "SUM(amount)"
        },
        "order_count": {
            "source": "order_id",
            "type": "AGGREGATE",
            "description": "COUNT(order_id)"
        },
    }
)
```

### Example 3: Custom Transformation

```python
def enrich_data(**context):
    orders = read_orders()
    # Custom enrichment logic
    orders["priority"] = orders["amount"].apply(calculate_priority)
    orders["region"] = orders["postal_code"].apply(get_region)
    write_enriched(orders)

enrich_task = DataTransformOperator(
    task_id="enrich_orders",
    transform_callable=enrich_data,
    source_dataset={
        "namespace": "postgres://localhost:5432/db",
        "name": "public.orders"
    },
    dest_dataset={
        "namespace": "postgres://localhost:5432/db",
        "name": "public.enriched_orders"
    },
    column_lineage={
        "priority": {
            "source": "amount",
            "type": "TRANSFORM",
            "description": "Calculated from amount"
        },
        "region": {
            "source": "postal_code",
            "type": "TRANSFORM",
            "description": "Derived from postal code"
        },
    }
)
```

## Lineage Visualization

When configured, lineage appears in your OpenLineage backend:

```
┌─────────────────┐
│ public.orders   │
│ (source)        │
└────────┬────────┘
         │
         │ Transformation:
         │ - order_date → date (IDENTITY)
         │ - amount → total_amount (AGGREGATE: SUM)
         │ - order_id → order_count (AGGREGATE: COUNT)
         │
         ▼
┌─────────────────────┐
│ public.daily_summary│
│ (destination)       │
└─────────────────────┘
```

## When to Use

### Use DataTransformOperator when:
- ✅ You have custom Python transformation logic
- ✅ You want automatic lineage tracking
- ✅ You're not using SQL (use SQLExecuteQueryOperator for SQL)
- ✅ You need flexibility in transformation code

### Don't use when:
- ❌ You're executing SQL (use `SQLExecuteQueryOperator`)
- ❌ You don't need lineage tracking (use `PythonOperator`)
- ❌ You need complex multi-source lineage (extend the operator)

## OpenLineage Integration

The operator integrates with Airflow's OpenLineage plugin. Configure in `airflow.cfg`:

```ini
[openlineage]
transport = {"type": "http", "url": "http://marquez:5000"}
namespace = my_airflow_instance
```

Lineage will be automatically sent to your OpenLineage backend when tasks complete.

## How Lineage Emission Works

Understanding how `get_openlineage_facets_on_complete` is called helps you understand the integration:

### Call Chain

```
1. Airflow Core (Task Completion)
   │
   └─> 2. OpenLineageListener (Plugin Hook)
       │   File: providers/openlineage/src/airflow/providers/openlineage/plugins/listener.py
       │   Method: on_task_instance_success()
       │
       └─> 3. Listener Internal Handler
           │   Method: _on_task_instance_success()
           │
           └─> 4. ExtractorManager
               │   File: providers/openlineage/src/airflow/providers/openlineage/extractors/manager.py
               │   Method: extract_metadata()
               │
               └─> 5. DefaultExtractor
                   │   File: providers/openlineage/src/airflow/providers/openlineage/extractors/base.py
                   │   Method: extract_on_complete()
                   │
                   └─> 6. YOUR OPERATOR
                       │   File: dev/atam/lineage/transform_operator.py
                       │   Method: get_openlineage_facets_on_complete()
                       │
                       └─> Returns OperatorLineage (inputs, outputs, facets)
```

### Step-by-Step Flow

#### 1. **Task Completes Successfully**
When your `DataTransformOperator` task finishes executing successfully, Airflow core triggers all registered listeners.

#### 2. **OpenLineageListener Intercepts Event**
The OpenLineage plugin registers a listener with the `@hookimpl` decorator:

```python
# listener.py:249
@hookimpl
def on_task_instance_success(
    self,
    previous_state: TaskInstanceState,
    task_instance: RuntimeTaskInstance
) -> None:
    self._on_task_instance_success(task_instance, dag, dagrun, task)
```

This is a **hook implementation** - Airflow automatically calls this when any task succeeds.

#### 3. **ExtractorManager Finds the Right Extractor**
The listener delegates to `ExtractorManager` to extract metadata:

```python
# manager.py:93
def extract_metadata(self, dagrun, task, task_instance_state, task_instance):
    extractor = self._get_extractor(task)  # Find extractor for this operator

    if task_instance_state == TaskInstanceState.SUCCESS:
        task_metadata = extractor.extract_on_complete(task_instance)
```

The manager looks for:
1. Custom extractor registered for your operator class
2. Falls back to `DefaultExtractor` (which checks for `get_openlineage_facets_on_*` methods)

#### 4. **DefaultExtractor Checks for Method**
The `DefaultExtractor` uses reflection to find your method:

```python
# base.py:123
def extract_on_complete(self, task_instance):
    method = getattr(self.operator, 'get_openlineage_facets_on_complete', None)
    if callable(method):
        return self._get_openlineage_facets(method, task_instance)
```

This is why you just need to implement `get_openlineage_facets_on_complete` - the extractor automatically discovers it!

#### 5. **Your Operator Returns Lineage**
Your operator's method is called:

```python
# transform_operator.py:134
def get_openlineage_facets_on_complete(self, task_instance) -> OperatorLineage:
    # Build inputs
    inputs = [InputDataset(namespace=..., name=...)]

    # Build outputs with column lineage facets
    outputs = [OutputDataset(namespace=..., name=..., facets={...})]

    # Return lineage
    return OperatorLineage(inputs=inputs, outputs=outputs)
```

#### 6. **Lineage Sent to Backend**
The `OpenLineageAdapter` (adapter.py) takes your `OperatorLineage` and:
1. Wraps it in an OpenLineage event (with run ID, timestamps, etc.)
2. Serializes to JSON
3. Sends to configured OpenLineage backend (HTTP, Kafka, etc.)

### Key Concepts

#### Plugin Architecture
The **OpenLineageProviderPlugin** (plugins/openlineage.py) registers itself:

```python
class OpenLineageProviderPlugin(AirflowPlugin):
    name = "OpenLineageProviderPlugin"
    listeners = [get_openlineage_listener()]  # Registers the listener
```

When Airflow loads plugins, it automatically hooks this listener into the task lifecycle.

#### Hook Implementation (`@hookimpl`)
The `@hookimpl` decorator marks methods that should be called by Airflow's plugin system:

```python
@hookimpl
def on_task_instance_success(...):  # Called on task success
@hookimpl
def on_task_instance_running(...):  # Called on task start
@hookimpl
def on_task_instance_failed(...):   # Called on task failure
```

These are **callbacks** - Airflow calls them at the appropriate time.

#### Extractor Pattern
Airflow uses the **Extractor Pattern** to get lineage from operators:

1. **Custom Extractors**: For complex operators (like SQL), custom extractors parse queries
2. **Default Extractor**: For simple operators, checks for `get_openlineage_facets_*` methods
3. **No Extractor**: No lineage emitted

Your `DataTransformOperator` uses the **Default Extractor** approach - just implement the method!

### Why This Design?

This design allows:
- **Loose Coupling**: Operators don't need to know about OpenLineage
- **Opt-in**: Only operators with `get_openlineage_facets_*` emit lineage
- **Extensibility**: Custom extractors can add complex logic (SQL parsing, etc.)
- **Automatic**: No manual code needed to send events - it's all automatic

### Comparison with SQLExecuteQueryOperator

| Aspect | SQLExecuteQueryOperator | DataTransformOperator |
|--------|------------------------|----------------------|
| **Method Called** | `get_openlineage_facets_on_complete()` | `get_openlineage_facets_on_complete()` |
| **Extractor Used** | Custom SQL extractor (parses SQL) | Default extractor (calls method) |
| **Lineage Source** | Parsed from SQL statements | Configured explicitly |
| **Same Hook** | ✅ Yes - same listener | ✅ Yes - same listener |
| **Same Flow** | ✅ Yes - same call chain | ✅ Yes - same call chain |

Both operators use **the exact same mechanism** - they just differ in how lineage is extracted!

### Debugging Lineage

If lineage isn't appearing, check:

1. **Is OpenLineage plugin enabled?**
   ```bash
   # Check airflow.cfg
   [openlineage]
   disabled = false
   ```

2. **Is the listener registered?**
   ```bash
   airflow plugins
   # Should show: OpenLineageProviderPlugin
   ```

3. **Is your method being called?**
   ```python
   def get_openlineage_facets_on_complete(self, task_instance):
       self.log.info("LINEAGE METHOD CALLED!")  # Add logging
       ...
   ```

4. **Check Airflow logs:**
   ```bash
   # Look for OpenLineage debug logs
   grep -i "openlineage" airflow/logs/...
   ```

5. **Is lineage sent to backend?**
   - Check your Marquez/OpenLineage backend logs
   - Verify transport configuration in `airflow.cfg`

## Files

### Recommended (V2 - Automatic Lineage Inference)
- **`transform_operator_v2.py`** - V2 operator with automatic lineage inference
- **`example_transform_dag_v2.py`** - V2 example DAG showing automatic lineage

### Reference (V1 - Explicit Lineage)
- **`transform_operator.py`** - V1 operator with explicit lineage configuration
- **`example_transform_dag.py`** - V1 example DAG with explicit lineage

### Documentation
- **`README.md`** - This documentation

## Summary

### DataTransformOperatorV2 (Recommended)
A **data transformation operator** that works like `SQLExecuteQueryOperator`:
- **Primary purpose**: Execute data transformations
- **Lineage**: Automatically inferred from transformation config (like SQL parsing)
- **User provides**: Transformation specification (WHAT to do)
- **Operator does**: Infers lineage automatically (HOW data flows)

### DataTransformOperator (V1)
Reference implementation with explicit lineage:
- **Primary purpose**: Execute data transformations
- **Lineage**: Explicitly configured by user
- **Use case**: Custom scenarios where automatic inference isn't possible

**The key insight**: Like `SQLExecuteQueryOperator`, V2 analyzes what you're doing and figures out lineage automatically - you don't specify it explicitly!
