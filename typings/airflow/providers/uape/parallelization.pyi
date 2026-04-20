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

"""Type stub for ``airflow.providers.uape.parallelization`` (dev-only UAPE provider)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

THRESHOLD_REMOVE: int
THRESHOLD_UNCERTAIN: int
N_SIMULATIONS: int


@dataclass
class SignalResult:
    name: str
    passed: bool
    weight: int
    explanation: str
    skipped: bool = False


@dataclass
class EdgeScore:
    from_task: str
    to_task: str
    signals: list[SignalResult]
    confidence_score: int
    verdict: str


@dataclass
class DurationProfile:
    task_id: str
    dist_name: str
    params: tuple[Any, ...]
    mean: float
    std: float
    p5: float
    p50: float
    p95: float
    n_samples: int


@dataclass
class SimulationResult:
    mean_savings_seconds: float
    p5_savings_seconds: float
    p50_savings_seconds: float
    p95_savings_seconds: float
    prob_improvement: float
    n_simulations: int


def signal_asset_overlap(task_dict: dict[str, Any], upstream_id: str, downstream_id: str) -> SignalResult: ...
def signal_xcom_analysis(task_dict: dict[str, Any], upstream_id: str, downstream_id: str) -> SignalResult: ...
def signal_timing_correlation(
    dag_id: str,
    upstream_id: str,
    downstream_id: str,
    session: Session | None,
) -> SignalResult: ...
def signal_transitive_reduction(adj: dict[str, set[str]], upstream_id: str, downstream_id: str) -> SignalResult: ...
def score_edge(from_task: str, to_task: str, signals: list[SignalResult]) -> EdgeScore: ...
def fit_duration_profile(task_id: str, durations: list[float]) -> DurationProfile | None: ...
def simulate_savings(
    task_ids: list[str],
    adj_current: dict[str, set[str]],
    adj_proposed: dict[str, set[str]],
    profiles: dict[str, DurationProfile],
    n: int = ...,
) -> SimulationResult | None: ...
def analyze_dag_edges(
    dag: Any,
    *,
    session: Session | None = ...,
    n_simulations: int = ...,
) -> dict[str, Any]: ...
