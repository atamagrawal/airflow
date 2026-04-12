UAPE (Uncertainty-Aware Parallelization Engine) — advisory CLI with conservative defaults.

Install into the same environment as Airflow (example from repo root):

  uv pip install -e dev/uape-provider

Then:

  airflow uape independence-report <dag_id> [--format text|json]
  airflow uape export <dag_id> [--format json]

This is a normal Airflow provider distribution (apache_airflow_provider entry point),
not a PLUGINS_FOLDER plugin. It does not change airflow-core.

Optional background note in this repository: ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md
