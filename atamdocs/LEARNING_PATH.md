# Apache Airflow Learning Path

> Your roadmap to mastering Apache Airflow - from beginner to expert
>
> Repository: https://github.com/apache/airflow

## Navigation

This document provides a structured learning path through the Airflow documentation in this repository.

---

## Learning Levels

### Level 1: Beginner - Getting Started

**Goal:** Set up Airflow and create your first DAG

**Start Here:**

1. **[Getting Started Guide](./getting-started-guide.md)** [~30 minutes]
   - Quick 5-minute setup
   - Create your first DAG
   - Learn basic commands
   - Run your first workflow

**You'll Learn:**
- How to install and configure Airflow
- What a DAG is and how to create one
- Basic operators (Python, Bash)
- How to run and test tasks
- Common CLI commands

**Hands-On Exercise:**
```
Complete all three example DAGs in the Getting Started Guide:
1. Hello World DAG
2. Data Pipeline DAG
3. Branching DAG
```

---

### Level 1.5: Build and Development Setup (For Contributors)

**Goal:** Set up a development environment and build Airflow from source

**Study This:**

1.5. **[Build and Development Setup Guide](./build-and-development-setup.md)** [~45 minutes]
   - Understand the build system
   - Set up UV package manager
   - Build Airflow from source
   - Configure your IDE
   - Learn development workflow

**You'll Learn:**
- How to build Airflow from source
- UV package manager and workspace management
- Building specific components (core, providers)
- Development environment setup
- Common build issues and solutions
- IDE configuration
- Running tests locally

**Hands-On Exercise:**
```
1. Install UV package manager
2. Clone Airflow repository
3. Build full development environment with: uv sync --all-packages
4. Run a test to verify your build
5. Set up your IDE (VS Code or PyCharm)
6. Make a small code change and test it
```

**When to Study:**
- You want to contribute to Airflow
- You need to build from source
- You want to work on providers
- You're debugging issues in Airflow core

**Skip if:**
- You only need to use Airflow (install from PyPI instead)
- You're just learning Airflow concepts

---

### Level 2: Intermediate - Core Concepts

**Goal:** Understand Airflow's architecture and extend it

**Study These:**

2. **[Airflow Implementation Guide](./airflow-implementation-guide.md)** [~2 hours]
   - Understand overall architecture
   - Learn core concepts deeply
   - Understand how components interact
   - Follow 6 detailed code walkthroughs

**You'll Learn:**
- Directory structure and organization
- Core abstractions (DAG, Operator, TaskInstance, etc.)
- How scheduler, executor, and database work together
- Where to find specific functionality in code
- Key files and their purposes

**Hands-On Exercise:**
```
1. Navigate the Airflow codebase using GitHub links
2. Read the source code for PythonOperator
3. Trace the execution of a task from scheduler to completion
4. Understand the DAG parsing process
```

3. **[Operators and Hooks Guide](./operators-and-hooks-guide.md)** [~1.5 hours]
   - Learn operator types and patterns
   - Create custom operators
   - Understand hooks
   - Use the operator-hook pattern

**You'll Learn:**
- Different operator types (Action, Transfer, Sensor)
- How to create custom operators
- Hook design patterns
- XCom for task communication
- Templating and dynamic values
- Testing operators and hooks

**Hands-On Exercise:**
```
1. Create a custom operator for your use case
2. Create a custom hook for an external API
3. Implement the operator-hook pattern
4. Use XComs to pass data between tasks
5. Write tests for your operator
```

---

### Level 3: Advanced - Deep Dives

**Goal:** Master complex topics and optimize performance

**Study These:**

4. **[Scheduler Deep Dive](./scheduler-deep-dive.md)** [~2 hours]
   - Understand scheduler internals
   - Learn the main scheduler loop
   - Master dependency resolution
   - Optimize performance

**You'll Learn:**
- How the scheduler works internally
- DAG parsing mechanism
- DagRun creation logic
- Task scheduling algorithms
- Dependency resolution process
- Performance tuning strategies
- Debugging scheduler issues

**Hands-On Exercise:**
```
1. Monitor scheduler logs during DAG execution
2. Configure scheduler parameters for optimization
3. Debug a scheduling issue
4. Set up multiple schedulers (if using Airflow 2.0+)
5. Analyze database queries made by scheduler
```

---

### Level 4: Specialized Topics

**Goal:** Master specific advanced features

**Study These:**

5. **[Column Lineage Guide](lineage/column-lineage-guide.md)** [~1 hour]
   - Understand column lineage concepts
   - Implement for SQL operators
   - Set up lineage tracking
   - Use for data governance

**You'll Learn:**
- What column lineage is and why it matters
- How Airflow generates lineage automatically
- Implementation for SQL-based operators
- Provider-specific examples (BigQuery, Snowflake)
- Best practices for lineage tracking

**When to Study:**
- Working with SQL transformations
- Need data governance/compliance
- Building data catalogs
- Debugging data quality issues

6. **[Column Lineage for Python](lineage/column-lineage-python.md)** [~1.5 hours]
   - Implement lineage for Python code
   - Handle Pandas and PySpark
   - Manual lineage patterns
   - Best practices

**You'll Learn:**
- Why Python lineage is challenging
- Manual lineage implementation
- Patterns for Pandas DataFrames
- Patterns for PySpark
- Decorator and mixin patterns
- Future automation approaches

**When to Study:**
- Using Python for transformations
- Working with Pandas/PySpark
- ML feature engineering pipelines
- Complex Python data processing

---

## Learning Path Matrix

### By Role

#### Data Engineer
```
Path: 1 → 2 → 3 → 5 → 6
Focus: Creating robust data pipelines
```

#### Platform Engineer / DevOps
```
Path: 1 → 2 → 4 → 3
Focus: Deployment, scaling, monitoring
```

#### Data Scientist / ML Engineer
```
Path: 1 → 3 → 6
Focus: Building ML pipelines, feature engineering
```

#### Software Developer (Contributing to Airflow)
```
Path: 1 → 1.5 → 2 → 4 → 3 → 5 → 6
Focus: Understanding internals, contributing code
```

#### Architect
```
Path: 1 → 2 → 4
Focus: System design, performance, scalability
```

---

## Quick Navigation

### I Want To...

**...get started quickly**
→ [Getting Started Guide](./getting-started-guide.md)

**...build Airflow from source**
→ [Build and Development Setup Guide](./build-and-development-setup.md)

**...understand how Airflow works**
→ [Airflow Implementation Guide](./airflow-implementation-guide.md)

**...create a custom operator**
→ [Operators and Hooks Guide](./operators-and-hooks-guide.md)

**...debug scheduler issues**
→ [Scheduler Deep Dive](./scheduler-deep-dive.md)

**...implement data lineage for SQL**
→ [Column Lineage Guide](lineage/column-lineage-guide.md)

**...implement data lineage for Python**
→ [Column Lineage for Python](lineage/column-lineage-python.md)

**...find a specific topic**
→ [README.md](./README.md) - Detailed topic index

---

## Study Time Estimates

### Weekend Warrior (2 days)
**Goal:** Get productive with Airflow

**Saturday (4 hours):**
- Morning: Getting Started Guide (2 hours)
- Afternoon: Airflow Implementation Guide (2 hours)

**Sunday (4 hours):**
- Morning: Operators and Hooks Guide (2 hours)
- Afternoon: Practice building custom operators (2 hours)

**Result:** Can build and deploy DAGs confidently

---

### Week-Long Deep Dive (5 days)
**Goal:** Become an Airflow expert

**Day 1:** Getting Started + Implementation Guide
**Day 2:** Operators and Hooks Guide + Practice
**Day 3:** Scheduler Deep Dive + Debugging
**Day 4:** Column Lineage (both guides)
**Day 5:** Build a complete real-world pipeline

**Result:** Expert-level understanding, ready to contribute

---

### Lunch-and-Learn Series (6 sessions)
**Goal:** Team onboarding

**Week 1:** Getting Started - Basic concepts
**Week 2:** Architecture and Core Concepts
**Week 3:** Building Custom Operators
**Week 4:** Scheduler Internals
**Week 5:** Data Lineage
**Week 6:** Best Practices and Production Tips

---

## Recommended Order

### Complete Beginner
1. Getting Started Guide
2. Airflow Implementation Guide (Overview sections only)
3. Operators and Hooks Guide (Basic sections)
4. Return to guides as needed for specific topics

### Experienced Developer New to Airflow
1. Getting Started Guide (skim quickly)
2. Airflow Implementation Guide (complete)
3. Scheduler Deep Dive
4. Operators and Hooks Guide
5. Specialized topics as needed

### Airflow User Wanting to Contribute
1. Getting Started Guide (review)
2. Build and Development Setup Guide (complete)
3. Airflow Implementation Guide (complete with code exploration)
4. Scheduler Deep Dive (complete with code exploration)
5. Operators and Hooks Guide (complete)
6. Study actual Airflow source code on GitHub

---

## Additional Resources

### Official Documentation
- **Main Docs:** https://airflow.apache.org/docs/
- **API Reference:** https://airflow.apache.org/docs/apache-airflow/stable/python-api-ref.html
- **Concepts:** https://airflow.apache.org/docs/apache-airflow/stable/concepts/index.html

### GitHub Repository
- **Main Repo:** https://github.com/apache/airflow
- **Core Source:** https://github.com/apache/airflow/tree/main/airflow-core/src/airflow
- **Providers:** https://github.com/apache/airflow/tree/main/providers

### Community
- **Slack:** apache-airflow.slack.com
- **Dev Mailing List:** dev@airflow.apache.org
- **Stack Overflow:** Tag `apache-airflow`
- **YouTube:** Search "Apache Airflow Summit"

### Books
- "Data Pipelines with Apache Airflow" by Bas Harenslak and Julian de Ruiter
- "Apache Airflow: The Hands-On Guide" by Marc Lamberti

### Video Resources
- Airflow Summit talks
- Official YouTube channel
- Conference presentations

---

## Practice Projects

### Beginner Projects

1. **Daily Report Generator**
   - Extract data from API
   - Transform and aggregate
   - Send email with report

2. **File Processing Pipeline**
   - Watch for new files
   - Process CSV/JSON
   - Load to database

3. **Weather Data Pipeline**
   - Fetch from weather API
   - Store historical data
   - Generate visualizations

### Intermediate Projects

4. **ETL Pipeline with Error Handling**
   - Multiple data sources
   - Complex transformations
   - Retry logic and alerts
   - Data quality checks

5. **Multi-Cloud Data Sync**
   - Read from AWS S3
   - Transform data
   - Load to GCP BigQuery

6. **ML Feature Pipeline**
   - Extract raw data
   - Feature engineering
   - Store features for training
   - Versioning

### Advanced Projects

7. **Dynamic DAG Generation**
   - Generate DAGs from config
   - Templating system
   - Validation

8. **Custom Executor**
   - Build custom executor
   - Integration with scheduler
   - Resource management

9. **Lineage-Aware Pipeline**
   - Implement full lineage tracking
   - Custom lineage backend
   - Visualization

---

## Assessment Checklist

### Beginner Level ✓
- [ ] Can install and configure Airflow
- [ ] Can create basic DAGs with Python and Bash operators
- [ ] Can use CLI to trigger and test tasks
- [ ] Understand DAG scheduling basics
- [ ] Can view logs and debug simple issues

### Intermediate Level ✓
- [ ] Understand Airflow architecture
- [ ] Can create custom operators
- [ ] Can use hooks to connect to external systems
- [ ] Understand XComs and templating
- [ ] Can implement error handling and retries
- [ ] Can use Connections and Variables

### Advanced Level ✓
- [ ] Understand scheduler internals
- [ ] Can optimize DAG performance
- [ ] Can debug complex scheduling issues
- [ ] Can implement custom sensors
- [ ] Understand dependency resolution
- [ ] Can tune database and executor settings

### Expert Level ✓
- [ ] Can contribute to Airflow codebase
- [ ] Can build custom executors
- [ ] Can implement advanced lineage tracking
- [ ] Can design production-ready architectures
- [ ] Can mentor others on Airflow

---

## Tips for Effective Learning

### 1. Hands-On Practice
- Don't just read - code along
- Try to break things and fix them
- Experiment with different configurations

### 2. Read Source Code
- Use GitHub links in guides
- Start with simple files
- Trace execution paths
- Add print statements to understand flow

### 3. Join Community
- Ask questions on Slack
- Read GitHub issues
- Attend Airflow Summit
- Contribute documentation

### 4. Build Real Projects
- Start with simple use cases
- Gradually increase complexity
- Deploy to production (with caution)
- Learn from failures

### 5. Keep Updated
- Follow Airflow releases
- Read release notes
- Update your knowledge for new features
- Airflow evolves quickly

---

## Next Steps After Completing

1. **Contribute to Airflow**
   - Fix documentation typos
   - Submit bug reports
   - Create provider packages
   - Contribute code improvements

2. **Share Knowledge**
   - Write blog posts
   - Give presentations
   - Mentor team members
   - Create tutorials

3. **Build Tools**
   - Custom operators for your domain
   - Monitoring dashboards
   - Testing frameworks
   - DAG generators

4. **Stay Current**
   - Subscribe to dev mailing list
   - Watch Airflow GitHub
   - Attend conferences
   - Read Airflow blogs

---

## Getting Help

### When Stuck

1. **Check Logs**
   - Task logs in UI
   - Scheduler logs
   - Webserver logs

2. **Search Documentation**
   - These guides (atamdocs)
   - Official documentation
   - GitHub issues

3. **Debug Systematically**
   - Use `airflow tasks test`
   - Check DAG import errors
   - Verify dependencies
   - Test in isolation

4. **Ask Community**
   - Slack (search first)
   - Stack Overflow (search first)
   - GitHub Discussions

### Common Pitfalls

- Not setting `catchup=False` for new DAGs
- Using SQLite in production
- Heavy computations in DAG file scope
- Not making tasks idempotent
- Ignoring task dependencies
- Not monitoring scheduler health

---

## Success Stories

Share your success stories after completing the learning path!

**Template:**
```
Name: [Your name]
Role: [Your role]
Time to Complete: [Days/weeks]
Path Followed: [Your learning path]
Key Takeaway: [Most valuable thing learned]
Current Use: [How you use Airflow now]
```

---

**Happy Learning!**

Remember: The best way to learn is by doing. Start with simple examples and gradually build up complexity.

---

**Document Version**: 1.0
**Last Updated**: 2026-02-20
**Maintained By**: Atam Agrawal
