# Atam's Documentation Repository

This directory contains personal documentation, guides, and reference materials for Apache Airflow development and usage.

## Purpose

The `atamdocs` folder serves as a knowledge base for:
- In-depth technical guides
- Implementation patterns and examples
- Best practices and recommendations
- Research and analysis on Airflow features

## Contents

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
