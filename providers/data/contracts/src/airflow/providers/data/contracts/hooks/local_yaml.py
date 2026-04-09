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
    Load contracts from YAML or JSON files (catalog-lite / file-backed policy).

    **Without** an Airflow connection, pass ``contract_yaml_path=...`` to read a single contract file
    from disk (``get_contract`` then checks that ``dataset_urn`` matches the file's ``dataset_urn``).

    **With** a ``data_contract_yaml`` connection, ``extras.contracts`` maps URNs to file paths.

    Connection extras:

    * ``contracts`` — mapping of ``dataset_urn`` → absolute or DAG-relative file path
    * ``contracts_base_dir`` — optional base directory for relative paths in ``contracts``
    """

    conn_name_attr = "catalog_conn_id"
    default_conn_name = "data_contract_yaml_default"
    conn_type = "data_contract_yaml"
    hook_name = "Data contract (YAML)"

    def __init__(
        self,
        catalog_conn_id: str | None = None,
        *,
        contract_yaml_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        path_set = contract_yaml_path is not None and str(contract_yaml_path).strip() != ""
        conn_set = catalog_conn_id is not None and str(catalog_conn_id).strip() != ""
        if path_set and conn_set:
            msg = "Set only one of catalog_conn_id or contract_yaml_path on YamlDataContractHook"
            raise ValueError(msg)
        if path_set:
            self.catalog_conn_id = ""
            self._file_only_path = Path(contract_yaml_path).expanduser()
            self._contracts_map = {}
            self._base_dir = None
            return
        cid = catalog_conn_id if conn_set else self.default_conn_name
        self.catalog_conn_id = cid
        self._file_only_path = None
        conn = self.get_connection(cid)
        extra = conn.extra_dejson
        self._contracts_map = dict(extra.get("contracts") or {})
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
        if self._file_only_path is not None:
            contract = self.load_contract_from_file(self._file_only_path)
            if contract.dataset_urn != dataset_urn:
                msg = (
                    f"Contract file defines dataset_urn {contract.dataset_urn!r}, "
                    f"but {dataset_urn!r} was requested."
                )
                raise ContractResolutionError(msg)
            return contract
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
