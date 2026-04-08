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
from __future__ import annotations

from unittest.mock import MagicMock, patch

from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook


def test_load_contract_from_file(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        """
dataset_urn: urn:local:orders
dataset_name: orders
version: 1
status: ACTIVE
schema:
  - name: id
    type: STRING
    nullable: false
min_row_count: 1
allowed_trigger_users:
  - ops
""",
        encoding="utf-8",
    )
    dc = YamlDataContractHook.load_contract_from_file(p)
    assert dc.dataset_name == "orders"
    assert dc.schema[0].name == "id"
    assert dc.allowed_trigger_users == ["ops"]


@patch.object(YamlDataContractHook, "get_connection")
def test_get_contract_uses_extras_map(mock_get_conn, tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "dataset_urn: urn:local:orders\ndataset_name: orders\nversion: 1\nstatus: ACTIVE\nschema: []\n",
        encoding="utf-8",
    )
    mock_get_conn.return_value = MagicMock(
        extra_dejson={"contracts": {"urn:local:orders": str(p)}},
        host=None,
    )
    hook = YamlDataContractHook(catalog_conn_id="yaml_default")
    dc = hook.get_contract("urn:local:orders")
    assert dc.dataset_urn == "urn:local:orders"
