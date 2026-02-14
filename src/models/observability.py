"""Observability models for deployments, sessions, actions, and anomalies."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .trace import AITrace, AITraceSpan


class DeploymentEnvironment(str, Enum):
    """Deployment environment."""

    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class SessionStatus(str, Enum):
    """Agent session status."""

    ACTIVE = "active"
    IDLE = "idle"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


class ActionType(str, Enum):
    """Type of action performed by an agent."""

    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    DELEGATION = "delegation"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    NETWORK_CALL = "network_call"
    POLICY_ACTION = "policy_action"


class MemoryConsistencyState(str, Enum):
    """Consistency status of memory snapshots."""

    CONSISTENT = "consistent"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


class BudgetScopeType(str, Enum):
    """Scope where a budget policy is applied."""

    ORG = "org"
    DEPLOYMENT = "deployment"
    AGENT = "agent"


class BudgetPeriodType(str, Enum):
    """Budget policy accounting period."""

    HOUR = "hour"
    DAY = "day"
    MONTH = "month"


class PolicyActionType(str, Enum):
    """Action to take when a budget is breached."""

    ALERT = "alert"
    THROTTLE = "throttle"
    REQUIRE_APPROVAL = "require_approval"
    SHUTDOWN = "shutdown"


class PolicyStatus(str, Enum):
    """Budget policy lifecycle state."""

    ACTIVE = "active"
    PAUSED = "paused"


class PolicyApprovalStatus(str, Enum):
    """Policy action approval workflow state."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AnomalyType(str, Enum):
    """Supported anomaly classes."""

    API_SPIKE = "api_spike"
    UNUSUAL_RESOURCE_ACCESS = "unusual_resource_access"
    MEMORY_DIVERGENCE = "memory_divergence"
    COST_SPIKE = "cost_spike"
    DELEGATION_LOOP = "delegation_loop"


class AnomalySeverity(str, Enum):
    """Anomaly severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyStatus(str, Enum):
    """Anomaly workflow state."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class DelegationStatus(str, Enum):
    """Delegation edge state."""

    REQUESTED = "requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentDeployment(Base):
    """Agent runtime deployment metadata."""

    __tablename__ = "agent_deployments"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    deployment_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    environment: Mapped[DeploymentEnvironment] = mapped_column(
        PG_ENUM("dev", "staging", "prod", name="deployment_environment", create_type=False),
        nullable=False,
    )
    runtime: Mapped[str] = mapped_column(String(100), nullable=False)
    runtime_version: Mapped[Optional[str]] = mapped_column(String(100))
    region: Mapped[Optional[str]] = mapped_column(String(100))
    owner: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    deployment_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    sessions: Mapped[list["AgentSession"]] = relationship(
        "AgentSession",
        back_populates="deployment",
        cascade="all, delete-orphan",
    )
    budget_policies: Mapped[list["BudgetPolicy"]] = relationship(
        "BudgetPolicy",
        back_populates="deployment",
    )
    anomalies: Mapped[list["AnomalyEvent"]] = relationship(
        "AnomalyEvent",
        back_populates="deployment",
    )
    traces: Mapped[list["AITrace"]] = relationship("AITrace", back_populates="deployment")

    __table_args__ = (
        Index("uq_agent_deployments_org_key", "org_id", "deployment_key", unique=True),
        Index("ix_agent_deployments_environment", "environment"),
    )


class AgentSession(Base):
    """Lifecycle and execution context for an active agent session."""

    __tablename__ = "agent_sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    deployment_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_instance_id: Mapped[Optional[str]] = mapped_column(String(255))
    correlation_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), index=True)
    root_trace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    workload_type: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[SessionStatus] = mapped_column(
        PG_ENUM(
            "active", "idle", "completed", "failed", "terminated",
            name="session_status", create_type=False
        ),
        nullable=False,
        default=SessionStatus.ACTIVE,
    )
    started_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column()
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column()
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[list[str]]] = mapped_column(JSONB, default=list)
    session_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    deployment: Mapped["AgentDeployment"] = relationship("AgentDeployment", back_populates="sessions")
    parent_session: Mapped[Optional["AgentSession"]] = relationship(
        "AgentSession",
        remote_side=[id],
        foreign_keys=[parent_session_id],
        back_populates="child_sessions",
    )
    child_sessions: Mapped[list["AgentSession"]] = relationship(
        "AgentSession",
        back_populates="parent_session",
    )
    actions: Mapped[list["AgentAction"]] = relationship(
        "AgentAction",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    outgoing_delegations: Mapped[list["DelegationEdge"]] = relationship(
        "DelegationEdge",
        foreign_keys="DelegationEdge.parent_session_id",
        back_populates="parent_session",
    )
    incoming_delegations: Mapped[list["DelegationEdge"]] = relationship(
        "DelegationEdge",
        foreign_keys="DelegationEdge.child_session_id",
        back_populates="child_session",
    )
    memory_snapshots: Mapped[list["MemorySnapshot"]] = relationship(
        "MemorySnapshot",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    budget_policy_events: Mapped[list["BudgetPolicyEvent"]] = relationship(
        "BudgetPolicyEvent",
        back_populates="session",
    )
    anomalies: Mapped[list["AnomalyEvent"]] = relationship(
        "AnomalyEvent",
        back_populates="session",
    )
    traces: Mapped[list["AITrace"]] = relationship(
        "AITrace",
        back_populates="session",
        foreign_keys="AITrace.session_id",
    )
    spans: Mapped[list["AITraceSpan"]] = relationship(
        "AITraceSpan",
        back_populates="session",
        foreign_keys="AITraceSpan.session_id",
    )

    __table_args__ = (
        Index("ix_agent_sessions_deployment_status", "deployment_id", "status"),
        Index("ix_agent_sessions_agent_status", "agent_id", "status"),
    )


class AgentAction(Base):
    """Normalized agent runtime action event."""

    __tablename__ = "agent_actions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_event_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    trace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    span_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_trace_spans.id", ondelete="SET NULL"),
        nullable=True,
    )
    action_type: Mapped[ActionType] = mapped_column(
        PG_ENUM(
            "llm_call", "tool_call", "delegation", "memory_read",
            "memory_write", "network_call", "policy_action",
            name="action_type", create_type=False
        ),
        nullable=False,
    )
    action_name: Mapped[str] = mapped_column(String(255), nullable=False)
    resource: Mapped[Optional[str]] = mapped_column(String(2048))
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    model: Mapped[Optional[str]] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    action_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    session: Mapped["AgentSession"] = relationship("AgentSession", back_populates="actions")
    delegations: Mapped[list["DelegationEdge"]] = relationship(
        "DelegationEdge",
        back_populates="parent_action",
    )
    anomalies: Mapped[list["AnomalyEvent"]] = relationship(
        "AnomalyEvent",
        back_populates="action",
    )

    __table_args__ = (
        Index("ix_agent_actions_session_occurred", "session_id", "occurred_at"),
        Index(
            "uq_agent_actions_session_client_event_id",
            "session_id",
            "client_event_id",
            unique=True,
            postgresql_where=sa.text("client_event_id IS NOT NULL"),
        ),
    )


class DelegationEdge(Base):
    """Relationship between parent and child sessions in agent delegation."""

    __tablename__ = "delegation_edges"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    trace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    child_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_action_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[DelegationStatus] = mapped_column(
        PG_ENUM(
            "requested", "accepted", "rejected", "completed", "failed",
            name="delegation_status", create_type=False
        ),
        nullable=False,
        default=DelegationStatus.REQUESTED,
    )
    delegation_reason: Mapped[Optional[str]] = mapped_column(Text)
    requested_capabilities: Mapped[Optional[list[str]]] = mapped_column(JSONB, default=list)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column()
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    delegation_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    parent_session: Mapped["AgentSession"] = relationship(
        "AgentSession",
        foreign_keys=[parent_session_id],
        back_populates="outgoing_delegations",
    )
    child_session: Mapped["AgentSession"] = relationship(
        "AgentSession",
        foreign_keys=[child_session_id],
        back_populates="incoming_delegations",
    )
    parent_action: Mapped[Optional["AgentAction"]] = relationship(
        "AgentAction",
        back_populates="delegations",
    )

    __table_args__ = (
        Index("ix_delegation_edges_parent_started", "parent_session_id", "started_at"),
        Index("ix_delegation_edges_child_started", "child_session_id", "started_at"),
    )


class MemorySnapshot(Base):
    """Memory consistency snapshot emitted by an agent session."""

    __tablename__ = "memory_snapshots"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="SET NULL"),
        nullable=True,
    )
    memory_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    memory_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(255))
    version_vector: Mapped[Optional[dict]] = mapped_column(JSONB)
    source_sequence: Mapped[Optional[int]] = mapped_column(BigInteger)
    consistency_state: Mapped[MemoryConsistencyState] = mapped_column(
        PG_ENUM(
            "consistent", "diverged", "unknown",
            name="memory_consistency_state", create_type=False
        ),
        nullable=False,
    )
    divergence_score: Mapped[Optional[float]] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    snapshot_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    session: Mapped["AgentSession"] = relationship("AgentSession", back_populates="memory_snapshots")

    __table_args__ = (
        Index("ix_memory_snapshots_session_observed", "session_id", "observed_at"),
        Index("ix_memory_snapshots_namespace_key_observed", "memory_namespace", "memory_key", "observed_at"),
        Index("ix_memory_snapshots_state_observed", "consistency_state", "observed_at"),
    )


class BudgetPolicy(Base):
    """Budget threshold policy for cost/token/action limits."""

    __tablename__ = "budget_policies"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_type: Mapped[BudgetScopeType] = mapped_column(
        PG_ENUM("org", "deployment", "agent", name="budget_scope_type", create_type=False),
        nullable=False,
    )
    deployment_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[Optional[str]] = mapped_column(String(255))
    period_type: Mapped[BudgetPeriodType] = mapped_column(
        PG_ENUM("hour", "day", "month", name="budget_period_type", create_type=False),
        nullable=False,
    )
    max_cost_usd: Mapped[Optional[float]] = mapped_column(Float)
    max_input_tokens: Mapped[Optional[int]] = mapped_column(BigInteger)
    max_output_tokens: Mapped[Optional[int]] = mapped_column(BigInteger)
    max_actions: Mapped[Optional[int]] = mapped_column(BigInteger)
    max_session_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    action_on_breach: Mapped[PolicyActionType] = mapped_column(
        PG_ENUM(
            "alert", "throttle", "require_approval", "shutdown",
            name="policy_action_type", create_type=False
        ),
        nullable=False,
    )
    throttle_rate: Mapped[Optional[int]] = mapped_column(Integer)
    cooldown_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    notification_targets: Mapped[Optional[list[str]]] = mapped_column(JSONB, default=list)
    status: Mapped[PolicyStatus] = mapped_column(
        PG_ENUM("active", "paused", name="policy_status", create_type=False),
        nullable=False,
        default=PolicyStatus.ACTIVE,
    )
    policy_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))

    deployment: Mapped[Optional["AgentDeployment"]] = relationship(
        "AgentDeployment",
        back_populates="budget_policies",
    )
    events: Mapped[list["BudgetPolicyEvent"]] = relationship(
        "BudgetPolicyEvent",
        back_populates="policy",
        cascade="all, delete-orphan",
    )
    approvals: Mapped[list["PolicyActionApproval"]] = relationship(
        "PolicyActionApproval",
        back_populates="policy",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_budget_policies_org_status", "org_id", "status"),
        Index("ix_budget_policies_deployment_status", "deployment_id", "status"),
        Index("ix_budget_policies_agent_status", "agent_id", "status"),
    )


class AnomalyEvent(Base):
    """Detected anomaly for runtime behavior."""

    __tablename__ = "anomaly_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    deployment_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    trace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="SET NULL"),
        nullable=True,
    )
    action_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    anomaly_type: Mapped[AnomalyType] = mapped_column(
        PG_ENUM(
            "api_spike", "unusual_resource_access", "memory_divergence",
            "cost_spike", "delegation_loop",
            name="anomaly_type", create_type=False
        ),
        nullable=False,
    )
    severity: Mapped[AnomalySeverity] = mapped_column(
        PG_ENUM("low", "medium", "high", "critical", name="anomaly_severity", create_type=False),
        nullable=False,
    )
    status: Mapped[AnomalyStatus] = mapped_column(
        PG_ENUM("open", "acknowledged", "resolved", name="anomaly_status", create_type=False),
        nullable=False,
        default=AnomalyStatus.OPEN,
    )
    detector_name: Mapped[str] = mapped_column(String(100), nullable=False)
    baseline_value: Mapped[Optional[float]] = mapped_column(Float)
    observed_value: Mapped[Optional[float]] = mapped_column(Float)
    deviation_ratio: Mapped[Optional[float]] = mapped_column(Float)
    score: Mapped[Optional[float]] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column()
    resolved_at: Mapped[Optional[datetime]] = mapped_column()
    updated_by: Mapped[Optional[str]] = mapped_column(String(255))
    note: Mapped[Optional[str]] = mapped_column(Text)
    anomaly_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    deployment: Mapped[Optional["AgentDeployment"]] = relationship(
        "AgentDeployment",
        back_populates="anomalies",
    )
    session: Mapped[Optional["AgentSession"]] = relationship(
        "AgentSession",
        back_populates="anomalies",
    )
    action: Mapped[Optional["AgentAction"]] = relationship(
        "AgentAction",
        back_populates="anomalies",
    )
    budget_policy_events: Mapped[list["BudgetPolicyEvent"]] = relationship(
        "BudgetPolicyEvent",
        back_populates="anomaly_event",
    )

    __table_args__ = (
        Index("ix_anomaly_events_status_severity_detected", "status", "severity", "detected_at"),
        Index("ix_anomaly_events_deployment_detected", "deployment_id", "detected_at"),
    )


class BudgetPolicyEvent(Base):
    """Recorded policy-trigger event when a threshold is crossed."""

    __tablename__ = "budget_policy_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("budget_policies.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    anomaly_event_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("anomaly_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    trigger_type: Mapped[str] = mapped_column(String(100), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(nullable=False)
    observed_value: Mapped[Optional[float]] = mapped_column(Float)
    threshold_value: Mapped[Optional[float]] = mapped_column(Float)
    action_executed: Mapped[PolicyActionType] = mapped_column(
        PG_ENUM(
            "alert", "throttle", "require_approval", "shutdown",
            name="policy_action_type", create_type=False
        ),
        nullable=False,
    )
    action_status: Mapped[Optional[str]] = mapped_column(String(50))
    details: Mapped[Optional[dict]] = mapped_column(JSONB)

    policy: Mapped["BudgetPolicy"] = relationship("BudgetPolicy", back_populates="events")
    session: Mapped[Optional["AgentSession"]] = relationship("AgentSession", back_populates="budget_policy_events")
    anomaly_event: Mapped[Optional["AnomalyEvent"]] = relationship(
        "AnomalyEvent",
        back_populates="budget_policy_events",
    )

    __table_args__ = (
        Index("ix_budget_policy_events_policy_triggered", "policy_id", "triggered_at"),
    )


class PolicyActionApproval(Base):
    """Approval record for sensitive policy actions."""

    __tablename__ = "policy_action_approvals"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("budget_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[PolicyActionType] = mapped_column(
        PG_ENUM(
            "alert", "throttle", "require_approval", "shutdown",
            name="policy_action_type", create_type=False
        ),
        nullable=False,
    )
    status: Mapped[PolicyApprovalStatus] = mapped_column(
        PG_ENUM(
            "pending", "approved", "rejected", "expired",
            name="policy_approval_status", create_type=False
        ),
        nullable=False,
        default=PolicyApprovalStatus.PENDING,
    )
    requested_by: Mapped[Optional[str]] = mapped_column(String(255))
    requested_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(index=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(255))
    decided_at: Mapped[Optional[datetime]] = mapped_column()
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)
    approval_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    policy: Mapped["BudgetPolicy"] = relationship("BudgetPolicy", back_populates="approvals")

    __table_args__ = (
        Index("ix_policy_action_approvals_policy_status", "policy_id", "status"),
        Index("ix_policy_action_approvals_org_status", "org_id", "status"),
        Index("ix_policy_action_approvals_expires_at", "expires_at"),
    )


class ObservabilityOperationRun(Base):
    """Execution log for observability control-loop runs."""

    __tablename__ = "observability_operation_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    detector_summary: Mapped[Optional[dict]] = mapped_column(JSONB)
    policy_summary: Mapped[Optional[dict]] = mapped_column(JSONB)
    notification_summary: Mapped[Optional[dict]] = mapped_column(JSONB)
    run_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("ix_observability_operation_runs_org_started", "org_id", "started_at"),
        Index("ix_observability_operation_runs_run_type_started", "run_type", "started_at"),
    )
