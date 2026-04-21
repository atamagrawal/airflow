#
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
"""Shadow DAG ORM model — AIP-09."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from airflow._shared.timezones import timezone
from airflow.models.base import Base, StringID
from airflow.utils.sqlalchemy import UtcDateTime

if TYPE_CHECKING:
    pass


class ShadowDagStatus(str, Enum):
    """Lifecycle phases for a Shadow DAG (AIP-09 §5)."""

    REGISTERED = "registered"
    ACTIVE = "active"
    REVIEW = "review"
    PROMOTED = "promoted"
    DISCARDED = "discarded"
    CLEANED_UP = "cleaned_up"

    #: Valid transitions: source → {allowed targets}
    _TRANSITIONS: dict[str, set[str]] = {}  # populated after class body

    def can_transition_to(self, target: ShadowDagStatus) -> bool:
        """Return True if this status may transition to *target*."""
        return target in _VALID_TRANSITIONS.get(self, set())


_VALID_TRANSITIONS: dict[ShadowDagStatus, set[ShadowDagStatus]] = {
    ShadowDagStatus.REGISTERED: {ShadowDagStatus.ACTIVE, ShadowDagStatus.DISCARDED},
    ShadowDagStatus.ACTIVE: {ShadowDagStatus.REVIEW, ShadowDagStatus.DISCARDED},
    ShadowDagStatus.REVIEW: {ShadowDagStatus.PROMOTED, ShadowDagStatus.DISCARDED},
    ShadowDagStatus.PROMOTED: {ShadowDagStatus.CLEANED_UP},
    ShadowDagStatus.DISCARDED: {ShadowDagStatus.CLEANED_UP},
    ShadowDagStatus.CLEANED_UP: set(),
}


class InvalidShadowTransition(ValueError):
    """Raised when a requested lifecycle transition is not permitted."""

    def __init__(self, current: ShadowDagStatus, target: ShadowDagStatus) -> None:
        super().__init__(
            f"Cannot transition Shadow DAG from '{current.value}' to '{target.value}'. "
            f"Allowed targets: {[s.value for s in _VALID_TRANSITIONS.get(current, set())]}"
        )


class ShadowDagNotFound(LookupError):
    """Raised when a requested Shadow DAG record does not exist."""

    def __init__(self, shadow_id: str) -> None:
        super().__init__(f"Shadow DAG '{shadow_id}' not found.")


class ShadowDag(Base):
    """
    Metadata record for a Shadow DAG experiment (AIP-09).

    One row per shadow experiment.  Multiple shadow runs (DagRun rows for the
    *candidate* DAG) are linked back to this record via ``shadow_id`` stored in
    ``DagRun.conf["__shadow_id__"]``.
    """

    __tablename__ = "shadow_dag"

    shadow_id: Mapped[str] = mapped_column(StringID(), primary_key=True)
    production_dag_id: Mapped[str] = mapped_column(StringID(), nullable=False)
    candidate_dag_id: Mapped[str] = mapped_column(StringID(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ShadowDagStatus.REGISTERED.value,
    )
    ttl_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    divergence_alert_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    notify: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=timezone.utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_comparison_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    __table_args__ = (
        Index("idx_shadow_dag_production_dag_id", "production_dag_id"),
        Index("idx_shadow_dag_status", "status"),
    )

    @property
    def status_enum(self) -> ShadowDagStatus:
        return ShadowDagStatus(self.status)

    @status_enum.setter
    def status_enum(self, value: ShadowDagStatus) -> None:
        self.status = value.value

    def is_terminal(self) -> bool:
        """Return True when no further lifecycle transitions are possible."""
        return self.status_enum in (ShadowDagStatus.PROMOTED, ShadowDagStatus.CLEANED_UP)

    def is_active(self) -> bool:
        return self.status == ShadowDagStatus.ACTIVE.value

    def __repr__(self) -> str:
        return f"<ShadowDag shadow_id={self.shadow_id!r} status={self.status!r}>"
