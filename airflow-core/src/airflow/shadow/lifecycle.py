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
"""
Shadow DAG lifecycle service — AIP-09 §5.

``ShadowDagService`` is the single entry-point for all create / read / update
operations on ``ShadowDag`` records and lifecycle transitions.  It emits
standard Airflow ``Stats`` metrics for every significant event.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from airflow._shared.observability.metrics.stats import Stats
from airflow._shared.timezones import timezone
from airflow.models.shadow_dag import (
    InvalidShadowTransition,
    ShadowDag,
    ShadowDagNotFound,
    ShadowDagStatus,
    _VALID_TRANSITIONS,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from airflow.shadow.comparison import ComparisonReport

log = logging.getLogger(__name__)

_MAX_TTL_DAYS = 14
_MIN_TTL_DAYS = 1


def _parse_ttl(ttl: str) -> int:
    """
    Parse a human-friendly TTL string (e.g. ``"7d"``, ``"3"``) into days.

    :raises ValueError: When the string cannot be parsed or is out of range.
    """
    raw = ttl.rstrip("d").strip()
    try:
        days = int(raw)
    except ValueError:
        raise ValueError(f"Invalid TTL value '{ttl}'. Use the format '7d' or an integer number of days.")
    if not (_MIN_TTL_DAYS <= days <= _MAX_TTL_DAYS):
        raise ValueError(
            f"TTL {days}d is outside allowed range [{_MIN_TTL_DAYS}, {_MAX_TTL_DAYS}] days. "
            "Extend beyond 14 days requires explicit justification."
        )
    return days


def _build_shadow_id(production_dag_id: str) -> str:
    today = timezone.utcnow().strftime("%Y%m%d")
    safe_dag = production_dag_id.replace(".", "_").replace("/", "_")[:40]
    return f"shd_{safe_dag}_{today}"


class ShadowDagService:
    """
    Manages the full lifecycle of Shadow DAG experiment records.

    All public methods accept a SQLAlchemy ``session`` parameter and follow
    the project convention of not calling ``session.commit()`` — the caller is
    responsible for committing.
    """

    def create(
        self,
        *,
        production_dag_id: str,
        candidate_dag_id: str,
        ttl: str = "7d",
        divergence_alert: float = 0.05,
        notify: str | None = None,
        session: Session,
    ) -> ShadowDag:
        """
        Register a new Shadow DAG experiment.

        :param production_dag_id: DAG ID of the production DAG being shadowed.
        :param candidate_dag_id: DAG ID of the candidate/experimental DAG.
        :param ttl: Time-to-live string, e.g. ``"7d"``.  Max 14d.
        :param divergence_alert: Fractional threshold (0–1) at which to alert.
        :param notify: Optional e-mail address for divergence alerts.
        :param session: SQLAlchemy session (no commit).
        :returns: Newly created ``ShadowDag`` instance (in session but uncommitted).
        :raises ValueError: When TTL is invalid.
        """
        ttl_days = _parse_ttl(ttl)
        now = timezone.utcnow()
        shadow_id = _build_shadow_id(production_dag_id)

        existing = session.get(ShadowDag, shadow_id)
        if existing is not None:
            log.info("Shadow DAG '%s' already exists, returning existing record.", shadow_id)
            return existing

        shadow = ShadowDag(
            shadow_id=shadow_id,
            production_dag_id=production_dag_id,
            candidate_dag_id=candidate_dag_id,
            status=ShadowDagStatus.REGISTERED.value,
            ttl_days=ttl_days,
            divergence_alert_pct=divergence_alert,
            notify=notify,
            created_at=now,
            expires_at=now + timedelta(days=ttl_days),
        )
        session.add(shadow)
        Stats.incr("shadow.created", tags={"dag_id": production_dag_id})
        log.info(
            "Shadow DAG '%s' registered (production=%s, candidate=%s, ttl=%dd).",
            shadow_id,
            production_dag_id,
            candidate_dag_id,
            ttl_days,
        )
        return shadow

    def get(self, shadow_id: str, *, session: Session) -> ShadowDag:
        """
        Fetch a Shadow DAG record by ID.

        :raises ShadowDagNotFound: When no record exists for *shadow_id*.
        """
        shadow = session.get(ShadowDag, shadow_id)
        if shadow is None:
            raise ShadowDagNotFound(shadow_id)
        return shadow

    def list(
        self,
        *,
        status: ShadowDagStatus | None = None,
        production_dag_id: str | None = None,
        session: Session,
    ) -> list[ShadowDag]:
        """List Shadow DAG records, optionally filtered by status or production_dag_id."""
        stmt = select(ShadowDag)
        if status is not None:
            stmt = stmt.where(ShadowDag.status == status.value)
        if production_dag_id is not None:
            stmt = stmt.where(ShadowDag.production_dag_id == production_dag_id)
        stmt = stmt.order_by(ShadowDag.created_at.desc())
        return list(session.scalars(stmt))

    def transition(
        self,
        shadow_id: str,
        *,
        new_status: ShadowDagStatus,
        session: Session,
    ) -> ShadowDag:
        """
        Move a Shadow DAG to *new_status*.

        :raises ShadowDagNotFound: When *shadow_id* is unknown.
        :raises InvalidShadowTransition: When the transition is not permitted.
        """
        shadow = self.get(shadow_id, session=session)
        current = shadow.status_enum
        if not current.can_transition_to(new_status):
            raise InvalidShadowTransition(current, new_status)
        shadow.status = new_status.value
        Stats.incr(
            "shadow.transition",
            tags={"dag_id": shadow.production_dag_id, "status": new_status.value},
        )
        log.info("Shadow DAG '%s' transitioned: %s → %s", shadow_id, current.value, new_status.value)
        return shadow

    def promote(self, shadow_id: str, *, session: Session) -> ShadowDag:
        """
        Promote a Shadow DAG in REVIEW status to PROMOTED.

        Prints deployment guidance to stdout (future: opens a PR/ticket).
        """
        shadow = self.transition(shadow_id, new_status=ShadowDagStatus.PROMOTED, session=session)
        log.info(
            "Shadow DAG '%s' promoted.  Deploy '%s' to production and archive shadow data.",
            shadow_id,
            shadow.candidate_dag_id,
        )
        return shadow

    def discard(self, shadow_id: str, *, session: Session) -> ShadowDag:
        """Move a Shadow DAG to DISCARDED, then queue it for cleanup."""
        shadow = self.transition(shadow_id, new_status=ShadowDagStatus.DISCARDED, session=session)
        return shadow

    def record_comparison(
        self,
        shadow_id: str,
        *,
        report: ComparisonReport,
        session: Session,
    ) -> None:
        """
        Persist the latest ``ComparisonReport`` on the ``ShadowDag`` record.

        Also emits verdict and row-delta metrics.
        """
        shadow = self.get(shadow_id, session=session)
        shadow.last_comparison_json = json.dumps(report.to_dict())

        Stats.incr(
            "shadow.run.verdict",
            tags={"dag_id": shadow.production_dag_id, "shadow_id": shadow_id, "verdict": report.verdict.value},
        )
        Stats.gauge(
            "shadow.row_delta_pct",
            value=abs(report.row_count_delta_pct),
            tags={"dag_id": shadow.production_dag_id, "shadow_id": shadow_id},
        )

        # Auto-transition to REVIEW when the shadow reaches expiry
        if shadow.status == ShadowDagStatus.ACTIVE.value:
            if timezone.utcnow() >= shadow.expires_at:
                self.transition(shadow_id, new_status=ShadowDagStatus.REVIEW, session=session)

    def cleanup_expired(self, *, session: Session) -> int:
        """
        Transition REVIEW/DISCARDED shadows past their cleanup window to CLEANED_UP.

        Called by the scheduler heartbeat to ensure expired shadows are
        eventually archived.

        :returns: Number of records transitioned.
        """
        now = timezone.utcnow()
        terminal_candidate_statuses = {ShadowDagStatus.REVIEW.value, ShadowDagStatus.DISCARDED.value}
        stmt = select(ShadowDag).where(
            ShadowDag.status.in_(terminal_candidate_statuses),
            ShadowDag.expires_at <= now,
        )
        expired = list(session.scalars(stmt))
        for shadow in expired:
            shadow.status = ShadowDagStatus.CLEANED_UP.value
            log.info("Shadow DAG '%s' cleaned up (expired_at=%s).", shadow.shadow_id, shadow.expires_at)
        if expired:
            Stats.gauge("shadow.cleaned_up_count", value=len(expired))
        return len(expired)
