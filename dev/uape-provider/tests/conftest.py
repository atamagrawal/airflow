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
Test environment bootstrap for the UAPE dev provider.

In the development workspace, some provider packages are registered via
entry-points but are not installed (e.g. vespa) or have metadata mismatches
(e.g. apache-livy).  Airflow's provider discovery normally aborts with an
ImportError or ValueError in those cases.

We work around this by injecting a stub for
``airflow._shared.providers_discovery.providers_discovery`` into sys.modules
BEFORE any airflow module is imported.  When the real providers_manager
imports ``discover_all_providers_from_packages`` at runtime it finds the no-op
stub, so Airflow initialises without trying to load every provider package.

This must happen at conftest MODULE-LOAD time (not inside a fixture) so it
fires before pytest imports the test module (which would trigger
airflow/__init__.py).
"""

from __future__ import annotations

import os
import sys
from types import ModuleType

# ---------------------------------------------------------------------------
# 1. Environment flag — must be set before airflow is first imported
# ---------------------------------------------------------------------------
os.environ.setdefault("AIRFLOW__CORE__UNIT_TEST_MODE", "True")

# ---------------------------------------------------------------------------
# 2. Stub the provider-discovery module with a no-op implementation.
#
# airflow._shared.providers_discovery.providers_discovery is imported by
# providers_manager.py as:
#
#   from airflow._shared.providers_discovery.providers_discovery import
#       discover_all_providers_from_packages
#
# By placing a stub in sys.modules now (before airflow/__init__.py runs) the
# real file is never loaded and the broken entry-points are never visited.
# ---------------------------------------------------------------------------

_DISCOVERY_MODULE = "airflow._shared.providers_discovery.providers_discovery"


def _noop_discover_all_providers_from_packages(provider_dict, schema_validator):
    """No-op stub: skip provider discovery so broken entry-points don't abort tests."""


if _DISCOVERY_MODULE not in sys.modules:
    _stub = ModuleType(_DISCOVERY_MODULE)
    _stub.discover_all_providers_from_packages = _noop_discover_all_providers_from_packages  # type: ignore[attr-defined]
    # Stub all names re-exported by airflow._shared.providers_discovery.__init__
    # so that "from .providers_discovery import ..." in __init__.py succeeds.
    _stub.KNOWN_UNHANDLED_OPTIONAL_FEATURE_ERRORS = ()  # type: ignore[attr-defined]
    _stub.HookClassProvider = object  # type: ignore[attr-defined]
    _stub.HookInfo = object  # type: ignore[attr-defined]
    _stub.LazyDictWithCache = dict  # type: ignore[attr-defined]
    _stub.PluginInfo = object  # type: ignore[attr-defined]
    _stub.ProviderInfo = object  # type: ignore[attr-defined]
    _stub._check_builtin_provider_prefix = lambda *a, **kw: None  # type: ignore[attr-defined]
    _stub._create_provider_info_schema_validator = lambda *a, **kw: None  # type: ignore[attr-defined]
    _stub.log_import_warning = lambda *a, **kw: None  # type: ignore[attr-defined]
    _stub.log_optional_feature_disabled = lambda *a, **kw: None  # type: ignore[attr-defined]
    _stub.provider_info_cache = lambda *a, **kw: lambda f: f  # identity decorator stub
    sys.modules[_DISCOVERY_MODULE] = _stub
