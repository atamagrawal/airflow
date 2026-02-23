# Apache Airflow Build and Development Setup Guide

> Complete guide to building Apache Airflow from source and setting up your development environment
>
> **Repository:** https://github.com/apache/airflow
> **Last Updated:** February 2026

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Build System Architecture](#build-system-architecture)
- [Quick Start: Development Setup with UV](#quick-start-development-setup-with-uv)
- [Building Specific Components](#building-specific-components)
- [Alternative: Docker with Breeze](#alternative-docker-with-breeze)
- [End User Installation](#end-user-installation)
- [Common Build Issues](#common-build-issues)
- [Testing Your Build](#testing-your-build)
- [IDE Setup](#ide-setup)
- [Best Practices](#best-practices)
- [References](#references)

---

## Overview

Apache Airflow uses a modern Python build system based on **PEP 517/518** with `hatchling` as the build backend. The project has transitioned to using **`uv`** as the recommended tool for development environment management due to its speed and workspace support.

### Key Information

- **Build System:** `hatchling` (defined in `pyproject.toml`)
- **Package Manager:** `uv` (recommended for development)
- **Python Versions:** 3.10, 3.11, 3.12, 3.13
- **Project Structure:** Monorepo with workspaces (airflow-core, providers, task-sdk, etc.)
- **No Traditional Makefile:** Uses modern Python packaging standards

---

## Prerequisites

### System Requirements

**For Development:**
- Python 3.10, 3.11, 3.12, or 3.13
- `uv` package manager
- Git
- 4GB+ RAM (8GB recommended)
- 10GB+ disk space
- macOS, Linux, or WSL2 on Windows

**For Docker Development:**
- Docker Engine
- Docker Compose plugin
- 4GB RAM, 40GB disk space, 2+ cores
- Docker buildx

### System Packages

Depending on your OS, you may need additional packages:

**Ubuntu/Debian:**
```bash
sudo apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    pkgconf \
    graphviz
```

**macOS:**
```bash
brew install pkgconf graphviz
```

---

## Build System Architecture

### Project Structure

```
airflow/
├── pyproject.toml           # Root workspace configuration
├── airflow-core/            # Core Airflow package
│   ├── pyproject.toml       # Core package config
│   └── src/airflow/         # Core source code
├── providers/               # Provider packages
│   ├── amazon/              # AWS provider
│   ├── google/              # GCP provider
│   └── ...
├── task-sdk/                # Task SDK package
├── airflow-ctl/             # CLI tool package
└── contributing-docs/       # Development documentation
```

### Build Backend

From `pyproject.toml`:
```toml
[build-system]
requires = [
    "GitPython==3.1.45",
    "hatchling==1.27.0",
    "packaging==25.0",
    # ... other build dependencies
]
build-backend = "hatchling.build"
```

---

## Quick Start: Development Setup with UV

### Step 1: Install UV

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Or via Homebrew (macOS):**
```bash
brew install uv
```

**Windows:**
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

For more installation options, see: https://docs.astral.sh/uv/getting-started/installation/

### Step 2: Configure System (macOS Users)

macOS has a low file descriptor limit that can cause issues with workspace installations:

```bash
ulimit -n 2048
```

Add this to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.):
```bash
echo "ulimit -n 2048" >> ~/.zshrc
source ~/.zshrc
```

### Step 3: Clone Repository

```bash
git clone https://github.com/apache/airflow.git
cd airflow
```

### Step 4: Build and Install Development Environment

**Option A: Full Development Environment (Recommended)**

Install all packages including airflow-core, all providers, and development dependencies:

```bash
uv sync --all-packages
```

This installs:
- Airflow core with all optional dependencies
- All provider packages
- Task SDK
- Development tools and test dependencies
- Pre-commit hooks dependencies

**Option B: Core Only**

For working on airflow-core only:

```bash
cd airflow-core
uv sync --all-packages
```

> Note: Currently requires `--all-packages` until provider tests are fully separated.

**Option C: Specific Provider**

To work on a specific provider (e.g., Amazon):

```bash
uv sync --package apache-airflow-providers-amazon
```

This installs:
- Airflow core
- Amazon provider and its dependencies
- All providers that Amazon depends on
- Development dependencies

### Step 5: Activate Virtual Environment

`uv` creates a virtual environment in `.venv/`:

```bash
source .venv/bin/activate
```

### Step 6: Verify Installation

```bash
# Check Python version
python --version

# Check Airflow installation
python -c "import airflow; print(airflow.__version__)"

# Run a simple test
pytest airflow-core/tests/models/test_dag.py -k test_dag_default_view
```

---

## Building Specific Components

### Building Airflow Core

```bash
cd airflow-core
uv sync

# Build wheel
uv build

# Output: dist/apache_airflow-*.whl
```

### Building a Provider Package

```bash
cd providers/amazon
uv sync

# Build wheel
uv build

# Output: dist/apache_airflow_providers_amazon-*.whl
```

### Building Task SDK

```bash
cd task-sdk
uv sync
uv build
```

### Installing in Editable Mode

When you run `uv sync`, packages are installed in editable mode automatically. This means your code changes are immediately reflected without reinstalling.

Verify editable installation:
```bash
pip list | grep airflow
# Should show paths to your local directories
```

---

## Alternative: Docker with Breeze

Breeze is a Python tool that provides a containerized development environment.

### Install Breeze

```bash
# Using uv (recommended)
uv tool install apache-airflow-breeze

# Or using pipx
pipx install apache-airflow-breeze
```

### Start Development Shell

```bash
breeze shell

# With specific Python version
breeze shell --python 3.11

# With specific backend
breeze shell --backend postgres
```

### Run Tests in Breeze

```bash
breeze testing tests --test-type Core

# Run specific test
breeze testing tests tests/models/test_dag.py
```

### Build Production Image

```bash
breeze prod-image build

# With specific providers
breeze prod-image build --install-providers-from-sources
```

### Benefits of Breeze
- Consistent environment across all developers
- Includes all system dependencies
- Easy integration testing with databases
- Reproducible CI environment locally

### Drawbacks of Breeze
- Requires Docker (4GB RAM, 40GB disk)
- Slightly slower than native development
- Harder to debug with IDE
- Additional layer of complexity

---

## End User Installation

If you're not developing Airflow but just want to use it:

### Install from PyPI

```bash
# Create virtual environment
python -m venv airflow_venv
source airflow_venv/bin/activate

# Install Airflow with constraints
pip install apache-airflow==3.0.0 \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.0.0/constraints-3.11.txt"
```

### Install with Extras

```bash
# Install with specific providers
pip install "apache-airflow[amazon,google,postgres]==3.0.0" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.0.0/constraints-3.11.txt"
```

### Run Airflow

```bash
# Set home directory
export AIRFLOW_HOME=~/airflow

# Initialize database
airflow db init

# Create admin user
airflow users create \
    --username admin \
    --firstname Admin \
    --lastname User \
    --role Admin \
    --email admin@example.com

# Start Airflow (all components)
airflow standalone
```

Access UI at: http://localhost:8080

---

## Common Build Issues

### Issue: "Too many open files" on macOS

**Solution:**
```bash
ulimit -n 2048
```

### Issue: "Command not found: uv"

**Solution:**
```bash
# Ensure uv is in PATH
export PATH="$HOME/.cargo/bin:$PATH"

# Or reinstall uv
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Issue: Package build fails with "missing dependencies"

**Solution:**
```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt-get install build-essential python3-dev libxml2-dev

# macOS
brew install pkgconf
```

### Issue: "externally-managed-environment" error

**Solution:**
Don't use system Python. Use `uv` which manages Python versions:
```bash
uv python install 3.11
uv venv --python 3.11
```

### Issue: Import errors after installation

**Solution:**
```bash
# Ensure you're in the virtual environment
source .venv/bin/activate

# Reinstall in editable mode
uv sync --reinstall
```

### Issue: Tests fail with database errors

**Solution:**
```bash
# Reset test database
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="sqlite:////tmp/airflow_test.db"
airflow db reset --yes
```

---

## Testing Your Build

### Run Unit Tests

```bash
# Run all core tests
pytest airflow-core/tests/

# Run specific test file
pytest airflow-core/tests/models/test_dag.py

# Run specific test
pytest airflow-core/tests/models/test_dag.py::TestDag::test_dag_default_view

# Run with markers
pytest -m "not integration"
```

### Run Provider Tests

```bash
# Test specific provider
pytest providers/amazon/tests/

# Run with coverage
pytest --cov=airflow providers/amazon/tests/
```

### Run Static Checks

```bash
# Install pre-commit
pre-commit install

# Run all checks
pre-commit run --all-files

# Run specific check
pre-commit run ruff --all-files
```

### Run Type Checks

```bash
# Using mypy
mypy airflow-core/src/airflow/models/dag.py
```

---

## IDE Setup

### VS Code

Create `.vscode/settings.json`:
```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": [
    "airflow-core/tests"
  ],
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true
}
```

### PyCharm

1. **Set Python Interpreter:**
   - File → Settings → Project → Python Interpreter
   - Add Interpreter → Existing Environment
   - Select `.venv/bin/python`

2. **Configure Test Runner:**
   - Settings → Tools → Python Integrated Tools
   - Default test runner: pytest
   - Working directory: `airflow-core/tests`

3. **Mark Source Directories:**
   - Right-click `airflow-core/src` → Mark Directory as → Sources Root
   - Right-click `airflow-core/tests` → Mark Directory as → Test Sources Root

### Configure Debugging

**VS Code launch.json:**
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: Airflow Scheduler",
      "type": "python",
      "request": "launch",
      "module": "airflow",
      "args": ["scheduler"],
      "console": "integratedTerminal"
    },
    {
      "name": "Python: Current Test",
      "type": "python",
      "request": "launch",
      "module": "pytest",
      "args": ["${file}", "-v"],
      "console": "integratedTerminal"
    }
  ]
}
```

---

## Best Practices

### Development Workflow

1. **Use UV for dependency management:**
   ```bash
   # Add new dependency
   uv add <package>

   # Update dependencies
   uv sync

   # Lock dependencies
   uv lock
   ```

2. **Use pre-commit hooks:**
   ```bash
   pre-commit install
   pre-commit run --all-files
   ```

3. **Run tests before committing:**
   ```bash
   pytest airflow-core/tests/models/
   ```

4. **Keep virtual environment clean:**
   ```bash
   # Recreate venv if needed (uv is fast!)
   rm -rf .venv
   uv sync
   ```

### Performance Tips

1. **Use `--no-install` to skip reinstalls:**
   ```bash
   pytest --no-install airflow-core/tests/
   ```

2. **Run tests in parallel:**
   ```bash
   pytest -n auto airflow-core/tests/
   ```

3. **Use test markers to run subsets:**
   ```bash
   pytest -m "not slow" airflow-core/tests/
   ```

### Code Quality

1. **Format code with ruff:**
   ```bash
   ruff format .
   ```

2. **Check for issues:**
   ```bash
   ruff check .
   ```

3. **Run type checks:**
   ```bash
   mypy airflow-core/src/airflow
   ```

---

## References

### Official Documentation

- **UV Documentation:** https://docs.astral.sh/uv/
- **Airflow Contributing Guide:** https://github.com/apache/airflow/blob/main/contributing-docs/03_contributors_quick_start.rst
- **Local Virtualenv Setup:** https://github.com/apache/airflow/blob/main/contributing-docs/07_local_virtualenv.rst
- **Development Environments:** https://github.com/apache/airflow/blob/main/contributing-docs/06_development_environments.rst

### Source Code References

- **Root pyproject.toml:** https://github.com/apache/airflow/blob/main/pyproject.toml
- **Build Configuration:** https://github.com/apache/airflow/blob/main/pyproject.toml#L18-L30
- **Core Package:** https://github.com/apache/airflow/tree/main/airflow-core
- **Providers:** https://github.com/apache/airflow/tree/main/providers

### Community Resources

- **Slack:** https://apache-airflow.slack.com
- **GitHub Issues:** https://github.com/apache/airflow/issues
- **Dev Mailing List:** dev@airflow.apache.org
- **Stack Overflow:** Tag `apache-airflow`

---

## Quick Reference

### Common Commands

```bash
# Install full dev environment
uv sync --all-packages

# Install specific provider
uv sync --package apache-airflow-providers-amazon

# Activate virtual environment
source .venv/bin/activate

# Run tests
pytest airflow-core/tests/

# Run static checks
pre-commit run --all-files

# Build wheel
uv build

# Start Breeze shell
breeze shell

# Clean and reinstall
rm -rf .venv && uv sync
```

### File Locations

- **Virtual Environment:** `.venv/`
- **Core Source:** `airflow-core/src/airflow/`
- **Core Tests:** `airflow-core/tests/`
- **Provider Source:** `providers/<provider-name>/src/`
- **Provider Tests:** `providers/<provider-name>/tests/`
- **Build Output:** `dist/`

---

**Document Version:** 1.0
**Last Updated:** 2026-02-22
**Maintained By:** Atam Agrawal
**Airflow Version:** 3.0.0+
