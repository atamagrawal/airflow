# Airflow Local Testing with Debugger Guide
This guide covers how to test Apache Airflow locally using a debugger (such as pdb, PyCharm, or VSCode).
## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Setup Local Virtual Environment](#setup-local-virtual-environment)
3. [Running Tests with Different IDEs](#running-tests-with-different-ides)
4. [Using Python Debugger (pdb)](#using-python-debugger-pdb)
5. [Common Testing Patterns](#common-testing-patterns)
---
## Prerequisites
Before starting, ensure you have:
- **Python**: Version 3.10, 3.11, 3.12, or 3.13
- **macOS Requirements**:
  - Increase file descriptor limit: `ulimit -n 2048`
  - Install graphviz (for ARM architectures): `brew install graphviz`
- **uv package manager** (recommended): Follow [UV Getting started](https://docs.astral.sh/uv/getting-started/installation/)
## Setup Local Virtual Environment
### Option 1: Using `uv` (Recommended)
```bash
# Navigate to the Airflow directory
cd /Users/maan/Workspace/git/airflow
# Create and activate the virtual environment using uv
uv sync --python 3.10
# Activate the environment
source .venv/bin/activate
```
### Option 2: Using Traditional pip/venv
```bash
# Create virtual environment
python3.10 -m venv airflow_venv
source airflow_venv/bin/activate
# Install Airflow in editable mode
pip install --upgrade pip setuptools wheel
pip install -e ./airflow-core[devel]
```
### Initialize the Database
```bash
# Set up the Airflow database (uses SQLite by default)
airflow db init
# Or reset if needed
airflow db reset --yes
```
---
## Running Tests with Different IDEs
### PyCharm IDE
#### 1. **Configure Test Runner (Simple Local virtualenv)**
- Go to **PyCharm > Preferences > Tools > Python Integrated Tools**
- Set **Default test runner** to `pytest`
- Select your virtual environment as the project interpreter
Then run tests directly:
- Right-click on a test file or test class → **Run 'test_...'**
- To debug: Right-click → **Debug 'test_...'** (or press `Ctrl+Shift+D`)
#### 2. **Configure Breeze as External Tool (Docker-based)**
If you want to run tests in the Breeze environment (Docker):
1. Open **Preferences > Tools > External Tools**
2. Click **+** to create a new tool with:
   - **Name**: `Breeze`
   - **Program**: `breeze`
   - **Arguments**: `testing core-tests $FileRelativePath$`
   - **Working directory**: `$ProjectFileDir$`
3. Add to context menu:
   - Go to **Appearance & Behavior > Menus & Toolbars > Project View Popup Menu**
   - Click **+** and select your Breeze external tool
**Usage**: Right-click on test file → **Breeze**
---
### Visual Studio Code
1. **Install Python Extension**:
   - Open Extensions view (`Cmd+Shift+X`)
   - Search and install "Python"
2. **Configure pytest**:
   - Open Testing view (`Cmd+Shift+T`)
   - Click **Configure Python Tests**
   - Select `pytest` framework
3. **Setup test discovery** (if needed):
   - Create/edit `.vscode/settings.json`:
   ```json
   {
     "python.testing.pytestArgs": ["airflow-core/tests", "providers"]
   }
   ```
4. **Run/Debug Tests**:
   - Click the Run/Debug icons next to tests
   - Set breakpoints and debug using the Debug view (`Cmd+Shift+D`)
---
## Using Python Debugger (pdb)
### Method 1: Using `breakpoint()` (Python 3.7+)
Add to your test code where you want to debug:
```python
def test_my_function():
    result = some_function()
    breakpoint()  # Execution pauses here
    assert result == expected
```
Run the test:
```bash
pytest airflow-core/tests/unit/path/to/test_file.py::TestClass::test_my_function -s
```
The `-s` flag prevents pytest from capturing stdout, allowing interaction with pdb.
### Method 2: Using `import pdb; pdb.set_trace()`
```python
import pdb
def test_my_function():
    result = some_function()
    pdb.set_trace()  # Debugger stops here
    assert result == expected
```
### pdb Commands
Once paused:
- `n` - next line
- `s` - step into function
- `c` - continue execution
- `l` - list current code
- `p variable_name` - print variable
- `h` - help
- `q` - quit debugger
Example:
```bash
(Pdb) p result
42
(Pdb) n
(Pdb) c
```
---
## Common Testing Patterns
### 1. **Run a Single Test File**
```bash
pytest airflow-core/tests/unit/core/test_core.py -v
```
### 2. **Run a Single Test Class**
```bash
pytest airflow-core/tests/unit/core/test_core.py::TestCore -v
```
### 3. **Run a Single Test Method**
```bash
pytest airflow-core/tests/unit/core/test_core.py::TestCore::test_dag_params -v
```
### 4. **Run Tests Matching a Pattern**
```bash
pytest airflow-core/tests/unit/core -k "TestCore and not check" -v
```
### 5. **Run with Debug Output**
```bash
pytest --log-cli-level=DEBUG airflow-core/tests/unit/core/test_core.py::TestCore
```
### 6. **Run Database Tests (Requires SQLite or real DB)**
```bash
# Mark test class as DB test
pytest airflow-core/tests/unit/path/to/test_file.py::TestClass --run-db-tests-only
```
### 7. **Run Tests with Verbose Output and No Capture**
```bash
pytest -vv -s airflow-core/tests/unit/core/test_core.py::TestCore::test_my_test
```
---
## Debugging DB Tests Locally
### 1. **Using SQLite (Default)**
```bash
# Set environment variable for SQLite
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="sqlite:////Users/maan/Workspace/git/airflow/airflow.db"
# Run DB tests
pytest airflow-core/tests/unit/path/to/test_file.py -v
```
### 2. **Using PostgreSQL**
```bash
# Install PostgreSQL and create a test database
createdb airflow_test
# Set connection string
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql://user:password@localhost:5432/airflow_test"
# Initialize DB
airflow db init
# Run tests
pytest airflow-core/tests/unit/path/to/test_file.py::TestClass -v
```
### 3. **Reset Database Between Test Runs**
```bash
# Reset SQLite database
airflow db reset --yes
# Run tests again
pytest airflow-core/tests/unit/path/to/test_file.py -v
```
---
## Debugging Provider Tests
Provider tests follow the same pattern as core tests:
```bash
# Run a specific provider test
pytest providers/common/sql/tests/unit/operators/test_sql.py::TestSQLOperator -v -s
# Run with debugger
pytest -s providers/common/sql/tests/unit/operators/test_sql.py::TestSQLOperator
# Then add breakpoint() in the test code
```
---
## Tips for Efficient Debugging
1. **Use `-s` flag**: Prevents pytest from capturing output, allowing you to interact with debugger
   ```bash
   pytest -s airflow-core/tests/unit/core/test_core.py::TestCore::test_method
   ```
2. **Use `-vv` for verbose output**: Shows more detailed test output
   ```bash
   pytest -vv airflow-core/tests/unit/core/test_core.py::TestCore
   ```
3. **Run tests in isolation**: Helps identify flaky tests
   ```bash
   pytest --count=5 airflow-core/tests/unit/core/test_core.py::TestCore::test_method
   ```
4. **Skip slow tests initially**: Mark long-running tests and skip them
   ```bash
   pytest airflow-core/tests/unit/core/test_core.py -v --ignore-glob="*long*"
   ```
5. **Use pytest fixtures for setup**: Reduces debugging time
   ```python
   @pytest.fixture
   def sample_data():
       return {"key": "value"}
   def test_with_data(sample_data):
       assert sample_data["key"] == "value"
   ```
6. **Mock external dependencies**: Speed up tests and make them deterministic
   ```python
   from unittest import mock
   @mock.patch('airflow.providers.external_service.API')
   def test_with_mock(mock_api):
       mock_api.return_value = {"status": "success"}
       # Your test code
   ```
---
## Troubleshooting
### "AIRFLOW_HOME" not set
```bash
export AIRFLOW_HOME=~/airflow
```
### Too many open files (macOS)
```bash
ulimit -n 2048
# Add to ~/.zshrc or ~/.bashrc to persist:
# echo "ulimit -n 2048" >> ~/.zshrc
```
### Database locked errors
```bash
# Reset database
airflow db reset --yes
```
### Tests not discovered
Ensure `pytest` is installed:
```bash
pip install pytest pytest-cov pytest-xdist
```
### Slow test collection
Use specific test paths instead of wildcards:
```bash
# Good
pytest airflow-core/tests/unit/core/test_core.py -v
# Avoid
pytest airflow-core/tests -v
```
---
## Additional Resources
- [Pytest Documentation](https://docs.pytest.org/)
- [Airflow Testing Guide](../../contributing-docs/testing/unit_tests.rst)
- [Airflow Local Virtualenv Guide](../../contributing-docs/07_local_virtualenv.rst)
- [UV Documentation](https://docs.astral.sh/uv/)
- [Breeze Documentation](../../dev/breeze/doc/README.rst)
---
## Quick Reference Commands
```bash
# Setup
source .venv/bin/activate
airflow db init
# Run tests
pytest airflow-core/tests/unit/core/test_core.py -v -s
# Debug with breakpoint()
pytest -s airflow-core/tests/unit/core/test_core.py::TestClass::test_method
# Run with logging
pytest --log-cli-level=DEBUG airflow-core/tests/unit/core/test_core.py
# Run provider tests
pytest -s providers/common/sql/tests/unit/operators/test_sql.py
# Run DB tests
pytest --run-db-tests-only airflow-core/tests/unit/models/test_dag.py
# Clean database
airflow db reset --yes
```
