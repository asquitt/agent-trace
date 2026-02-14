"""Add observability dimensions to trace tables.

Revision ID: 004
Revises: 003
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_traces", sa.Column("org_id", sa.String(length=255), nullable=True))
    op.add_column(
        "ai_traces",
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ai_traces",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("ai_traces", sa.Column("agent_id", sa.String(length=255), nullable=True))

    op.add_column(
        "ai_trace_spans",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_ai_traces_deployment_id_agent_deployments",
        "ai_traces",
        "agent_deployments",
        ["deployment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ai_traces_session_id_agent_sessions",
        "ai_traces",
        "agent_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ai_trace_spans_session_id_agent_sessions",
        "ai_trace_spans",
        "agent_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_ai_traces_org_started", "ai_traces", ["org_id", "started_at"])
    op.create_index(
        "ix_ai_traces_deployment_started",
        "ai_traces",
        ["deployment_id", "started_at"],
    )
    op.create_index("ix_ai_traces_session_id", "ai_traces", ["session_id"])
    op.create_index("ix_ai_traces_agent_id", "ai_traces", ["agent_id"])
    op.create_index(
        "ix_ai_trace_spans_session_started",
        "ai_trace_spans",
        ["session_id", "started_at"],
    )

    # Best-effort backfill from existing metadata fields.
    op.execute("""
        UPDATE ai_traces
        SET org_id = metadata->>'org_id'
        WHERE org_id IS NULL
          AND metadata IS NOT NULL
          AND metadata ? 'org_id';
    """)
    op.execute("""
        UPDATE ai_traces
        SET agent_id = metadata->>'agent_id'
        WHERE agent_id IS NULL
          AND metadata IS NOT NULL
          AND metadata ? 'agent_id';
    """)
    op.execute("""
        UPDATE ai_traces
        SET deployment_id = (metadata->>'deployment_id')::uuid
        WHERE deployment_id IS NULL
          AND metadata IS NOT NULL
          AND metadata ? 'deployment_id'
          AND (metadata->>'deployment_id') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          AND EXISTS (
              SELECT 1
              FROM agent_deployments d
              WHERE d.id = (metadata->>'deployment_id')::uuid
          );
    """)
    op.execute("""
        UPDATE ai_traces
        SET session_id = (metadata->>'session_id')::uuid
        WHERE session_id IS NULL
          AND metadata IS NOT NULL
          AND metadata ? 'session_id'
          AND (metadata->>'session_id') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          AND EXISTS (
              SELECT 1
              FROM agent_sessions s
              WHERE s.id = (metadata->>'session_id')::uuid
          );
    """)

    # Ensure spans inherit session dimension from parent traces when available.
    op.execute("""
        UPDATE ai_trace_spans s
        SET session_id = t.session_id
        FROM ai_traces t
        WHERE s.trace_id = t.id
          AND s.session_id IS NULL
          AND t.session_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.drop_index("ix_ai_trace_spans_session_started", table_name="ai_trace_spans")
    op.drop_index("ix_ai_traces_agent_id", table_name="ai_traces")
    op.drop_index("ix_ai_traces_session_id", table_name="ai_traces")
    op.drop_index("ix_ai_traces_deployment_started", table_name="ai_traces")
    op.drop_index("ix_ai_traces_org_started", table_name="ai_traces")

    op.drop_constraint(
        "fk_ai_trace_spans_session_id_agent_sessions",
        "ai_trace_spans",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_traces_session_id_agent_sessions",
        "ai_traces",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_traces_deployment_id_agent_deployments",
        "ai_traces",
        type_="foreignkey",
    )

    op.drop_column("ai_trace_spans", "session_id")
    op.drop_column("ai_traces", "agent_id")
    op.drop_column("ai_traces", "session_id")
    op.drop_column("ai_traces", "deployment_id")
    op.drop_column("ai_traces", "org_id")
