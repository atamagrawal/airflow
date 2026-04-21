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
``@shadow_dag`` decorator — AIP-09 §6.

Marks a DAG as a shadow candidate inline in the DAG file.  The DAG Processor
detects the ``__shadow_config__`` attribute on parsed DAGs and registers a
``ShadowDag`` record in the metadata DB.

Usage::

    from airflow.sdk import dag, shadow_dag

    @shadow_dag(
        shadows="etl.orders_daily",
        ttl="7d",
        divergence_alert=0.05,
        notify="data-eng-oncall@example.com",
    )
    @dag(schedule="@daily", catchup=False)
    def orders_daily_v2():
        ...
"""

from __future__ import annotations

import functools
import json
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)

#: Attribute name written onto the decorated callable.  The DAG Processor reads
#: this attribute after parsing a DAG file and auto-registers the shadow.
SHADOW_CONFIG_ATTR = "__shadow_config__"

#: Prefix used to embed shadow config as a DAG tag so that
#: the serialized form carries it through to ``collection.py``.
SHADOW_TAG_PREFIX = "__shadow__:"


def _encode_shadow_tag(config: dict) -> str:
    """Encode shadow config as a DAG tag string."""
    return SHADOW_TAG_PREFIX + json.dumps(config, separators=(",", ":"))


def decode_shadow_tag(tag: str) -> dict | None:
    """Decode a shadow tag string back to a config dict.  Returns None if not a shadow tag."""
    if not tag.startswith(SHADOW_TAG_PREFIX):
        return None
    try:
        return json.loads(tag[len(SHADOW_TAG_PREFIX):])
    except (json.JSONDecodeError, ValueError):
        return None


def shadow_dag(
    shadows: str,
    ttl: str = "7d",
    divergence_alert: float = 0.05,
    notify: str | None = None,
) -> Callable[[F], F]:
    """
    Mark a DAG function as a shadow candidate for an existing production DAG.

    The decorator:
    1. Marks the function with ``__shadow_config__`` for introspection.
    2. Wraps the ``@dag`` factory so that when the DAG is instantiated it
       receives a special ``__shadow__:<json>`` tag.  The DAG Processor
       detects this tag in the serialized form and auto-registers a
       ``ShadowDag`` record in the metadata DB.

    :param shadows: DAG ID of the production DAG this shadow mirrors.
    :param ttl: How long to run the shadow (e.g. ``"7d"``).  Max 14 days.
    :param divergence_alert: Fractional row-count divergence threshold that
        triggers an alert (default ``0.05`` = 5 %).
    :param notify: Optional e-mail address for divergence notifications.
    """
    if not shadows:
        raise ValueError("'shadows' must be a non-empty production DAG ID.")

    def decorator(dag_func: F) -> F:
        config = {
            "shadows": shadows,
            "ttl": ttl,
            "divergence_alert": divergence_alert,
            "notify": notify,
        }
        shadow_tag = _encode_shadow_tag(config)

        setattr(dag_func, SHADOW_CONFIG_ATTR, config)

        @functools.wraps(dag_func)
        def wrapper(*args, **kwargs):
            dag_obj = dag_func(*args, **kwargs)
            # Inject shadow tag so the serialized form carries the config.
            if hasattr(dag_obj, "tags"):
                dag_obj.tags.add(shadow_tag)
            return dag_obj

        setattr(wrapper, SHADOW_CONFIG_ATTR, config)
        return wrapper  # type: ignore[return-value]

    return decorator
