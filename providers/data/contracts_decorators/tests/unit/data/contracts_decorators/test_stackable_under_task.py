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

from airflow.providers.data.contracts_decorators.decorators._stackable_under_task import (
    expect_contract_stats_dict,
    expect_dataset_urn_list,
    expect_non_empty_dataset_urn,
)


def test_expect_contract_stats_dict_accepts_mapping():
    d = {"row_count": 1}
    assert expect_contract_stats_dict(d) is d


def test_expect_contract_stats_dict_rejects_non_dict():
    with pytest.raises(TypeError, match="contract stats dict"):
        expect_contract_stats_dict("nope")


def test_expect_dataset_urn_list_accepts_str_list():
    u = ["urn:a"]
    assert expect_dataset_urn_list(u) is u


def test_expect_dataset_urn_list_rejects_mixed():
    with pytest.raises(TypeError, match="list\\[str\\]"):
        expect_dataset_urn_list(["urn:a", 1])


def test_expect_non_empty_dataset_urn_strips():
    assert expect_non_empty_dataset_urn("  urn:x  ") == "urn:x"


def test_expect_non_empty_dataset_urn_rejects_blank():
    with pytest.raises(TypeError, match="dataset URN"):
        expect_non_empty_dataset_urn("   ")
