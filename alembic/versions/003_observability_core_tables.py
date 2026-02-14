"""Add observability core tables.

Revision ID: 003
Revises: 002
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    deployment_environment_enum = postgresql.ENUM(
        "dev", "staging", "prod", name="deployment_environment", create_type=False
    )
    session_status_enum = postgresql.ENUM(
        "active", "idle", "completed", "failed", "terminated", name="session_status", create_type=False
    )
    action_type_enum = postgresql.ENUM(
        "llm_call", "tool_call", "delegation", "memory_read",
        "memory_write", "network_call", "policy_action",
        name="action_type", create_type=False
    )
    memory_consistency_state_enum = postgresql.ENUM(
        "consistent", "diverged", "unknown", name="memory_consistency_state", create_type=False
    )
    budget_scope_type_enum = postgresql.ENUM(
        "org", "deployment", "agent", name="budget_scope_type", create_type=False
    )
    budget_period_type_enum = postgresql.ENUM(
        "hour", "day", "month", name="budget_period_type", create_type=False
    )
    policy_action_type_enum = postgresql.ENUM(
        "alert", "throttle", "require_approval", "shutdown",
        name="policy_action_type", create_type=False
    )
    policy_status_enum = postgresql.ENUM(
        "active", "paused", name="policy_status", create_type=False
    )
    anomaly_type_enum = postgresql.ENUM(
        "api_spike", "unusual_resource_access", "memory_divergence",
        "cost_spike", "delegation_loop",
        name="anomaly_type", create_type=False
    )
    anomaly_severity_enum = postgresql.ENUM(
        "low", "medium", "high", "critical", name="anomaly_severity", create_type=False
    )
    anomaly_status_enum = postgresql.ENUM(
        "open", "acknowledged", "resolved", name="anomaly_status", create_type=False
    )
    delegation_status_enum = postgresql.ENUM(
        "requested", "accepted", "rejected", "completed", "failed",
        name="delegation_status", create_type=False
    )

    op.create_table(
        "agent_deployments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("deployment_key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("environment", deployment_environment_enum, nullable=False),
        sa.Column("runtime", sa.String(length=100), nullable=False),
        sa.Column("runtime_version", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "deployment_key", name="uq_agent_deployments_org_key"),
    )
    op.create_index("ix_agent_deployments_org_id", "agent_deployments", ["org_id"])
    op.create_index("ix_agent_deployments_environment", "agent_deployments", ["environment"])
    op.create_index("ix_agent_deployments_is_active", "agent_deployments", ["is_active"])

    op.create_table(
        "agent_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("agent_instance_id", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("root_trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workload_type", sa.String(length=100), nullable=True),
        sa.Column("status", session_status_enum, nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["agent_deployments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["root_trace_id"], ["ai_traces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_session_id"], ["agent_sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_sessions_deployment_id", "agent_sessions", ["deployment_id"])
    op.create_index("ix_agent_sessions_correlation_id", "agent_sessions", ["correlation_id"])
    op.create_index("ix_agent_sessions_started_at", "agent_sessions", ["started_at"])
    op.create_index(
        "ix_agent_sessions_deployment_status",
        "agent_sessions",
        ["deployment_id", "status"],
    )
    op.create_index(
        "ix_agent_sessions_agent_status",
        "agent_sessions",
        ["agent_id", "status"],
    )

    op.create_table(
        "agent_actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("span_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", action_type_enum, nullable=False),
        sa.Column("action_name", sa.String(length=255), nullable=False),
        sa.Column("resource", sa.String(length=2048), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["ai_traces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["span_id"], ["ai_trace_spans.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_actions_session_id", "agent_actions", ["session_id"])
    op.create_index("ix_agent_actions_trace_id", "agent_actions", ["trace_id"])
    op.create_index("ix_agent_actions_occurred_at", "agent_actions", ["occurred_at"])
    op.create_index(
        "ix_agent_actions_session_occurred",
        "agent_actions",
        ["session_id", "occurred_at"],
    )
    op.create_index(
        "uq_agent_actions_session_client_event_id",
        "agent_actions",
        ["session_id", "client_event_id"],
        unique=True,
        postgresql_where=sa.text("client_event_id IS NOT NULL"),
    )

    op.create_table(
        "delegation_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", delegation_status_enum, nullable=False, server_default="requested"),
        sa.Column("delegation_reason", sa.Text(), nullable=True),
        sa.Column("requested_capabilities", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["ai_traces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_session_id"], ["agent_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["child_session_id"], ["agent_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_action_id"], ["agent_actions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delegation_edges_trace_id", "delegation_edges", ["trace_id"])
    op.create_index(
        "ix_delegation_edges_parent_started",
        "delegation_edges",
        ["parent_session_id", "started_at"],
    )
    op.create_index(
        "ix_delegation_edges_child_started",
        "delegation_edges",
        ["child_session_id", "started_at"],
    )

    op.create_table(
        "memory_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_namespace", sa.String(length=255), nullable=False),
        sa.Column("memory_key", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=255), nullable=True),
        sa.Column("version_vector", postgresql.JSONB(), nullable=True),
        sa.Column("source_sequence", sa.BigInteger(), nullable=True),
        sa.Column("consistency_state", memory_consistency_state_enum, nullable=False),
        sa.Column("divergence_score", sa.Float(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["ai_traces.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_snapshots_session_id", "memory_snapshots", ["session_id"])
    op.create_index("ix_memory_snapshots_observed_at", "memory_snapshots", ["observed_at"])
    op.create_index(
        "ix_memory_snapshots_session_observed",
        "memory_snapshots",
        ["session_id", "observed_at"],
    )
    op.create_index(
        "ix_memory_snapshots_namespace_key_observed",
        "memory_snapshots",
        ["memory_namespace", "memory_key", "observed_at"],
    )
    op.create_index(
        "ix_memory_snapshots_state_observed",
        "memory_snapshots",
        ["consistency_state", "observed_at"],
    )

    op.create_table(
        "budget_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("policy_name", sa.String(length=255), nullable=False),
        sa.Column("scope_type", budget_scope_type_enum, nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", sa.String(length=255), nullable=True),
        sa.Column("period_type", budget_period_type_enum, nullable=False),
        sa.Column("max_cost_usd", sa.Float(), nullable=True),
        sa.Column("max_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_actions", sa.BigInteger(), nullable=True),
        sa.Column("max_session_minutes", sa.Integer(), nullable=True),
        sa.Column("action_on_breach", policy_action_type_enum, nullable=False),
        sa.Column("throttle_rate", sa.Integer(), nullable=True),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=True),
        sa.Column("notification_targets", postgresql.JSONB(), nullable=True),
        sa.Column("status", policy_status_enum, nullable=False, server_default="active"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["agent_deployments.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_budget_policies_org_status", "budget_policies", ["org_id", "status"])
    op.create_index(
        "ix_budget_policies_deployment_status",
        "budget_policies",
        ["deployment_id", "status"],
    )
    op.create_index(
        "ix_budget_policies_agent_status",
        "budget_policies",
        ["agent_id", "status"],
    )

    op.create_table(
        "anomaly_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("anomaly_type", anomaly_type_enum, nullable=False),
        sa.Column("severity", anomaly_severity_enum, nullable=False),
        sa.Column("status", anomaly_status_enum, nullable=False, server_default="open"),
        sa.Column("detector_name", sa.String(length=100), nullable=False),
        sa.Column("baseline_value", sa.Float(), nullable=True),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("deviation_ratio", sa.Float(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["agent_deployments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["ai_traces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["agent_actions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_anomaly_events_detected_at", "anomaly_events", ["detected_at"])
    op.create_index(
        "ix_anomaly_events_status_severity_detected",
        "anomaly_events",
        ["status", "severity", "detected_at"],
    )
    op.create_index(
        "ix_anomaly_events_deployment_detected",
        "anomaly_events",
        ["deployment_id", "detected_at"],
    )

    op.create_table(
        "budget_policy_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("anomaly_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger_type", sa.String(length=100), nullable=False),
        sa.Column("triggered_at", sa.DateTime(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("action_executed", policy_action_type_enum, nullable=False),
        sa.Column("action_status", sa.String(length=50), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["budget_policies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["anomaly_event_id"], ["anomaly_events.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_budget_policy_events_policy_triggered",
        "budget_policy_events",
        ["policy_id", "triggered_at"],
    )


def downgrade() -> None:
    op.drop_table("budget_policy_events")
    op.drop_table("anomaly_events")
    op.drop_table("budget_policies")
    op.drop_table("memory_snapshots")
    op.drop_table("delegation_edges")
    op.drop_table("agent_actions")
    op.drop_table("agent_sessions")
    op.drop_table("agent_deployments")
