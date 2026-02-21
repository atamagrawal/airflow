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
Comprehensive guide to understanding and implementing column-level lineage tracking in Apache Airflow.

**Topics covered:**
- Overview of column lineage concepts
- Architecture and components
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
