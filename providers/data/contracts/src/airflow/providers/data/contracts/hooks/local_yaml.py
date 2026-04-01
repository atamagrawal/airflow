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

import json
from pathlib import Path

import yaml

from airflow.providers.data.contracts.exceptions import ContractResolutionError
from airflow.providers.data.contracts.hooks.base_catalog import BaseCatalogHook
from airflow.providers.data.contracts.models.contract import DataContract, data_contract_from_mapping


class YamlDataContractHook(BaseCatalogHook):
    """
    Catalog-lite hook that loads contracts from YAML (or JSON) files.

    Connection extras:

    * ``contracts`` — mapping of ``dataset_urn`` → absolute or DAG-relative file path
    * ``contracts_base_dir`` — optional base directory for relative paths in ``contracts``
    """

    conn_name_attr = "catalog_conn_id"
    default_conn_name = "data_contract_yaml_default"
    conn_type = "data_contract_yaml"
    hook_name = "Data contract (YAML)"

    def __init__(self, catalog_conn_id: str = default_conn_name) -> None:
        super().__init__()
        self.catalog_conn_id = catalog_conn_id
        conn = self.get_connection(catalog_conn_id)
        extra = conn.extra_dejson
        self._contracts_map: dict[str, str] = dict(extra.get("contracts") or {})
        base = extra.get("contracts_base_dir") or conn.host
        self._base_dir = Path(base).expanduser() if base else None

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str).expanduser()
        if not p.is_absolute() and self._base_dir is not None:
            p = self._base_dir / p
        return p

    @staticmethod
    def load_contract_from_file(path: str | Path) -> DataContract:
        """Load a :class:`DataContract` from a ``.yaml``/``.yml``/``.json`` file."""
        p = Path(path).expanduser()
        if not p.is_file():
            msg = f"Contract file not found: {p}"
            raise ContractResolutionError(msg)
        raw = p.read_text(encoding="utf-8")
        if p.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(raw)
        elif p.suffix.lower() == ".json":
            data = json.loads(raw)
        else:
            data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            msg = "Contract file must contain a mapping at the top level"
            raise ContractResolutionError(msg)
        return data_contract_from_mapping(data, catalog_url=f"file://{p.resolve()}")

    def get_contract(self, dataset_urn: str) -> DataContract:
        path_key = self._contracts_map.get(dataset_urn)
        if not path_key:
            msg = (
                f"No YAML contract path configured for URN {dataset_urn!r} in connection "
                f"{self.catalog_conn_id!r} (extras.contracts)."
            )
            raise ContractResolutionError(msg)
        return self.load_contract_from_file(self._resolve_path(path_key))

    def get_contract_status(self, dataset_urn: str) -> str:
        return self.get_contract(dataset_urn).status
