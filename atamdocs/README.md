# Atam's Documentation Repository

This directory contains personal documentation, guides, and reference materials for Apache Airflow development and usage.

## Purpose

The `atamdocs` folder serves as a knowledge base for:
- In-depth technical guides
- Implementation patterns and examples
- Best practices and recommendations
- Research and analysis on Airflow features

## Contents

### [Getting Started Guide](./getting-started-guide.md)
**START HERE** - Practical guide to get you up and running with Airflow

**Topics covered:**
- 5-minute quick start setup
- Development environment configuration
- Your first DAG (Hello World, ETL pipeline, Branching)
- Understanding the workflow lifecycle
- Common CLI commands (DAGs, tasks, database, users, connections)
- Debugging tips and troubleshooting
- Next steps and resources

**Use cases:**
- First-time Airflow setup
- Learning basic concepts
- Quick reference for common commands
- Troubleshooting common issues

**Best for:** Complete beginners and as a quick reference guide

---

### [Airflow Implementation Guide](./airflow-implementation-guide.md)
**NEW** - Comprehensive walkthrough of Apache Airflow's codebase and architecture

**Topics covered:**
- Directory structure and organization
- Core concepts (DAG, Operator, TaskInstance, Executor, Scheduler, Hooks)
- Key components deep dive
- How components work together
- 6 detailed code walkthroughs
- GitHub links for each component

**Use cases:**
- Understanding Airflow internals
- Contributing to Airflow
- Debugging complex issues
- Building custom extensions
- Learning the architecture

**Best for:** Developers new to Airflow codebase or wanting deep understanding

---

### [Scheduler Deep Dive](./scheduler-deep-dive.md)
Detailed guide to understanding the Airflow Scheduler

**Topics covered:**
- Scheduler architecture and design
- Main scheduler loop phases
- DAG parsing mechanism
- DagRun creation logic
- Task scheduling and dependency resolution
- Performance optimization
- Monitoring and debugging

**Use cases:**
- Understanding how DAGs are executed
- Performance tuning
- Debugging scheduling issues
- Multi-scheduler setup

**Best for:** Advanced users wanting to understand scheduling internals

---

### [Operators and Hooks Guide](./operators-and-hooks-guide.md)
Comprehensive guide to creating and using Operators and Hooks

**Topics covered:**
- Operator types and base classes
- Creating custom operators
- Built-in operators (Python, Bash, Sensors)
- Hook design patterns
- Creating custom hooks
- Operator-Hook pattern
- Advanced topics (templating, XComs, dynamic mapping)
- Best practices and testing

**Use cases:**
- Building custom operators
- Integrating with external systems
- Understanding operator lifecycle
- Testing operators and hooks

**Best for:** Developers creating custom Airflow extensions

---

### [Column Lineage Guide](./column-lineage-guide.md)
Comprehensive guide to understanding and implementing column-level lineage tracking in Apache Airflow using SQL-based operators.

**Topics covered:**
- Overview of column lineage concepts
- Architecture and components
- How lineage is generated from DAGs
- Implementation examples (identity, SQL parsing, multi-source)
- Provider-specific examples (BigQuery, Snowflake)
- Custom operator implementation
- Best practices and testing
- Current limitations and future improvements

**Use cases:**
- Data governance and compliance
- Impact analysis for schema changes
- Debugging data quality issues
- Auto-generating data flow documentation

**Best for:** SQL-based transformations (automatic lineage)

---

### [Column Lineage for Python Transformations](./column-lineage-python.md)
Dedicated guide for implementing column lineage with Python-based transformations (Pandas, PySpark, custom Python code).

**Topics covered:**
- Why Python lineage is challenging
- Manual column lineage implementation patterns
- Pandas DataFrame transformation examples
- PySpark transformation examples
- Python to database transformations
- Advanced patterns (mixins, decorators)
- Best practices for Python lineage
- Automated solutions and future directions

**Use cases:**
- Pandas-based ETL pipelines
- PySpark data processing
- ML feature engineering
- Custom Python transformations
- Mixed Python/SQL workflows

**Best for:** Python-based transformations (manual lineage required)

## Usage

These documents are meant for:
1. **Reference** - Quick lookup during development
2. **Learning** - Understanding complex Airflow features
3. **Implementation** - Copy-paste ready code examples
4. **Knowledge Sharing** - Share patterns with team members

## Contributing

Feel free to add new documentation files following this structure:
- Clear table of contents
- Practical examples with real code
- Visual diagrams where helpful
- Links to relevant Airflow source files
- Version and date information

## Document Format

Each guide should include:
- Overview and motivation
- Architecture/concepts
- Implementation examples
- Best practices
- Limitations and improvements
- Resources and references

---

**Maintained by:** Atam Agrawal
**Last Updated:** February 2026
