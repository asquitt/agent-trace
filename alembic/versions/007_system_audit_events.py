"""Add system audit events for control-plane activity.

Revision ID: 007
Revises: 006
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("actor_roles", postgresql.JSONB(), nullable=True),
        sa.Column("org_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=120), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_audit_events_occurred_at", "system_audit_events", ["occurred_at"])
    op.create_index("ix_system_audit_events_org_id", "system_audit_events", ["org_id"])
    op.create_index("ix_system_audit_events_action", "system_audit_events", ["action"])
    op.create_index("ix_system_audit_events_request_id", "system_audit_events", ["request_id"])
    op.create_index(
        "ix_system_audit_events_org_occurred",
        "system_audit_events",
        ["org_id", "occurred_at"],
    )
    op.create_index(
        "ix_system_audit_events_action_occurred",
        "system_audit_events",
        ["action", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_system_audit_events_action_occurred", table_name="system_audit_events")
    op.drop_index("ix_system_audit_events_org_occurred", table_name="system_audit_events")
    op.drop_index("ix_system_audit_events_request_id", table_name="system_audit_events")
    op.drop_index("ix_system_audit_events_action", table_name="system_audit_events")
    op.drop_index("ix_system_audit_events_org_id", table_name="system_audit_events")
    op.drop_index("ix_system_audit_events_occurred_at", table_name="system_audit_events")
    op.drop_table("system_audit_events")
