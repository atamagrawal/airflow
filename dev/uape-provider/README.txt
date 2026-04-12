UAPE (Uncertainty-Aware Parallelization Engine) — advisory CLI with conservative defaults.

Install into the same environment as Airflow (example from repo root):

  uv pip install -e dev/uape-provider

Then:

  airflow uape independence-report <dag_id> [--format text|json]
  airflow uape export <dag_id> [--format json]

After restarting the API server, **external applications** (not the Airflow UI) can fetch
the same report the CLI produces:

  GET {api_base}/uape/dags/{dag_id}/recommendations.json

Use your deployment’s API base URL and the same authentication your API server expects
for other HTTP calls (for example bearer tokens in front of the API).

This is a normal Airflow provider distribution (apache_airflow_provider entry point),
not a PLUGINS_FOLDER plugin. It does not change airflow-core.

Implementation documentation: impl/uape/README.rst

Optional background note in this repository: ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md
