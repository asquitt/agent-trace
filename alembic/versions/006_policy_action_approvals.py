"""Add policy action approvals for shutdown safety controls.

Revision ID: 006
Revises: 005
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'policy_approval_status') THEN
                CREATE TYPE policy_approval_status AS ENUM ('pending', 'approved', 'rejected', 'expired');
            END IF;
        END$$;
        """
    )

    policy_action_type_enum = postgresql.ENUM(
        "alert", "throttle", "require_approval", "shutdown",
        name="policy_action_type", create_type=False
    )
    policy_approval_status_enum = postgresql.ENUM(
        "pending", "approved", "rejected", "expired",
        name="policy_approval_status", create_type=False
    )

    op.create_table(
        "policy_action_approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", policy_action_type_enum, nullable=False),
        sa.Column("status", policy_approval_status_enum, nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["budget_policies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_action_approvals_org_id", "policy_action_approvals", ["org_id"])
    op.create_index("ix_policy_action_approvals_policy_id", "policy_action_approvals", ["policy_id"])
    op.create_index(
        "ix_policy_action_approvals_policy_status",
        "policy_action_approvals",
        ["policy_id", "status"],
    )
    op.create_index(
        "ix_policy_action_approvals_org_status",
        "policy_action_approvals",
        ["org_id", "status"],
    )
    op.create_index(
        "ix_policy_action_approvals_requested_at",
        "policy_action_approvals",
        ["requested_at"],
    )
    op.create_index(
        "ix_policy_action_approvals_expires_at",
        "policy_action_approvals",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_policy_action_approvals_expires_at", table_name="policy_action_approvals")
    op.drop_index("ix_policy_action_approvals_requested_at", table_name="policy_action_approvals")
    op.drop_index("ix_policy_action_approvals_org_status", table_name="policy_action_approvals")
    op.drop_index("ix_policy_action_approvals_policy_status", table_name="policy_action_approvals")
    op.drop_index("ix_policy_action_approvals_policy_id", table_name="policy_action_approvals")
    op.drop_index("ix_policy_action_approvals_org_id", table_name="policy_action_approvals")
    op.drop_table("policy_action_approvals")
    op.execute("DROP TYPE IF EXISTS policy_approval_status")
