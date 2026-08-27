"""Add durable notification delivery outbox.

Revision ID: 011
Revises: 010
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("operation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("target_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "target_source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("idempotency_supported", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("claim_owner", sa.String(length=255), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('webhook', 'slack', 'pagerduty')",
            name="ck_notification_deliveries_channel",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry_scheduled', 'accepted', "
            "'dead_letter', 'blocked', 'uncertain', 'cancelled')",
            name="ck_notification_deliveries_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_notification_deliveries_payload_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(target_source_refs) = 'array'",
            name="ck_notification_deliveries_source_refs_array",
        ),
        sa.CheckConstraint(
            "payload_version >= 1",
            name="ck_notification_deliveries_payload_version",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_notification_deliveries_attempt_bounds",
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND claim_owner IS NOT NULL "
            "AND claim_expires_at IS NOT NULL) OR "
            "(status != 'processing' AND claim_owner IS NULL "
            "AND claim_expires_at IS NULL)",
            name="ck_notification_deliveries_claim_fields",
        ),
        sa.CheckConstraint(
            "status NOT IN ('pending', 'retry_scheduled') OR next_attempt_at IS NOT NULL",
            name="ck_notification_deliveries_next_attempt",
        ),
        sa.CheckConstraint(
            "(status IN ('accepted', 'dead_letter', 'blocked', 'uncertain', 'cancelled') "
            "AND terminal_at IS NOT NULL) OR "
            "(status NOT IN ('accepted', 'dead_letter', 'blocked', 'uncertain', 'cancelled') "
            "AND terminal_at IS NULL)",
            name="ck_notification_deliveries_terminal_time",
        ),
        sa.CheckConstraint(
            "(status = 'accepted' AND accepted_at IS NOT NULL) OR "
            "(status != 'accepted' AND accepted_at IS NULL)",
            name="ck_notification_deliveries_accepted_time",
        ),
        sa.CheckConstraint(
            "last_http_status IS NULL OR "
            "(last_http_status >= 100 AND last_http_status <= 599)",
            name="ck_notification_deliveries_http_status",
        ),
        sa.ForeignKeyConstraint(
            ["operation_run_id"],
            ["observability_operation_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_run_id",
            "channel",
            "target_fingerprint",
            name="uq_notification_deliveries_run_target",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_org_id",
        "notification_deliveries",
        ["org_id"],
    )
    op.create_index(
        "ix_notification_deliveries_operation_run_id",
        "notification_deliveries",
        ["operation_run_id"],
    )
    op.create_index(
        "ix_notification_deliveries_status",
        "notification_deliveries",
        ["status"],
    )
    op.create_index(
        "ix_notification_deliveries_claim_expires_at",
        "notification_deliveries",
        ["claim_expires_at"],
    )
    op.create_index(
        "ix_notification_deliveries_next_attempt_at",
        "notification_deliveries",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_notification_deliveries_terminal_at",
        "notification_deliveries",
        ["terminal_at"],
    )
    op.create_index(
        "uq_notification_deliveries_idempotency_key",
        "notification_deliveries",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_notification_deliveries_claim_scope",
        "notification_deliveries",
        ["org_id", "status", "next_attempt_at"],
    )
    op.create_index(
        "ix_notification_deliveries_claim_expiry",
        "notification_deliveries",
        ["status", "claim_expires_at"],
    )

    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_notification_delivery_attempts_number",
        ),
        sa.CheckConstraint(
            "outcome IN ('in_progress', 'accepted', 'retry_scheduled', 'dead_letter', "
            "'blocked', 'uncertain', 'abandoned_retryable', "
            "'worker_lost_uncertain', 'cancelled')",
            name="ck_notification_delivery_attempts_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'in_progress' AND completed_at IS NULL) OR "
            "(outcome != 'in_progress' AND completed_at IS NOT NULL)",
            name="ck_notification_delivery_attempts_completion",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="ck_notification_delivery_attempts_http_status",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["notification_deliveries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_id",
            "attempt_number",
            name="uq_notification_delivery_attempts_sequence",
        ),
    )
    op.create_index(
        "ix_notification_delivery_attempts_org_id",
        "notification_delivery_attempts",
        ["org_id"],
    )
    op.create_index(
        "ix_notification_delivery_attempts_delivery_id",
        "notification_delivery_attempts",
        ["delivery_id"],
    )
    op.create_index(
        "ix_notification_delivery_attempts_started_at",
        "notification_delivery_attempts",
        ["started_at"],
    )
    op.create_index(
        "ix_notification_delivery_attempts_org_started",
        "notification_delivery_attempts",
        ["org_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_delivery_attempts_org_started",
        table_name="notification_delivery_attempts",
    )
    op.drop_index(
        "ix_notification_delivery_attempts_started_at",
        table_name="notification_delivery_attempts",
    )
    op.drop_index(
        "ix_notification_delivery_attempts_delivery_id",
        table_name="notification_delivery_attempts",
    )
    op.drop_index(
        "ix_notification_delivery_attempts_org_id",
        table_name="notification_delivery_attempts",
    )
    op.drop_table("notification_delivery_attempts")

    op.drop_index(
        "ix_notification_deliveries_claim_expiry",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_claim_scope",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "uq_notification_deliveries_idempotency_key",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_terminal_at",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_next_attempt_at",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_claim_expires_at",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_status",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_operation_run_id",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_org_id",
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
