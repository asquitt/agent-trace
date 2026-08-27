"""Add durable runtime control requests.

Revision ID: 010
Revises: 009
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_control_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("authorization_expires_at", sa.DateTime(), nullable=True),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_token_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_owner_subject", sa.String(length=255), nullable=True),
        sa.Column("lease_runtime_instance_id", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("acknowledgement_id", sa.String(length=255), nullable=True),
        sa.Column("acknowledgement_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledgement_details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('throttle', 'shutdown')",
            name="ck_runtime_control_requests_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'applied', 'failed')",
            name="ck_runtime_control_requests_status",
        ),
        sa.CheckConstraint(
            "status != 'leased' OR (lease_token_hash IS NOT NULL "
            "AND lease_owner_subject IS NOT NULL "
            "AND lease_runtime_instance_id IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_runtime_control_requests_lease_fields",
        ),
        sa.CheckConstraint(
            "status NOT IN ('applied', 'failed') OR acknowledged_at IS NOT NULL",
            name="ck_runtime_control_requests_terminal_time",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["policy_action_approvals.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["agent_deployments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_event_id"],
            ["budget_policy_events.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["budget_policies.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_control_requests_org_id",
        "runtime_control_requests",
        ["org_id"],
    )
    op.create_index(
        "ix_runtime_control_requests_deployment_id",
        "runtime_control_requests",
        ["deployment_id"],
    )
    op.create_index(
        "ix_runtime_control_requests_session_id",
        "runtime_control_requests",
        ["session_id"],
    )
    op.create_index(
        "ix_runtime_control_requests_policy_id",
        "runtime_control_requests",
        ["policy_id"],
    )
    op.create_index(
        "ix_runtime_control_requests_policy_event_id",
        "runtime_control_requests",
        ["policy_event_id"],
    )
    op.create_index(
        "ix_runtime_control_requests_approval_id",
        "runtime_control_requests",
        ["approval_id"],
    )
    op.create_index(
        "ix_runtime_control_requests_authorization_expires_at",
        "runtime_control_requests",
        ["authorization_expires_at"],
    )
    op.create_index(
        "ix_runtime_control_requests_status",
        "runtime_control_requests",
        ["status"],
    )
    op.create_index(
        "ix_runtime_control_requests_available_at",
        "runtime_control_requests",
        ["available_at"],
    )
    op.create_index(
        "ix_runtime_control_requests_lease_expires_at",
        "runtime_control_requests",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_runtime_control_requests_claim_scope",
        "runtime_control_requests",
        ["org_id", "deployment_id", "session_id", "status", "available_at"],
    )
    op.create_index(
        "ix_runtime_control_requests_lease_expiry",
        "runtime_control_requests",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "uq_runtime_control_requests_idempotency_key",
        "runtime_control_requests",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_runtime_control_requests_idempotency_key",
        table_name="runtime_control_requests",
    )
    op.drop_index(
        "ix_runtime_control_requests_lease_expiry",
        table_name="runtime_control_requests",
    )
    op.drop_index(
        "ix_runtime_control_requests_claim_scope",
        table_name="runtime_control_requests",
    )
    op.drop_index(
        "ix_runtime_control_requests_lease_expires_at",
        table_name="runtime_control_requests",
    )
    op.drop_index(
        "ix_runtime_control_requests_available_at",
        table_name="runtime_control_requests",
    )
    op.drop_index("ix_runtime_control_requests_status", table_name="runtime_control_requests")
    op.drop_index(
        "ix_runtime_control_requests_authorization_expires_at",
        table_name="runtime_control_requests",
    )
    op.drop_index("ix_runtime_control_requests_approval_id", table_name="runtime_control_requests")
    op.drop_index(
        "ix_runtime_control_requests_policy_event_id",
        table_name="runtime_control_requests",
    )
    op.drop_index("ix_runtime_control_requests_policy_id", table_name="runtime_control_requests")
    op.drop_index("ix_runtime_control_requests_session_id", table_name="runtime_control_requests")
    op.drop_index(
        "ix_runtime_control_requests_deployment_id",
        table_name="runtime_control_requests",
    )
    op.drop_index("ix_runtime_control_requests_org_id", table_name="runtime_control_requests")
    op.drop_table("runtime_control_requests")
