# AIP-03-0003: Airflow DAG Template System

## Status
Draft

## Authors
- Atam Prakash

## Created
2026-03-26

---

## Abstract

This AIP proposes a system for Airflow DAG templates, enabling data engineering teams to quickly create, share, and deploy pre-defined DAG patterns. The goal is to increase developer velocity, reduce errors, and promote standardization across pipelines by providing reusable, modular DAG templates.

---

## Motivation

Data teams often rewrite similar DAGs for common workflows (e.g., ETL from S3 to Snowflake, data validation, ML training pipelines). This leads to:

- Repetitive boilerplate code
- Inconsistent DAG designs and standards
- Slow onboarding of new engineers

A template system standardizes these patterns, enforces best practices, and allows rapid deployment.

---

## Goals

1. Provide reusable DAG templates with configurable parameters
2. Enable sharing of templates across teams or organizations
3. Support a library of standard operators and patterns
4. Integrate with version control for template management
5. Allow template instantiation as new DAGs with minimal customization

---

## Non-Goals

- Replacing Airflow core execution engine
- Handling dynamic DAG generation outside the template system
- Providing full low-level operator abstraction beyond standard patterns

---

## Proposal

### 1. Template Definition

Define DAG templates as parameterized Python modules:

```python
# example_template.py
from airflow.decorators import dag, task

@dag(schedule=None, default_args=default_args)
def s3_to_snowflake_dag(s3_bucket, table_name):

    @task
    def extract():
        return f"Reading from {s3_bucket}"

    @task
    def load(data):
        print(f"Loading {data} into {table_name}")

    load(extract())
```

- Parameters: DAG-level (schedules, retries) and task-level (bucket names, table names)
- Metadata: Author, description, recommended best practices

---

### 2. Template Registry

A central registry to store and manage templates:
- Catalog templates by category, source, and author
- Versioning for templates
- Permissions for sharing across teams

---

### 3. Template Instantiation

CLI or UI to instantiate a new DAG from a template:
```bash
airflow template instantiate \
  --template s3_to_snowflake \
  --dag-id new_dag \
  --params bucket=my-bucket,table=users
```

- Generates a new DAG file in the Airflow DAG folder
- Parameters are substituted at task or DAG level
- Optionally include template-specific hooks and monitors

---

### 4. UI Integration

- Browse available DAG templates
- Preview template DAG structure and tasks
- Instantiate templates directly from UI

---

### 5. Version Control Integration

- Templates stored in Git or internal registry
- Track changes, approve updates, and rollback versions
- Enable collaboration across teams

---

## Architecture

Components:
1. Template Definition (Python modules with metadata)
2. Template Registry (DB or file-based catalog)
3. Instantiation Engine (CLI/UI)
4. Version Control Adapter
5. Optional Template Marketplace (internal or external sharing)

Flow:
1. Engineer selects a template
2. Provide parameters (CLI/UI)
3. Instantiation engine generates a new DAG file
4. DAG becomes executable in Airflow

---

## API Changes

- `airflow template list` — list available templates
- `airflow template show <template>` — view template details
- `airflow template instantiate` — generate a new DAG from template

---

## Security Considerations

- Template access control based on team permissions
- Parameter validation to prevent unsafe DAGs
- Optional signing of templates to ensure authenticity

---

## Performance Considerations

- Templates are lightweight; instantiation is local
- Registry can be cached for faster browsing

---

## Future Work

- Template marketplace with curated DAG patterns
- Integration with Airflow AI Copilot for auto-generating DAGs from templates
- Support for multi-tenant template sharing

---

## Conclusion

The Airflow DAG Template System standardizes and accelerates DAG creation, promotes best practices, and reduces boilerplate, improving developer efficiency and reliability of Airflow pipelines.

