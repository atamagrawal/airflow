# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""
AIP-07 example — **Producer DAG** (PostgreSQL).

This DAG mirrors a typical warehouse load:

1. ``create_daily_orders_table`` — ensures ``warehouse.daily_orders`` exists
   (see ``sql/001_create_daily_orders.sql``).
2. ``load_daily_orders`` — loads rows for the logical run date into Postgres
   (``sql/002_load_daily_orders.sql``).
3. ``collect_contract_stats`` — reads ``COUNT(*)``, column metadata from
   ``information_schema``, and sets ``data_as_of`` for the contract validator.
4. ``validate_contract`` — validates those stats against the YAML contract.
5. ``publish_contract`` — records lineage / status in the catalog hook.

Connections
~~~~~~~~~~~
* **Postgres** — ``postgres_default`` (or set ``POSTGRES_CONN_ID``): database
  containing ``warehouse.daily_orders``.
* **YAML catalog** — ``data_contract_yaml_default``: maps the dataset URN to
  ``contracts/daily_orders.yaml``.  See the README.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.providers.data.contracts.operators.contract_publish import ContractPublishOperator
from airflow.providers.data.contracts.operators.contract_validate import ContractValidateOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import DAG, task

_EXAMPLE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SQL = os.path.join(_EXAMPLE_ROOT, "sql")

DATASET_URN = "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.daily_orders,PROD)"
CONTRACT_YAML = os.path.join(_EXAMPLE_ROOT, "contracts", "daily_orders.yaml")
CATALOG_CONN_ID = "data_contract_yaml_default"
POSTGRES_CONN_ID = os.environ.get("AIP07_POSTGRES_CONN_ID", "postgres_default")

PG_SCHEMA = "warehouse"
PG_TABLE = "daily_orders"


def _pg_data_type_to_contract_type(pg_data_type: str) -> str:
    """Map ``information_schema.columns.data_type`` to contract validator types."""
    t = (pg_data_type or "").lower()
    if t in ("numeric", "double precision", "real"):
        return "FLOAT"
    return "STRING"


@task
def collect_contract_stats() -> dict:
    """
    Build contract ``stats`` from the live Postgres table.

    Uses ``information_schema`` so the reported schema matches the table DDL.
    """
    hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    fq = f"{PG_SCHEMA}.{PG_TABLE}"
    cols_sql = """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """
    with hook.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {fq}")
            row_count = int(cur.fetchone()[0])
            cur.execute(cols_sql, (PG_SCHEMA, PG_TABLE))
            col_rows = cur.fetchall()

    schema: list[dict] = []
    for column_name, data_type, is_nullable in col_rows:
        schema.append(
            {
                "name": column_name,
                "type": _pg_data_type_to_contract_type(data_type),
                "nullable": str(is_nullable).upper() == "YES",
            }
        )

    return {
        "row_count": row_count,
        "schema": schema,
        "data_as_of": datetime.now(timezone.utc).isoformat(),
    }


with DAG(
    dag_id="aip07_producer",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["aip-07", "data-contracts", "producer", "postgres"],
    doc_md=__doc__,
) as dag:
    create_daily_orders_table = SQLExecuteQueryOperator(
        task_id="create_daily_orders_table",
        conn_id=POSTGRES_CONN_ID,
        sql=os.path.join(_SQL, "001_create_daily_orders.sql"),
        split_statements=True,
        autocommit=True,
    )

    load_daily_orders = SQLExecuteQueryOperator(
        task_id="load_daily_orders",
        conn_id=POSTGRES_CONN_ID,
        sql=os.path.join(_SQL, "002_load_daily_orders.sql"),
        autocommit=True,
    )

    stats = collect_contract_stats()

    validate = ContractValidateOperator(
        task_id="validate_contract",
        catalog_conn_id=CATALOG_CONN_ID,
        dataset_urn=DATASET_URN,
        stats_xcom_task_id="collect_contract_stats",
        contract_yaml_path=CONTRACT_YAML,
        validate_freshness=True,
        validate_completeness=True,
        validate_schema=True,
        validate_sla=False,
        on_schema_violation="fail",
        on_freshness_violation="warn",
        on_completeness_violation="fail",
        report_breach_to_catalog=False,
    )

    publish = ContractPublishOperator(
        task_id="publish_contract",
        catalog_conn_id=CATALOG_CONN_ID,
        dataset_urn=DATASET_URN,
        stats_xcom_task_id="collect_contract_stats",
    )

    create_daily_orders_table >> load_daily_orders >> stats >> validate >> publish
