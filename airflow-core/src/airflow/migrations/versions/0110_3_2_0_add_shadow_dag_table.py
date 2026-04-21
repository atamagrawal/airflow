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
Add shadow_dag table (AIP-09).

Revision ID: f4a8c9e2b1d7
Revises: 1d6611b6ab7c
Create Date: 2026-04-20 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from airflow.migrations.db_types import StringID, TIMESTAMP

# revision identifiers, used by Alembic.
revision = "f4a8c9e2b1d7"
down_revision = "1d6611b6ab7c"
branch_labels = None
depends_on = None
airflow_version = "3.2.0"


def upgrade():
    """Create shadow_dag table for AIP-09 Shadow DAGs."""
    op.create_table(
        "shadow_dag",
        sa.Column("shadow_id", StringID(), primary_key=True, nullable=False),
        sa.Column("production_dag_id", StringID(), nullable=False),
        sa.Column("candidate_dag_id", StringID(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="registered"),
        sa.Column("ttl_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("divergence_alert_pct", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("notify", sa.String(512), nullable=True),
        sa.Column("created_at", TIMESTAMP(), nullable=False),
        sa.Column("expires_at", TIMESTAMP(), nullable=False),
        sa.Column("last_comparison_json", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_shadow_dag_production_dag_id",
        "shadow_dag",
        ["production_dag_id"],
    )
    op.create_index(
        "idx_shadow_dag_status",
        "shadow_dag",
        ["status"],
    )


def downgrade():
    """Drop shadow_dag table."""
    op.drop_index("idx_shadow_dag_status", table_name="shadow_dag")
    op.drop_index("idx_shadow_dag_production_dag_id", table_name="shadow_dag")
    op.drop_table("shadow_dag")
