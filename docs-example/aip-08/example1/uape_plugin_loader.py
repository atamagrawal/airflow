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
Bridge loader for the UAPE ``AirflowPlugin``.

Airflow always scans ``$AIRFLOW_HOME/plugins/*.py`` (see ``[core] plugins_folder``) before
distribution entry points. Some Docker / install layouts do not surface ``airflow.plugins``
entry points the same way as a local ``uv pip install``; copying this file into the plugins
folder forces the real plugin class to be discovered so ``/uape`` and UI tabs register.
"""

from __future__ import annotations

from airflow.providers.uape.plugins.uape_plugin import UapeAdvisoryPlugin

__all__ = ["UapeAdvisoryPlugin"]
