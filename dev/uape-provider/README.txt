UAPE (Uncertainty-Aware Parallelization Engine) — advisory CLI with conservative defaults.

The ``AirflowPlugin`` (``/uape`` HTTP mount and UI tabs) is registered with the ``airflow.plugins``
entry point in ``pyproject.toml``. For Docker, use ``example/plugins/uape_plugin_loader.py`` copied
into ``$AIRFLOW_HOME/plugins/`` (see ``example/Dockerfile``) so discovery does not depend on entry points.

**FAB auth:** the UI loads plugin tabs from ``GET /api/v2/plugins``, which requires the **Plugins**
permission. If tabs are missing, grant your role **can read** on **Plugins** (Admin has it by default).

Restart the API server after install.

Install into the same environment as Airflow (example from repo root):

  uv pip install -e dev/uape-provider

Then:

  airflow uape independence-report <dag_id> [--format text|json]
  airflow uape export <dag_id> [--format json]

After restarting the API server:

* **Airflow UI:** plugin tab **UAPE recommendations** (iframe) on the DAG page, Dag Run page,
  Task page, and Task Instance page. Each loads ``/uape/dags/{dag_id}/recommendations-ui`` (same
  auth as the UI session).
* **HTTP / CLI:** same report as ``airflow uape export``:

  GET {api_base}/uape/dags/{dag_id}/recommendations.json

Use your deployment’s API base URL and the same authentication your API server expects
for other HTTP calls (for example bearer tokens in front of the API).

This is a normal Airflow provider distribution (apache_airflow_provider entry point),
not a PLUGINS_FOLDER plugin. It does not change airflow-core.

Implementation documentation: impl/uape/README.rst

Optional background note in this repository: ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md
