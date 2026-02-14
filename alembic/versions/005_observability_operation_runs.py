"""Add observability operation run logs.

Revision ID: 005
Revises: 004
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observability_operation_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("run_type", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("detector_summary", postgresql.JSONB(), nullable=True),
        sa.Column("policy_summary", postgresql.JSONB(), nullable=True),
        sa.Column("notification_summary", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_observability_operation_runs_org_started",
        "observability_operation_runs",
        ["org_id", "started_at"],
    )
    op.create_index(
        "ix_observability_operation_runs_run_type_started",
        "observability_operation_runs",
        ["run_type", "started_at"],
    )
    op.create_index(
        "ix_observability_operation_runs_started_at",
        "observability_operation_runs",
        ["started_at"],
    )
    op.create_index(
        "ix_observability_operation_runs_completed_at",
        "observability_operation_runs",
        ["completed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_observability_operation_runs_completed_at",
        table_name="observability_operation_runs",
    )
    op.drop_index(
        "ix_observability_operation_runs_started_at",
        table_name="observability_operation_runs",
    )
    op.drop_index(
        "ix_observability_operation_runs_run_type_started",
        table_name="observability_operation_runs",
    )
    op.drop_index(
        "ix_observability_operation_runs_org_started",
        table_name="observability_operation_runs",
    )
    op.drop_table("observability_operation_runs")
