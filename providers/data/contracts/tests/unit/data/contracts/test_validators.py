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

from datetime import datetime, timedelta, timezone

from airflow.providers.data.contracts.models.contract import DataContract, SchemaField
from airflow.providers.data.contracts.validators.contract_validators import (
    validate_completeness,
    validate_freshness,
    validate_schema,
)


def _contract(**kwargs) -> DataContract:
    base = {
        "contract_id": "c1",
        "dataset_urn": "urn:x",
        "dataset_name": "x",
        "version": 1,
        "status": "ACTIVE",
        "schema": [],
    }
    base.update(kwargs)
    return DataContract(**base)


def test_validate_schema_missing_column():
    c = _contract(
        schema=[SchemaField(name="a", type="STRING", nullable=False)],
    )
    stats = {"schema": []}
    v = validate_schema(c, stats)
    assert any(x.violation_type == "SCHEMA_MISMATCH" for x in v)


def test_validate_completeness_min_rows():
    c = _contract(schema=[], min_row_count=100)
    v = validate_completeness(c, {"row_count": 1})
    assert v
    assert v[0].violation_type == "COMPLETENESS"


def test_validate_freshness_too_old():
    c = _contract(schema=[], freshness_max_age_minutes=60)
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    v = validate_freshness(c, {"data_as_of": old.isoformat()})
    assert v
    assert v[0].violation_type == "FRESHNESS"
