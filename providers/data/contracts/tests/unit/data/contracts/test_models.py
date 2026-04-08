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

import pytest

from airflow.providers.data.contracts.models.contract import data_contract_from_mapping


def test_data_contract_from_mapping_minimal():
    dc = data_contract_from_mapping(
        {
            "dataset_urn": "urn:test:orders",
            "dataset_name": "orders",
            "version": 2,
            "status": "active",
            "schema": [{"name": "id", "type": "STRING", "nullable": False}],
        }
    )
    assert dc.dataset_urn == "urn:test:orders"
    assert dc.version == 2
    assert dc.status == "ACTIVE"
    assert len(dc.schema) == 1
    assert dc.schema[0].name == "id"
    assert dc.allowed_trigger_users is None


def test_data_contract_from_mapping_allowed_trigger_users():
    dc = data_contract_from_mapping(
        {
            "dataset_urn": "urn:test:orders",
            "dataset_name": "orders",
            "version": 1,
            "status": "ACTIVE",
            "schema": [],
            "allowed_trigger_users": ["alice", "bob"],
        }
    )
    assert dc.allowed_trigger_users == ["alice", "bob"]


def test_data_contract_from_mapping_allowed_trigger_users_invalid():
    with pytest.raises(ValueError, match="allowed_trigger_users"):
        data_contract_from_mapping(
            {
                "dataset_urn": "urn:test:orders",
                "dataset_name": "orders",
                "version": 1,
                "status": "ACTIVE",
                "schema": [],
                "allowed_trigger_users": "not-a-list",
            }
        )
