"""Observability API endpoints for fleet/session/runtime monitoring."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional, overload
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, or_, select

from ...dependencies import AuthDep, SettingsDep, StorageDep
from ...models.observability import (
    ActionType,
    AgentAction,
    AgentDeployment,
    AgentSession,
    AnomalyEvent,
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    BudgetPeriodType,
    BudgetPolicy,
    BudgetPolicyEvent,
    BudgetScopeType,
    DelegationEdge,
    DelegationStatus,
    DeploymentEnvironment,
    MemoryConsistencyState,
    MemorySnapshot,
    ObservabilityOperationRun,
    PolicyActionApproval,
    PolicyApprovalStatus,
    PolicyActionType,
    PolicyStatus,
    SessionStatus,
    SystemAuditEvent,
)
from ...models.trace import AITrace
from ...security import AuthContext, require_org_access, require_roles
from ...services.observability_runtime import (
    DetectorConfig,
    evaluate_budget_policies,
    run_anomaly_detectors,
)
from ...services.notifications import (
    collect_policy_notification_targets,
    normalize_webhook_targets,
    send_webhook_notifications,
)
from ...utils.time import to_naive_utc, utc_now_iso, utc_now_naive

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _enum_str(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


@overload
def _to_db_datetime(value: None) -> None: ...


@overload
def _to_db_datetime(value: datetime) -> datetime: ...


def _to_db_datetime(value: Optional[datetime]) -> Optional[datetime]:
    return to_naive_utc(value)


def _utcnow_naive() -> datetime:
    return utc_now_naive()


def _normalize_bucket(dt: datetime, granularity: str) -> datetime:
    if granularity == "1m":
        return dt.replace(second=0, microsecond=0)
    if granularity == "1h":
        return dt.replace(minute=0, second=0, microsecond=0)
    # 5m default
    minute = (dt.minute // 5) * 5
    return dt.replace(minute=minute, second=0, microsecond=0)


def _require_viewer(auth: AuthContext) -> None:
    require_roles(auth, "viewer", "operator", "admin")


def _require_operator(auth: AuthContext) -> None:
    require_roles(auth, "operator", "admin")


def _require_admin(auth: AuthContext) -> None:
    require_roles(auth, "admin")


def _enforce_org_scope(auth: AuthContext, org_id: str) -> None:
    require_org_access(auth, org_id)


def _request_id(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    return getattr(request.state, "request_id", None)


async def _dispatch_runtime_notifications(
    *,
    settings: SettingsDep,
    org_id: str,
    detector_summary: Optional[dict[str, Any]] = None,
    policy_summary: Optional[dict[str, Any]] = None,
    extra_targets: Optional[list[str]] = None,
) -> dict[str, Any]:
    global_targets = normalize_webhook_targets(settings.observability_notification_webhooks)
    policy_targets = collect_policy_notification_targets(policy_summary or {})
    targets = set(global_targets)
    for target in policy_targets:
        targets.add(target)
    for target in extra_targets or []:
        targets.add(target)

    payload = {
        "event_type": "observability_runtime_event",
        "org_id": org_id,
        "detector_summary": detector_summary or {},
        "policy_summary": policy_summary or {},
        "occurred_at": utc_now_iso(),
    }
    return await send_webhook_notifications(
        sorted(targets),
        payload,
        timeout_seconds=settings.observability_notification_timeout_seconds,
        max_attempts=settings.observability_notification_max_attempts,
        retry_backoff_seconds=settings.observability_notification_retry_backoff_seconds,
    )


async def _store_operation_run(
    storage: StorageDep,
    *,
    org_id: str,
    run_type: str,
    started_at: datetime,
    completed_at: datetime,
    success: bool,
    detector_summary: Optional[dict[str, Any]] = None,
    policy_summary: Optional[dict[str, Any]] = None,
    notification_summary: Optional[dict[str, Any]] = None,
    error_message: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    async with storage.session_factory() as session:
        row = ObservabilityOperationRun(
            org_id=org_id,
            run_type=run_type,
            started_at=started_at,
            completed_at=completed_at,
            success=success,
            error_message=error_message,
            detector_summary=detector_summary or {},
            policy_summary=policy_summary or {},
            notification_summary=notification_summary or {},
            run_metadata=metadata or {},
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return str(row.id)


async def _store_audit_event(
    storage: StorageDep,
    *,
    auth: AuthContext,
    org_id: Optional[str],
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    request_id: Optional[str] = None,
    success: bool = True,
    details: Optional[dict[str, Any]] = None,
) -> None:
    async with storage.session_factory() as session:
        row = SystemAuditEvent(
            occurred_at=_utcnow_naive(),
            actor_subject=auth.subject,
            actor_roles=sorted(auth.roles),
            org_id=org_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            success=success,
            details=details or {},
        )
        session.add(row)
        await session.commit()


class DeploymentUpsertRequest(BaseModel):
    org_id: str
    deployment_key: str
    name: str
    environment: DeploymentEnvironment
    runtime: str
    runtime_version: Optional[str] = None
    region: Optional[str] = None
    owner: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeploymentResponse(BaseModel):
    id: str
    org_id: str
    deployment_key: str
    name: str
    environment: str
    runtime: str
    runtime_version: Optional[str] = None
    region: Optional[str] = None
    owner: Optional[str] = None
    is_active: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class DeploymentListResponse(BaseModel):
    deployments: list[DeploymentResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class SessionCreateRequest(BaseModel):
    deployment_id: UUID
    agent_id: str
    agent_instance_id: Optional[str] = None
    correlation_id: Optional[UUID] = None
    root_trace_id: Optional[UUID] = None
    parent_session_id: Optional[UUID] = None
    workload_type: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime


class SessionUpdateRequest(BaseModel):
    status: Optional[SessionStatus] = None
    ended_at: Optional[datetime] = None
    error_message: Optional[str] = None
    last_activity_at: Optional[datetime] = None


class SessionResponse(BaseModel):
    id: str
    deployment_id: str
    agent_id: str
    agent_instance_id: Optional[str] = None
    correlation_id: Optional[str] = None
    root_trace_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    workload_type: Optional[str] = None
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    last_activity_at: Optional[datetime] = None
    error_message: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActiveSessionItem(BaseModel):
    id: str
    deployment_id: str
    agent_id: str
    status: str
    started_at: datetime
    last_activity_at: Optional[datetime] = None
    elapsed_ms: int


class ActiveSessionListResponse(BaseModel):
    sessions: list[ActiveSessionItem]
    total: int
    page: int
    page_size: int
    has_more: bool


class ActionEventRequest(BaseModel):
    client_event_id: Optional[UUID] = None
    trace_id: Optional[UUID] = None
    span_id: Optional[UUID] = None
    action_type: ActionType
    action_name: str
    resource: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: Optional[int] = None
    success: bool = True
    error_message: Optional[str] = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionBatchRequest(BaseModel):
    session_id: UUID
    events: list[ActionEventRequest]


class BatchIngestResponse(BaseModel):
    accepted: int
    rejected: int
    action_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    policy_evaluation: Optional[dict[str, Any]] = None


class DelegationCreateRequest(BaseModel):
    trace_id: Optional[UUID] = None
    parent_session_id: UUID
    child_session_id: UUID
    parent_action_id: Optional[UUID] = None
    status: DelegationStatus = DelegationStatus.REQUESTED
    delegation_reason: Optional[str] = None
    requested_capabilities: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DelegationResponse(BaseModel):
    id: str
    trace_id: Optional[str] = None
    parent_session_id: str
    child_session_id: str
    parent_action_id: Optional[str] = None
    status: str
    delegation_reason: Optional[str] = None
    requested_capabilities: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySnapshotRequest(BaseModel):
    trace_id: Optional[UUID] = None
    memory_namespace: str
    memory_key: str
    content_hash: Optional[str] = None
    version_vector: dict[str, int] = Field(default_factory=dict)
    source_sequence: Optional[int] = None
    consistency_state: MemoryConsistencyState
    divergence_score: Optional[float] = None
    observed_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryBatchRequest(BaseModel):
    session_id: UUID
    snapshots: list[MemorySnapshotRequest]


class MemoryBatchResponse(BaseModel):
    accepted: int
    rejected: int
    snapshot_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class BudgetPolicyCreateRequest(BaseModel):
    org_id: str
    policy_name: str
    scope_type: BudgetScopeType
    deployment_id: Optional[UUID] = None
    agent_id: Optional[str] = None
    period_type: BudgetPeriodType
    max_cost_usd: Optional[float] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_actions: Optional[int] = None
    max_session_minutes: Optional[int] = None
    action_on_breach: PolicyActionType
    throttle_rate: Optional[int] = None
    cooldown_seconds: Optional[int] = None
    notification_targets: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None


class BudgetPolicyResponse(BaseModel):
    id: str
    org_id: str
    policy_name: str
    scope_type: str
    deployment_id: Optional[str] = None
    agent_id: Optional[str] = None
    period_type: str
    max_cost_usd: Optional[float] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_actions: Optional[int] = None
    max_session_minutes: Optional[int] = None
    action_on_breach: str
    throttle_rate: Optional[int] = None
    cooldown_seconds: Optional[int] = None
    notification_targets: list[str] = Field(default_factory=list)
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class BudgetPolicyListResponse(BaseModel):
    policies: list[BudgetPolicyResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class BudgetPolicyEventResponse(BaseModel):
    id: str
    policy_id: str
    policy_name: str
    trigger_type: str
    triggered_at: datetime
    observed_value: Optional[float] = None
    threshold_value: Optional[float] = None
    action_executed: str
    action_status: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class BudgetPolicyEventListResponse(BaseModel):
    events: list[BudgetPolicyEventResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class PolicyApprovalCreateRequest(BaseModel):
    org_id: str
    policy_id: UUID
    requested_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyApprovalDecisionRequest(BaseModel):
    decision: PolicyApprovalStatus
    decided_by: Optional[str] = None
    reason: Optional[str] = None


class PolicyApprovalResponse(BaseModel):
    id: str
    org_id: str
    policy_id: str
    action_type: str
    status: str
    requested_by: Optional[str] = None
    requested_at: datetime
    expires_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyApprovalListResponse(BaseModel):
    approvals: list[PolicyApprovalResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class PolicyEvaluationRequest(BaseModel):
    org_id: str
    as_of: Optional[datetime] = None
    execute_actions: bool = True
    notify: bool = True
    extra_webhook_targets: list[str] = Field(default_factory=list)


class PolicyEvaluationResponse(BaseModel):
    evaluation: dict[str, Any]
    notification_result: Optional[dict[str, Any]] = None
    operation_run_id: Optional[str] = None


class AnomalyCreateRequest(BaseModel):
    deployment_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    trace_id: Optional[UUID] = None
    action_id: Optional[UUID] = None
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    detector_name: str
    baseline_value: Optional[float] = None
    observed_value: Optional[float] = None
    deviation_ratio: Optional[float] = None
    score: Optional[float] = None
    title: str
    description: Optional[str] = None
    detected_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnomalyUpdateRequest(BaseModel):
    status: AnomalyStatus
    note: Optional[str] = None
    updated_by: Optional[str] = None


class AnomalyResponse(BaseModel):
    id: str
    deployment_id: Optional[str] = None
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    action_id: Optional[str] = None
    anomaly_type: str
    severity: str
    status: str
    detector_name: str
    baseline_value: Optional[float] = None
    observed_value: Optional[float] = None
    deviation_ratio: Optional[float] = None
    score: Optional[float] = None
    title: str
    description: Optional[str] = None
    detected_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    note: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnomalyListResponse(BaseModel):
    anomalies: list[AnomalyResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class FleetWindow(BaseModel):
    from_time: datetime = Field(alias="from")
    to_time: datetime = Field(alias="to")
    granularity: str

    model_config = {"populate_by_name": True}


class FleetTotals(BaseModel):
    active_sessions: int
    action_count: int
    error_rate: float
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int


class FleetDashboardResponse(BaseModel):
    window: FleetWindow
    totals: FleetTotals
    timeseries: list[dict[str, Any]]
    top_agents: list[dict[str, Any]]
    top_resources: list[dict[str, Any]]


class CostSummaryResponse(BaseModel):
    totals: dict[str, Any]
    by_agent: list[dict[str, Any]]
    budgets: list[dict[str, Any]]


class MemoryConsistencyResponse(BaseModel):
    summary: dict[str, Any]
    hotspots: list[dict[str, Any]]
    recent: list[dict[str, Any]]


class DelegationChainResponse(BaseModel):
    trace_id: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    stats: dict[str, Any]


class DetectorRunRequest(BaseModel):
    org_id: str
    as_of: Optional[datetime] = None
    current_window_minutes: int = Field(default=15, ge=1, le=180)
    baseline_window_hours: int = Field(default=24, ge=1, le=168)
    api_spike_multiplier: float = Field(default=10.0, ge=1.0, le=100.0)
    cost_spike_multiplier: float = Field(default=5.0, ge=1.0, le=100.0)
    unusual_resource_min_calls: int = Field(default=3, ge=1, le=1000)
    memory_divergence_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    auto_evaluate_policies: bool = True
    execute_policy_actions: bool = True
    notify: bool = True
    extra_webhook_targets: list[str] = Field(default_factory=list)


class DetectorRunResponse(BaseModel):
    detector_run: dict[str, Any]
    policy_evaluation: Optional[dict[str, Any]] = None
    notification_result: Optional[dict[str, Any]] = None
    operation_run_id: Optional[str] = None


class RuntimeOperationsRunRequest(BaseModel):
    org_id: str
    run_detectors: bool = True
    run_policies: bool = True
    execute_policy_actions: bool = True
    notify: bool = True
    as_of: Optional[datetime] = None
    current_window_minutes: int = Field(default=15, ge=1, le=180)
    baseline_window_hours: int = Field(default=24, ge=1, le=168)
    api_spike_multiplier: float = Field(default=10.0, ge=1.0, le=100.0)
    cost_spike_multiplier: float = Field(default=5.0, ge=1.0, le=100.0)
    unusual_resource_min_calls: int = Field(default=3, ge=1, le=1000)
    memory_divergence_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    extra_webhook_targets: list[str] = Field(default_factory=list)


class RuntimeOperationsRunResponse(BaseModel):
    detector_run: dict[str, Any] = Field(default_factory=dict)
    policy_evaluation: dict[str, Any] = Field(default_factory=dict)
    notification_result: Optional[dict[str, Any]] = None
    operation_run_id: Optional[str] = None


class RuntimeOperationsStatusResponse(BaseModel):
    scheduler: dict[str, Any]


class OperationRunResponse(BaseModel):
    id: str
    org_id: str
    run_type: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    success: bool
    error_message: Optional[str] = None
    detector_summary: dict[str, Any] = Field(default_factory=dict)
    policy_summary: dict[str, Any] = Field(default_factory=dict)
    notification_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationRunListResponse(BaseModel):
    runs: list[OperationRunResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class AuditEventResponse(BaseModel):
    id: str
    occurred_at: datetime
    actor_subject: str
    actor_roles: list[str] = Field(default_factory=list)
    org_id: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    request_id: Optional[str] = None
    success: bool
    details: dict[str, Any] = Field(default_factory=dict)


class AuditEventListResponse(BaseModel):
    events: list[AuditEventResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class SiemExportRequest(BaseModel):
    org_id: str
    from_time: datetime = Field(alias="from")
    to_time: datetime = Field(alias="to")
    target_webhook: Optional[str] = None
    dry_run: bool = True
    include_anomalies: bool = True
    include_policy_events: bool = True
    include_operation_runs: bool = True
    include_audit_events: bool = True
    max_records_per_type: int = Field(default=1000, ge=1, le=10000)

    model_config = {"populate_by_name": True}


class SiemExportResponse(BaseModel):
    export_id: str
    org_id: str
    from_time: datetime = Field(alias="from")
    to_time: datetime = Field(alias="to")
    dry_run: bool
    counts: dict[str, int]
    notification_result: Optional[dict[str, Any]] = None
    sample: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


def _deployment_response(dep: AgentDeployment) -> DeploymentResponse:
    return DeploymentResponse(
        id=str(dep.id),
        org_id=dep.org_id,
        deployment_key=dep.deployment_key,
        name=dep.name,
        environment=_enum_str(dep.environment),
        runtime=dep.runtime,
        runtime_version=dep.runtime_version,
        region=dep.region,
        owner=dep.owner,
        is_active=dep.is_active,
        metadata=dep.deployment_metadata or {},
        created_at=dep.created_at,
        updated_at=dep.updated_at,
    )


def _session_response(session: AgentSession) -> SessionResponse:
    return SessionResponse(
        id=str(session.id),
        deployment_id=str(session.deployment_id),
        agent_id=session.agent_id,
        agent_instance_id=session.agent_instance_id,
        correlation_id=str(session.correlation_id) if session.correlation_id else None,
        root_trace_id=str(session.root_trace_id) if session.root_trace_id else None,
        parent_session_id=str(session.parent_session_id) if session.parent_session_id else None,
        workload_type=session.workload_type,
        status=_enum_str(session.status),
        started_at=session.started_at,
        ended_at=session.ended_at,
        duration_ms=session.duration_ms,
        last_activity_at=session.last_activity_at,
        error_message=session.error_message,
        tags=session.tags or [],
        metadata=session.session_metadata or {},
    )


def _budget_policy_response(policy: BudgetPolicy) -> BudgetPolicyResponse:
    return BudgetPolicyResponse(
        id=str(policy.id),
        org_id=policy.org_id,
        policy_name=policy.policy_name,
        scope_type=_enum_str(policy.scope_type),
        deployment_id=str(policy.deployment_id) if policy.deployment_id else None,
        agent_id=policy.agent_id,
        period_type=_enum_str(policy.period_type),
        max_cost_usd=policy.max_cost_usd,
        max_input_tokens=policy.max_input_tokens,
        max_output_tokens=policy.max_output_tokens,
        max_actions=policy.max_actions,
        max_session_minutes=policy.max_session_minutes,
        action_on_breach=_enum_str(policy.action_on_breach),
        throttle_rate=policy.throttle_rate,
        cooldown_seconds=policy.cooldown_seconds,
        notification_targets=policy.notification_targets or [],
        status=_enum_str(policy.status),
        metadata=policy.policy_metadata or {},
        created_by=policy.created_by,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _budget_policy_event_response(
    event: BudgetPolicyEvent,
    policy_name: str,
) -> BudgetPolicyEventResponse:
    return BudgetPolicyEventResponse(
        id=str(event.id),
        policy_id=str(event.policy_id),
        policy_name=policy_name,
        trigger_type=event.trigger_type,
        triggered_at=event.triggered_at,
        observed_value=event.observed_value,
        threshold_value=event.threshold_value,
        action_executed=_enum_str(event.action_executed),
        action_status=event.action_status,
        details=event.details or {},
    )


def _anomaly_response(anomaly: AnomalyEvent) -> AnomalyResponse:
    return AnomalyResponse(
        id=str(anomaly.id),
        deployment_id=str(anomaly.deployment_id) if anomaly.deployment_id else None,
        session_id=str(anomaly.session_id) if anomaly.session_id else None,
        trace_id=str(anomaly.trace_id) if anomaly.trace_id else None,
        action_id=str(anomaly.action_id) if anomaly.action_id else None,
        anomaly_type=_enum_str(anomaly.anomaly_type),
        severity=_enum_str(anomaly.severity),
        status=_enum_str(anomaly.status),
        detector_name=anomaly.detector_name,
        baseline_value=anomaly.baseline_value,
        observed_value=anomaly.observed_value,
        deviation_ratio=anomaly.deviation_ratio,
        score=anomaly.score,
        title=anomaly.title,
        description=anomaly.description,
        detected_at=anomaly.detected_at,
        acknowledged_at=anomaly.acknowledged_at,
        resolved_at=anomaly.resolved_at,
        updated_by=anomaly.updated_by,
        note=anomaly.note,
        metadata=anomaly.anomaly_metadata or {},
    )


def _operation_run_response(row: ObservabilityOperationRun) -> OperationRunResponse:
    return OperationRunResponse(
        id=str(row.id),
        org_id=row.org_id,
        run_type=row.run_type,
        started_at=row.started_at,
        completed_at=row.completed_at,
        success=row.success,
        error_message=row.error_message,
        detector_summary=row.detector_summary or {},
        policy_summary=row.policy_summary or {},
        notification_summary=row.notification_summary or {},
        metadata=row.run_metadata or {},
    )


def _audit_event_response(row: SystemAuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        id=str(row.id),
        occurred_at=row.occurred_at,
        actor_subject=row.actor_subject,
        actor_roles=list(row.actor_roles or []),
        org_id=row.org_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        request_id=row.request_id,
        success=row.success,
        details=row.details or {},
    )


def _policy_approval_response(row: PolicyActionApproval) -> PolicyApprovalResponse:
    return PolicyApprovalResponse(
        id=str(row.id),
        org_id=row.org_id,
        policy_id=str(row.policy_id),
        action_type=_enum_str(row.action_type),
        status=_enum_str(row.status),
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        expires_at=row.expires_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        metadata=row.approval_metadata or {},
    )


@router.post("/deployments", response_model=DeploymentResponse, status_code=201)
async def upsert_deployment(
    payload: DeploymentUpsertRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> DeploymentResponse:
    _require_operator(auth)
    _enforce_org_scope(auth, payload.org_id)
    async with storage.session_factory() as session:
        result = await session.execute(
            select(AgentDeployment).where(
                AgentDeployment.org_id == payload.org_id,
                AgentDeployment.deployment_key == payload.deployment_key,
            )
        )
        deployment = result.scalar_one_or_none()

        if deployment:
            deployment.name = payload.name
            deployment.environment = payload.environment
            deployment.runtime = payload.runtime
            deployment.runtime_version = payload.runtime_version
            deployment.region = payload.region
            deployment.owner = payload.owner
            deployment.is_active = True
            deployment.deployment_metadata = payload.metadata
        else:
            deployment = AgentDeployment(
                org_id=payload.org_id,
                deployment_key=payload.deployment_key,
                name=payload.name,
                environment=payload.environment,
                runtime=payload.runtime,
                runtime_version=payload.runtime_version,
                region=payload.region,
                owner=payload.owner,
                is_active=True,
                deployment_metadata=payload.metadata,
            )
            session.add(deployment)

        await session.commit()
        await session.refresh(deployment)
        return _deployment_response(deployment)


@router.get("/deployments", response_model=DeploymentListResponse)
async def list_deployments(
    storage: StorageDep,
    auth: AuthDep,
    org_id: Optional[str] = Query(None),
    environment: Optional[DeploymentEnvironment] = Query(None),
    is_active: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> DeploymentListResponse:
    _require_viewer(auth)
    if org_id:
        _enforce_org_scope(auth, org_id)
    elif not auth.is_global_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="org_id is required for non-global-admin access",
        )

    offset = (page - 1) * page_size
    async with storage.session_factory() as session:
        query = select(AgentDeployment).order_by(AgentDeployment.created_at.desc())
        count_query = select(func.count(AgentDeployment.id))

        filters = []
        if org_id:
            filters.append(AgentDeployment.org_id == org_id)
        if environment:
            filters.append(AgentDeployment.environment == environment.value)
        if is_active is not None:
            filters.append(AgentDeployment.is_active == is_active)

        if filters:
            query = query.where(*filters)
            count_query = count_query.where(*filters)

        query = query.limit(page_size + 1).offset(offset)
        result = await session.execute(query)
        rows = list(result.scalars().all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]

        total_result = await session.execute(count_query)
        total = int(total_result.scalar() or 0)

        return DeploymentListResponse(
            deployments=[_deployment_response(dep) for dep in rows],
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    payload: SessionCreateRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> SessionResponse:
    _require_operator(auth)
    started_at = _to_db_datetime(payload.started_at)

    async with storage.session_factory() as session:
        deployment = await session.get(AgentDeployment, payload.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")
        _enforce_org_scope(auth, deployment.org_id)

        session_row = AgentSession(
            deployment_id=payload.deployment_id,
            agent_id=payload.agent_id,
            agent_instance_id=payload.agent_instance_id,
            correlation_id=payload.correlation_id,
            root_trace_id=payload.root_trace_id,
            parent_session_id=payload.parent_session_id,
            workload_type=payload.workload_type,
            status=SessionStatus.ACTIVE,
            started_at=started_at,
            last_activity_at=started_at,
            tags=payload.tags,
            session_metadata=payload.metadata,
        )
        session.add(session_row)
        await session.commit()
        await session.refresh(session_row)
        return _session_response(session_row)


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: UUID,
    payload: SessionUpdateRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> SessionResponse:
    _require_operator(auth)
    async with storage.session_factory() as session:
        session_row = await session.get(AgentSession, session_id)
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")
        deployment = await session.get(AgentDeployment, session_row.deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        _enforce_org_scope(auth, deployment.org_id)

        if payload.status is not None:
            session_row.status = payload.status
        if payload.ended_at is not None:
            session_row.ended_at = _to_db_datetime(payload.ended_at)
        if payload.error_message is not None:
            session_row.error_message = payload.error_message
        if payload.last_activity_at is not None:
            session_row.last_activity_at = _to_db_datetime(payload.last_activity_at)

        if session_row.ended_at is not None:
            session_row.duration_ms = int(
                (session_row.ended_at - session_row.started_at).total_seconds() * 1000
            )

        await session.commit()
        await session.refresh(session_row)
        return _session_response(session_row)


@router.get("/sessions/active", response_model=ActiveSessionListResponse)
async def list_active_sessions(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    deployment_id: Optional[UUID] = Query(None),
    agent_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ActiveSessionListResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    offset = (page - 1) * page_size
    now = _utcnow_naive()

    async with storage.session_factory() as session:
        base = (
            select(AgentSession)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(
                AgentDeployment.org_id == org_id,
                AgentSession.status == SessionStatus.ACTIVE.value,
            )
            .order_by(AgentSession.started_at.desc())
        )
        count_q = (
            select(func.count(AgentSession.id))
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(
                AgentDeployment.org_id == org_id,
                AgentSession.status == SessionStatus.ACTIVE.value,
            )
        )

        if deployment_id:
            base = base.where(AgentSession.deployment_id == deployment_id)
            count_q = count_q.where(AgentSession.deployment_id == deployment_id)
        if agent_id:
            base = base.where(AgentSession.agent_id == agent_id)
            count_q = count_q.where(AgentSession.agent_id == agent_id)

        result = await session.execute(base.limit(page_size + 1).offset(offset))
        rows = list(result.scalars().all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]

        total_result = await session.execute(count_q)
        total = int(total_result.scalar() or 0)

        sessions = []
        for row in rows:
            elapsed = int((now - row.started_at).total_seconds() * 1000)
            sessions.append(
                ActiveSessionItem(
                    id=str(row.id),
                    deployment_id=str(row.deployment_id),
                    agent_id=row.agent_id,
                    status=_enum_str(row.status),
                    started_at=row.started_at,
                    last_activity_at=row.last_activity_at,
                    elapsed_ms=max(elapsed, 0),
                )
            )

        return ActiveSessionListResponse(
            sessions=sessions,
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.post("/actions/batch", response_model=BatchIngestResponse, status_code=202)
async def ingest_action_batch(
    payload: ActionBatchRequest,
    storage: StorageDep,
    settings: SettingsDep,
    auth: AuthDep,
    evaluate_policies_flag: bool = Query(True, alias="evaluate_policies"),
) -> BatchIngestResponse:
    _require_operator(auth)
    async with storage.session_factory() as session:
        session_row = await session.get(AgentSession, payload.session_id)
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")
        deployment = await session.get(AgentDeployment, session_row.deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        _enforce_org_scope(auth, deployment.org_id)

        existing_client_ids: set[UUID] = set()
        incoming_client_ids = [e.client_event_id for e in payload.events if e.client_event_id is not None]
        if incoming_client_ids:
            existing_result = await session.execute(
                select(AgentAction.client_event_id).where(
                    AgentAction.session_id == payload.session_id,
                    AgentAction.client_event_id.in_(incoming_client_ids),
                )
            )
            existing_client_ids = {row[0] for row in existing_result.all() if row[0] is not None}

        accepted = 0
        rejected = 0
        action_ids: list[str] = []
        errors: list[str] = []
        latest_event: Optional[datetime] = None

        for event in payload.events:
            if event.client_event_id and event.client_event_id in existing_client_ids:
                rejected += 1
                errors.append(f"duplicate client_event_id {event.client_event_id}")
                continue

            row = AgentAction(
                session_id=payload.session_id,
                client_event_id=event.client_event_id,
                trace_id=event.trace_id,
                span_id=event.span_id,
                action_type=event.action_type,
                action_name=event.action_name,
                resource=event.resource,
                provider=event.provider,
                model=event.model,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                estimated_cost_usd=event.estimated_cost_usd,
                latency_ms=event.latency_ms,
                success=event.success,
                error_message=event.error_message,
                occurred_at=_to_db_datetime(event.occurred_at),
                action_metadata=event.metadata,
            )
            session.add(row)
            await session.flush()
            accepted += 1
            action_ids.append(str(row.id))

            occurred_at = _to_db_datetime(event.occurred_at)
            if occurred_at and (latest_event is None or occurred_at > latest_event):
                latest_event = occurred_at

        if latest_event:
            session_row.last_activity_at = latest_event

        policy_evaluation: Optional[dict[str, Any]] = None
        if evaluate_policies_flag:
            if deployment:
                policy_evaluation = await evaluate_budget_policies(
                    session,
                    deployment.org_id,
                    as_of=latest_event or _utcnow_naive(),
                    execute_actions=True,
                    require_shutdown_approval=settings.observability_shutdown_requires_approval,
                    approval_max_age_minutes=settings.observability_shutdown_approval_max_age_minutes,
                )

        await session.commit()
        return BatchIngestResponse(
            accepted=accepted,
            rejected=rejected,
            action_ids=action_ids,
            errors=errors,
            policy_evaluation=policy_evaluation,
        )


@router.post("/delegations", response_model=DelegationResponse, status_code=201)
async def create_delegation(
    payload: DelegationCreateRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> DelegationResponse:
    _require_operator(auth)
    async with storage.session_factory() as session:
        parent = await session.get(AgentSession, payload.parent_session_id)
        child = await session.get(AgentSession, payload.child_session_id)
        if not parent or not child:
            raise HTTPException(status_code=404, detail="Parent or child session not found")
        parent_deployment = await session.get(AgentDeployment, parent.deployment_id)
        child_deployment = await session.get(AgentDeployment, child.deployment_id)
        if parent_deployment is None or child_deployment is None:
            raise HTTPException(status_code=404, detail="Parent or child deployment not found")
        if parent_deployment.org_id != child_deployment.org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent and child sessions must belong to the same org",
            )
        _enforce_org_scope(auth, parent_deployment.org_id)
        if payload.trace_id:
            trace = await session.get(AITrace, payload.trace_id)
            if not trace:
                raise HTTPException(status_code=404, detail="Trace not found")
        if payload.parent_action_id:
            action = await session.get(AgentAction, payload.parent_action_id)
            if not action:
                raise HTTPException(status_code=404, detail="Parent action not found")

        row = DelegationEdge(
            trace_id=payload.trace_id,
            parent_session_id=payload.parent_session_id,
            child_session_id=payload.child_session_id,
            parent_action_id=payload.parent_action_id,
            status=payload.status,
            delegation_reason=payload.delegation_reason,
            requested_capabilities=payload.requested_capabilities,
            started_at=_to_db_datetime(payload.started_at),
            completed_at=_to_db_datetime(payload.completed_at),
            duration_ms=payload.duration_ms,
            error_message=payload.error_message,
            delegation_metadata=payload.metadata,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

        return DelegationResponse(
            id=str(row.id),
            trace_id=str(row.trace_id) if row.trace_id else None,
            parent_session_id=str(row.parent_session_id),
            child_session_id=str(row.child_session_id),
            parent_action_id=str(row.parent_action_id) if row.parent_action_id else None,
            status=_enum_str(row.status),
            delegation_reason=row.delegation_reason,
            requested_capabilities=row.requested_capabilities or [],
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
            error_message=row.error_message,
            metadata=row.delegation_metadata or {},
        )


@router.post("/memory/snapshots/batch", response_model=MemoryBatchResponse, status_code=202)
async def ingest_memory_snapshots(
    payload: MemoryBatchRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> MemoryBatchResponse:
    _require_operator(auth)
    async with storage.session_factory() as session:
        session_row = await session.get(AgentSession, payload.session_id)
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")
        deployment = await session.get(AgentDeployment, session_row.deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        _enforce_org_scope(auth, deployment.org_id)

        accepted = 0
        rejected = 0
        snapshot_ids: list[str] = []
        errors: list[str] = []

        for snap in payload.snapshots:
            if snap.divergence_score is not None and not (0 <= snap.divergence_score <= 1):
                rejected += 1
                errors.append(f"invalid divergence_score for key {snap.memory_key}")
                continue

            row = MemorySnapshot(
                session_id=payload.session_id,
                trace_id=snap.trace_id,
                memory_namespace=snap.memory_namespace,
                memory_key=snap.memory_key,
                content_hash=snap.content_hash,
                version_vector=snap.version_vector,
                source_sequence=snap.source_sequence,
                consistency_state=snap.consistency_state,
                divergence_score=snap.divergence_score,
                observed_at=_to_db_datetime(snap.observed_at),
                snapshot_metadata=snap.metadata,
            )
            session.add(row)
            await session.flush()
            accepted += 1
            snapshot_ids.append(str(row.id))

        await session.commit()
        return MemoryBatchResponse(
            accepted=accepted,
            rejected=rejected,
            snapshot_ids=snapshot_ids,
            errors=errors,
        )


@router.post("/budget-policies", response_model=BudgetPolicyResponse, status_code=201)
async def create_budget_policy(
    payload: BudgetPolicyCreateRequest,
    storage: StorageDep,
    auth: AuthDep,
    request: Request,
) -> BudgetPolicyResponse:
    _require_admin(auth)
    _enforce_org_scope(auth, payload.org_id)
    async with storage.session_factory() as session:
        if payload.scope_type == BudgetScopeType.DEPLOYMENT and payload.deployment_id is None:
            raise HTTPException(status_code=400, detail="deployment_id required for deployment scope")
        if payload.scope_type == BudgetScopeType.AGENT and not payload.agent_id:
            raise HTTPException(status_code=400, detail="agent_id required for agent scope")

        row = BudgetPolicy(
            org_id=payload.org_id,
            policy_name=payload.policy_name,
            scope_type=payload.scope_type,
            deployment_id=payload.deployment_id,
            agent_id=payload.agent_id,
            period_type=payload.period_type,
            max_cost_usd=payload.max_cost_usd,
            max_input_tokens=payload.max_input_tokens,
            max_output_tokens=payload.max_output_tokens,
            max_actions=payload.max_actions,
            max_session_minutes=payload.max_session_minutes,
            action_on_breach=payload.action_on_breach,
            throttle_rate=payload.throttle_rate,
            cooldown_seconds=payload.cooldown_seconds,
            notification_targets=payload.notification_targets,
            status=PolicyStatus.ACTIVE,
            policy_metadata=payload.metadata,
            created_by=payload.created_by,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        response = _budget_policy_response(row)

    await _store_audit_event(
        storage,
        auth=auth,
        org_id=payload.org_id,
        action="budget_policy_create",
        resource_type="budget_policy",
        resource_id=response.id,
        request_id=_request_id(request),
        details={
            "policy_name": payload.policy_name,
            "scope_type": _enum_str(payload.scope_type),
            "period_type": _enum_str(payload.period_type),
            "action_on_breach": _enum_str(payload.action_on_breach),
        },
    )
    return response


@router.get("/budget-policies", response_model=BudgetPolicyListResponse)
async def list_budget_policies(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    deployment_id: Optional[UUID] = Query(None),
    agent_id: Optional[str] = Query(None),
    status: Optional[PolicyStatus] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> BudgetPolicyListResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    offset = (page - 1) * page_size
    async with storage.session_factory() as session:
        query = select(BudgetPolicy).where(BudgetPolicy.org_id == org_id).order_by(
            BudgetPolicy.created_at.desc()
        )
        count_q = select(func.count(BudgetPolicy.id)).where(BudgetPolicy.org_id == org_id)

        if deployment_id:
            query = query.where(BudgetPolicy.deployment_id == deployment_id)
            count_q = count_q.where(BudgetPolicy.deployment_id == deployment_id)
        if agent_id:
            query = query.where(BudgetPolicy.agent_id == agent_id)
            count_q = count_q.where(BudgetPolicy.agent_id == agent_id)
        if status:
            query = query.where(BudgetPolicy.status == status.value)
            count_q = count_q.where(BudgetPolicy.status == status.value)

        result = await session.execute(query.limit(page_size + 1).offset(offset))
        rows = list(result.scalars().all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]

        total_result = await session.execute(count_q)
        total = int(total_result.scalar() or 0)

        return BudgetPolicyListResponse(
            policies=[_budget_policy_response(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.get("/budget-policies/events", response_model=BudgetPolicyEventListResponse)
async def list_budget_policy_events(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    policy_id: Optional[UUID] = Query(None),
    from_time: Optional[datetime] = Query(None, alias="from"),
    to_time: Optional[datetime] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> BudgetPolicyEventListResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    from_time = _to_db_datetime(from_time)
    to_time = _to_db_datetime(to_time)
    offset = (page - 1) * page_size

    async with storage.session_factory() as session:
        query = (
            select(BudgetPolicyEvent, BudgetPolicy.policy_name)
            .join(BudgetPolicy, BudgetPolicyEvent.policy_id == BudgetPolicy.id)
            .where(BudgetPolicy.org_id == org_id)
            .order_by(desc(BudgetPolicyEvent.triggered_at))
        )
        count_q = (
            select(func.count(BudgetPolicyEvent.id))
            .join(BudgetPolicy, BudgetPolicyEvent.policy_id == BudgetPolicy.id)
            .where(BudgetPolicy.org_id == org_id)
        )

        if policy_id:
            query = query.where(BudgetPolicyEvent.policy_id == policy_id)
            count_q = count_q.where(BudgetPolicyEvent.policy_id == policy_id)
        if from_time:
            query = query.where(BudgetPolicyEvent.triggered_at >= from_time)
            count_q = count_q.where(BudgetPolicyEvent.triggered_at >= from_time)
        if to_time:
            query = query.where(BudgetPolicyEvent.triggered_at <= to_time)
            count_q = count_q.where(BudgetPolicyEvent.triggered_at <= to_time)

        rows = list((await session.execute(query.limit(page_size + 1).offset(offset))).all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        total = int((await session.execute(count_q)).scalar() or 0)

        events = [_budget_policy_event_response(row[0], row[1]) for row in rows]
        return BudgetPolicyEventListResponse(
            events=events,
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.post("/policy-approvals", response_model=PolicyApprovalResponse, status_code=201)
async def create_policy_approval(
    payload: PolicyApprovalCreateRequest,
    storage: StorageDep,
    settings: SettingsDep,
    auth: AuthDep,
    request: Request,
) -> PolicyApprovalResponse:
    _require_operator(auth)
    _enforce_org_scope(auth, payload.org_id)
    requested_at = _utcnow_naive()
    expires_at = _to_db_datetime(payload.expires_at) or (
        requested_at.replace(microsecond=0)
        + timedelta(minutes=settings.observability_shutdown_approval_max_age_minutes)
    )

    async with storage.session_factory() as session:
        policy = await session.get(BudgetPolicy, payload.policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="Budget policy not found")
        if policy.org_id != payload.org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="policy_id org does not match payload org_id",
            )

        row = PolicyActionApproval(
            org_id=payload.org_id,
            policy_id=policy.id,
            action_type=policy.action_on_breach,
            status=PolicyApprovalStatus.PENDING,
            requested_by=payload.requested_by or auth.subject,
            requested_at=requested_at,
            expires_at=expires_at,
            approval_metadata={
                **payload.metadata,
                "reason": payload.reason,
                "requested_by_subject": auth.subject,
            },
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        response = _policy_approval_response(row)

    await _store_audit_event(
        storage,
        auth=auth,
        org_id=payload.org_id,
        action="policy_approval_create",
        resource_type="policy_approval",
        resource_id=response.id,
        request_id=_request_id(request),
        details={
            "policy_id": str(payload.policy_id),
            "expires_at": _to_iso(expires_at),
        },
    )
    return response


@router.post("/policy-approvals/{approval_id}/decision", response_model=PolicyApprovalResponse)
async def decide_policy_approval(
    approval_id: UUID,
    payload: PolicyApprovalDecisionRequest,
    storage: StorageDep,
    auth: AuthDep,
    request: Request,
) -> PolicyApprovalResponse:
    _require_admin(auth)
    if payload.decision not in {PolicyApprovalStatus.APPROVED, PolicyApprovalStatus.REJECTED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision must be approved or rejected",
        )

    now = _utcnow_naive()
    async with storage.session_factory() as session:
        row = await session.get(PolicyActionApproval, approval_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Policy approval not found")
        _enforce_org_scope(auth, row.org_id)

        if row.expires_at and row.expires_at < now and row.status == PolicyApprovalStatus.PENDING:
            row.status = PolicyApprovalStatus.EXPIRED
            row.decided_at = now
            row.decision_reason = "Approval expired before decision"
            await session.commit()
            await session.refresh(row)
            response = _policy_approval_response(row)
            await _store_audit_event(
                storage,
                auth=auth,
                org_id=row.org_id,
                action="policy_approval_expired",
                resource_type="policy_approval",
                resource_id=response.id,
                request_id=_request_id(request),
                details={"policy_id": str(row.policy_id)},
            )
            return response

        row.status = payload.decision
        row.decided_by = payload.decided_by or auth.subject
        row.decided_at = now
        row.decision_reason = payload.reason
        await session.commit()
        await session.refresh(row)
        response = _policy_approval_response(row)

    await _store_audit_event(
        storage,
        auth=auth,
        org_id=response.org_id,
        action="policy_approval_decision",
        resource_type="policy_approval",
        resource_id=response.id,
        request_id=_request_id(request),
        details={
            "decision": response.status,
            "policy_id": response.policy_id,
        },
    )
    return response


@router.get("/policy-approvals", response_model=PolicyApprovalListResponse)
async def list_policy_approvals(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    policy_id: Optional[UUID] = Query(None),
    status_filter: Optional[PolicyApprovalStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PolicyApprovalListResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    offset = (page - 1) * page_size

    async with storage.session_factory() as session:
        query = (
            select(PolicyActionApproval)
            .where(PolicyActionApproval.org_id == org_id)
            .order_by(desc(PolicyActionApproval.requested_at))
        )
        count_q = select(func.count(PolicyActionApproval.id)).where(
            PolicyActionApproval.org_id == org_id
        )

        if policy_id:
            query = query.where(PolicyActionApproval.policy_id == policy_id)
            count_q = count_q.where(PolicyActionApproval.policy_id == policy_id)
        if status_filter:
            query = query.where(PolicyActionApproval.status == status_filter.value)
            count_q = count_q.where(PolicyActionApproval.status == status_filter.value)

        rows = list((await session.execute(query.limit(page_size + 1).offset(offset))).scalars().all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        total = int((await session.execute(count_q)).scalar() or 0)

        return PolicyApprovalListResponse(
            approvals=[_policy_approval_response(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.post("/policies/evaluate", response_model=PolicyEvaluationResponse)
async def evaluate_policies(
    payload: PolicyEvaluationRequest,
    storage: StorageDep,
    settings: SettingsDep,
    auth: AuthDep,
    request: Request,
) -> PolicyEvaluationResponse:
    _require_operator(auth)
    _enforce_org_scope(auth, payload.org_id)
    started_at = _utcnow_naive()
    notification_result: Optional[dict[str, Any]] = None
    async with storage.session_factory() as session:
        summary = await evaluate_budget_policies(
            session,
            payload.org_id,
            as_of=_to_db_datetime(payload.as_of),
            execute_actions=payload.execute_actions,
            require_shutdown_approval=settings.observability_shutdown_requires_approval,
            approval_max_age_minutes=settings.observability_shutdown_approval_max_age_minutes,
        )
        await session.commit()
    if payload.notify:
        notification_result = await _dispatch_runtime_notifications(
            settings=settings,
            org_id=payload.org_id,
            policy_summary=summary,
            extra_targets=payload.extra_webhook_targets,
        )
    operation_run_id = await _store_operation_run(
        storage,
        org_id=payload.org_id,
        run_type="api_policy_evaluation",
        started_at=started_at,
        completed_at=_utcnow_naive(),
        success=True,
        policy_summary=summary,
        notification_summary=notification_result,
        metadata={
            "endpoint": "/api/v1/observability/policies/evaluate",
            "execute_actions": payload.execute_actions,
            "notify": payload.notify,
        },
    )
    await _store_audit_event(
        storage,
        auth=auth,
        org_id=payload.org_id,
        action="policy_evaluate",
        resource_type="org",
        resource_id=payload.org_id,
        request_id=_request_id(request),
        details={
            "operation_run_id": operation_run_id,
            "execute_actions": payload.execute_actions,
            "notify": payload.notify,
            "breached_policies": summary.get("breached_policies", 0),
        },
    )
    return PolicyEvaluationResponse(
        evaluation=summary,
        notification_result=notification_result,
        operation_run_id=operation_run_id,
    )


@router.post("/anomalies", response_model=AnomalyResponse, status_code=201)
async def create_anomaly(
    payload: AnomalyCreateRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> AnomalyResponse:
    _require_operator(auth)
    async with storage.session_factory() as session:
        deployment_id = payload.deployment_id
        if deployment_id is None and payload.session_id is not None:
            session_row = await session.get(AgentSession, payload.session_id)
            if session_row:
                deployment_id = session_row.deployment_id
        if deployment_id is None:
            raise HTTPException(
                status_code=400,
                detail="deployment_id required directly or derivable from session_id",
            )
        deployment = await session.get(AgentDeployment, deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        _enforce_org_scope(auth, deployment.org_id)

        row = AnomalyEvent(
            deployment_id=deployment_id,
            session_id=payload.session_id,
            trace_id=payload.trace_id,
            action_id=payload.action_id,
            anomaly_type=payload.anomaly_type,
            severity=payload.severity,
            status=AnomalyStatus.OPEN,
            detector_name=payload.detector_name,
            baseline_value=payload.baseline_value,
            observed_value=payload.observed_value,
            deviation_ratio=payload.deviation_ratio,
            score=payload.score,
            title=payload.title,
            description=payload.description,
            detected_at=_to_db_datetime(payload.detected_at),
            anomaly_metadata=payload.metadata,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _anomaly_response(row)


@router.patch("/anomalies/{anomaly_id}", response_model=AnomalyResponse)
async def update_anomaly(
    anomaly_id: UUID,
    payload: AnomalyUpdateRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> AnomalyResponse:
    _require_operator(auth)
    now = _utcnow_naive()
    async with storage.session_factory() as session:
        row = await session.get(AnomalyEvent, anomaly_id)
        if not row:
            raise HTTPException(status_code=404, detail="Anomaly not found")
        if row.deployment_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Anomaly has no deployment scope",
            )
        deployment = await session.get(AgentDeployment, row.deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        _enforce_org_scope(auth, deployment.org_id)

        row.status = payload.status
        row.note = payload.note
        row.updated_by = payload.updated_by
        if payload.status == AnomalyStatus.ACKNOWLEDGED and row.acknowledged_at is None:
            row.acknowledged_at = now
        if payload.status == AnomalyStatus.RESOLVED:
            if row.acknowledged_at is None:
                row.acknowledged_at = now
            row.resolved_at = now

        await session.commit()
        await session.refresh(row)
        return _anomaly_response(row)


@router.get("/anomalies", response_model=AnomalyListResponse)
async def list_anomalies(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    status: Optional[AnomalyStatus] = Query(None),
    severity: Optional[AnomalySeverity] = Query(None),
    anomaly_type: Optional[AnomalyType] = Query(None),
    from_time: datetime = Query(..., alias="from"),
    to_time: datetime = Query(..., alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> AnomalyListResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    from_time = _to_db_datetime(from_time)
    to_time = _to_db_datetime(to_time)

    offset = (page - 1) * page_size
    async with storage.session_factory() as session:
        query = (
            select(AnomalyEvent)
            .join(AgentDeployment, AnomalyEvent.deployment_id == AgentDeployment.id)
            .where(
                AnomalyEvent.detected_at >= from_time,
                AnomalyEvent.detected_at <= to_time,
                AgentDeployment.org_id == org_id,
            )
            .order_by(desc(AnomalyEvent.detected_at))
        )
        count_q = (
            select(func.count(AnomalyEvent.id))
            .join(AgentDeployment, AnomalyEvent.deployment_id == AgentDeployment.id)
            .where(
                AnomalyEvent.detected_at >= from_time,
                AnomalyEvent.detected_at <= to_time,
                AgentDeployment.org_id == org_id,
            )
        )
        if status:
            query = query.where(AnomalyEvent.status == status.value)
            count_q = count_q.where(AnomalyEvent.status == status.value)
        if severity:
            query = query.where(AnomalyEvent.severity == severity.value)
            count_q = count_q.where(AnomalyEvent.severity == severity.value)
        if anomaly_type:
            query = query.where(AnomalyEvent.anomaly_type == anomaly_type.value)
            count_q = count_q.where(AnomalyEvent.anomaly_type == anomaly_type.value)

        result = await session.execute(query.limit(page_size + 1).offset(offset))
        rows = list(result.scalars().all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]

        total_result = await session.execute(count_q)
        total = int(total_result.scalar() or 0)

        return AnomalyListResponse(
            anomalies=[_anomaly_response(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.post("/detectors/run", response_model=DetectorRunResponse)
async def run_detectors(
    payload: DetectorRunRequest,
    storage: StorageDep,
    settings: SettingsDep,
    auth: AuthDep,
    request: Request,
) -> DetectorRunResponse:
    _require_operator(auth)
    _enforce_org_scope(auth, payload.org_id)
    started_at = _utcnow_naive()
    config = DetectorConfig(
        current_window_minutes=payload.current_window_minutes,
        baseline_window_hours=payload.baseline_window_hours,
        api_spike_multiplier=payload.api_spike_multiplier,
        cost_spike_multiplier=payload.cost_spike_multiplier,
        unusual_resource_min_calls=payload.unusual_resource_min_calls,
        memory_divergence_threshold=payload.memory_divergence_threshold,
    )

    async with storage.session_factory() as session:
        detector_summary = await run_anomaly_detectors(
            session,
            payload.org_id,
            as_of=_to_db_datetime(payload.as_of),
            config=config,
        )

        policy_evaluation: Optional[dict[str, Any]] = None
        if payload.auto_evaluate_policies:
            policy_evaluation = await evaluate_budget_policies(
                session,
                payload.org_id,
                as_of=_to_db_datetime(payload.as_of),
                execute_actions=payload.execute_policy_actions,
                require_shutdown_approval=settings.observability_shutdown_requires_approval,
                approval_max_age_minutes=settings.observability_shutdown_approval_max_age_minutes,
            )

        await session.commit()
    notification_result: Optional[dict[str, Any]] = None
    if payload.notify:
        notification_result = await _dispatch_runtime_notifications(
            settings=settings,
            org_id=payload.org_id,
            detector_summary=detector_summary,
            policy_summary=policy_evaluation,
            extra_targets=payload.extra_webhook_targets,
        )
    operation_run_id = await _store_operation_run(
        storage,
        org_id=payload.org_id,
        run_type="api_detector_run",
        started_at=started_at,
        completed_at=_utcnow_naive(),
        success=True,
        detector_summary=detector_summary,
        policy_summary=policy_evaluation,
        notification_summary=notification_result,
        metadata={
            "endpoint": "/api/v1/observability/detectors/run",
            "auto_evaluate_policies": payload.auto_evaluate_policies,
            "execute_policy_actions": payload.execute_policy_actions,
            "notify": payload.notify,
        },
    )
    await _store_audit_event(
        storage,
        auth=auth,
        org_id=payload.org_id,
        action="detectors_run",
        resource_type="org",
        resource_id=payload.org_id,
        request_id=_request_id(request),
        details={
            "operation_run_id": operation_run_id,
            "auto_evaluate_policies": payload.auto_evaluate_policies,
            "execute_policy_actions": payload.execute_policy_actions,
            "notify": payload.notify,
            "created_anomalies": detector_summary.get("total_created", 0),
        },
    )
    return DetectorRunResponse(
        detector_run=detector_summary,
        policy_evaluation=policy_evaluation,
        notification_result=notification_result,
        operation_run_id=operation_run_id,
    )


@router.post("/operations/run", response_model=RuntimeOperationsRunResponse)
async def run_operations_cycle(
    payload: RuntimeOperationsRunRequest,
    storage: StorageDep,
    settings: SettingsDep,
    auth: AuthDep,
    request: Request,
) -> RuntimeOperationsRunResponse:
    _require_operator(auth)
    _enforce_org_scope(auth, payload.org_id)
    started_at = _utcnow_naive()
    detector_summary: dict[str, Any] = {}
    policy_summary: dict[str, Any] = {}

    detector_config = DetectorConfig(
        current_window_minutes=payload.current_window_minutes,
        baseline_window_hours=payload.baseline_window_hours,
        api_spike_multiplier=payload.api_spike_multiplier,
        cost_spike_multiplier=payload.cost_spike_multiplier,
        unusual_resource_min_calls=payload.unusual_resource_min_calls,
        memory_divergence_threshold=payload.memory_divergence_threshold,
    )

    async with storage.session_factory() as session:
        if payload.run_detectors:
            detector_summary = await run_anomaly_detectors(
                session,
                payload.org_id,
                as_of=_to_db_datetime(payload.as_of),
                config=detector_config,
            )
        if payload.run_policies:
            policy_summary = await evaluate_budget_policies(
                session,
                payload.org_id,
                as_of=_to_db_datetime(payload.as_of),
                execute_actions=payload.execute_policy_actions,
                require_shutdown_approval=settings.observability_shutdown_requires_approval,
                approval_max_age_minutes=settings.observability_shutdown_approval_max_age_minutes,
            )
        await session.commit()

    notification_result: Optional[dict[str, Any]] = None
    if payload.notify:
        notification_result = await _dispatch_runtime_notifications(
            settings=settings,
            org_id=payload.org_id,
            detector_summary=detector_summary,
            policy_summary=policy_summary,
            extra_targets=payload.extra_webhook_targets,
        )
    operation_run_id = await _store_operation_run(
        storage,
        org_id=payload.org_id,
        run_type="api_operations_run",
        started_at=started_at,
        completed_at=_utcnow_naive(),
        success=True,
        detector_summary=detector_summary,
        policy_summary=policy_summary,
        notification_summary=notification_result,
        metadata={
            "endpoint": "/api/v1/observability/operations/run",
            "run_detectors": payload.run_detectors,
            "run_policies": payload.run_policies,
            "execute_policy_actions": payload.execute_policy_actions,
            "notify": payload.notify,
        },
    )
    await _store_audit_event(
        storage,
        auth=auth,
        org_id=payload.org_id,
        action="operations_run",
        resource_type="org",
        resource_id=payload.org_id,
        request_id=_request_id(request),
        details={
            "operation_run_id": operation_run_id,
            "run_detectors": payload.run_detectors,
            "run_policies": payload.run_policies,
            "execute_policy_actions": payload.execute_policy_actions,
            "notify": payload.notify,
        },
    )

    return RuntimeOperationsRunResponse(
        detector_run=detector_summary,
        policy_evaluation=policy_summary,
        notification_result=notification_result,
        operation_run_id=operation_run_id,
    )


@router.get("/operations/status", response_model=RuntimeOperationsStatusResponse)
async def get_operations_status(request: Request, auth: AuthDep) -> RuntimeOperationsStatusResponse:
    _require_viewer(auth)
    scheduler = getattr(request.app.state, "observability_scheduler", None)
    if scheduler is None:
        return RuntimeOperationsStatusResponse(
            scheduler={
                "enabled": False,
                "running": False,
                "reason": "scheduler_not_initialized",
            }
        )
    return RuntimeOperationsStatusResponse(scheduler=scheduler.status())


@router.get("/operations/runs", response_model=OperationRunListResponse)
async def list_operation_runs(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    run_type: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    from_time: Optional[datetime] = Query(None, alias="from"),
    to_time: Optional[datetime] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> OperationRunListResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    from_time = _to_db_datetime(from_time)
    to_time = _to_db_datetime(to_time)
    offset = (page - 1) * page_size

    async with storage.session_factory() as session:
        query = (
            select(ObservabilityOperationRun)
            .where(ObservabilityOperationRun.org_id == org_id)
            .order_by(desc(ObservabilityOperationRun.started_at))
        )
        count_q = select(func.count(ObservabilityOperationRun.id)).where(
            ObservabilityOperationRun.org_id == org_id
        )

        if run_type:
            query = query.where(ObservabilityOperationRun.run_type == run_type)
            count_q = count_q.where(ObservabilityOperationRun.run_type == run_type)
        if success is not None:
            query = query.where(ObservabilityOperationRun.success == success)
            count_q = count_q.where(ObservabilityOperationRun.success == success)
        if from_time:
            query = query.where(ObservabilityOperationRun.started_at >= from_time)
            count_q = count_q.where(ObservabilityOperationRun.started_at >= from_time)
        if to_time:
            query = query.where(ObservabilityOperationRun.started_at <= to_time)
            count_q = count_q.where(ObservabilityOperationRun.started_at <= to_time)

        rows = list((await session.execute(query.limit(page_size + 1).offset(offset))).scalars().all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        total = int((await session.execute(count_q)).scalar() or 0)

        return OperationRunListResponse(
            runs=[_operation_run_response(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.get("/operations/runs/{run_id}", response_model=OperationRunResponse)
async def get_operation_run(
    run_id: UUID,
    storage: StorageDep,
    auth: AuthDep,
) -> OperationRunResponse:
    _require_viewer(auth)
    async with storage.session_factory() as session:
        row = await session.get(ObservabilityOperationRun, run_id)
        if not row:
            raise HTTPException(status_code=404, detail="Operation run not found")
        _enforce_org_scope(auth, row.org_id)
        return _operation_run_response(row)


@router.get("/audit/events", response_model=AuditEventListResponse)
async def list_audit_events(
    storage: StorageDep,
    auth: AuthDep,
    org_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    from_time: Optional[datetime] = Query(None, alias="from"),
    to_time: Optional[datetime] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> AuditEventListResponse:
    _require_admin(auth)
    if org_id:
        _enforce_org_scope(auth, org_id)
    elif not auth.is_global_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="org_id is required for non-global-admin access",
        )

    from_time = _to_db_datetime(from_time)
    to_time = _to_db_datetime(to_time)
    offset = (page - 1) * page_size

    async with storage.session_factory() as session:
        query = select(SystemAuditEvent).order_by(desc(SystemAuditEvent.occurred_at))
        count_q = select(func.count(SystemAuditEvent.id))

        if org_id:
            query = query.where(SystemAuditEvent.org_id == org_id)
            count_q = count_q.where(SystemAuditEvent.org_id == org_id)
        if action:
            query = query.where(SystemAuditEvent.action == action)
            count_q = count_q.where(SystemAuditEvent.action == action)
        if from_time:
            query = query.where(SystemAuditEvent.occurred_at >= from_time)
            count_q = count_q.where(SystemAuditEvent.occurred_at >= from_time)
        if to_time:
            query = query.where(SystemAuditEvent.occurred_at <= to_time)
            count_q = count_q.where(SystemAuditEvent.occurred_at <= to_time)

        rows = list((await session.execute(query.limit(page_size + 1).offset(offset))).scalars().all())
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        total = int((await session.execute(count_q)).scalar() or 0)

        return AuditEventListResponse(
            events=[_audit_event_response(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            has_more=has_more,
        )


@router.post("/exports/siem", response_model=SiemExportResponse)
async def export_siem_events(
    payload: SiemExportRequest,
    storage: StorageDep,
    settings: SettingsDep,
    auth: AuthDep,
    request: Request,
) -> SiemExportResponse:
    _require_admin(auth)
    _enforce_org_scope(auth, payload.org_id)
    from_time = _to_db_datetime(payload.from_time)
    to_time = _to_db_datetime(payload.to_time)
    if to_time < from_time:
        raise HTTPException(status_code=400, detail="to must be >= from")

    export_id = str(uuid4())
    anomalies: list[dict[str, Any]] = []
    policy_events: list[dict[str, Any]] = []
    operation_runs: list[dict[str, Any]] = []
    audit_events: list[dict[str, Any]] = []

    async with storage.session_factory() as session:
        if payload.include_anomalies:
            anomaly_rows = list(
                (
                    await session.execute(
                        select(AnomalyEvent)
                        .join(AgentDeployment, AnomalyEvent.deployment_id == AgentDeployment.id)
                        .where(
                            AgentDeployment.org_id == payload.org_id,
                            AnomalyEvent.detected_at >= from_time,
                            AnomalyEvent.detected_at <= to_time,
                        )
                        .order_by(desc(AnomalyEvent.detected_at))
                        .limit(payload.max_records_per_type)
                    )
                ).scalars().all()
            )
            anomalies = [
                {
                    "id": str(row.id),
                    "anomaly_type": _enum_str(row.anomaly_type),
                    "severity": _enum_str(row.severity),
                    "status": _enum_str(row.status),
                    "detector_name": row.detector_name,
                    "title": row.title,
                    "description": row.description,
                    "detected_at": _to_iso(row.detected_at),
                    "metadata": row.anomaly_metadata or {},
                }
                for row in anomaly_rows
            ]

        if payload.include_policy_events:
            policy_event_rows = list(
                (
                    await session.execute(
                        select(BudgetPolicyEvent, BudgetPolicy.policy_name)
                        .join(BudgetPolicy, BudgetPolicyEvent.policy_id == BudgetPolicy.id)
                        .where(
                            BudgetPolicy.org_id == payload.org_id,
                            BudgetPolicyEvent.triggered_at >= from_time,
                            BudgetPolicyEvent.triggered_at <= to_time,
                        )
                        .order_by(desc(BudgetPolicyEvent.triggered_at))
                        .limit(payload.max_records_per_type)
                    )
                ).all()
            )
            policy_events = [
                {
                    "id": str(row[0].id),
                    "policy_id": str(row[0].policy_id),
                    "policy_name": row[1],
                    "trigger_type": row[0].trigger_type,
                    "triggered_at": _to_iso(row[0].triggered_at),
                    "observed_value": row[0].observed_value,
                    "threshold_value": row[0].threshold_value,
                    "action_executed": _enum_str(row[0].action_executed),
                    "action_status": row[0].action_status,
                    "details": row[0].details or {},
                }
                for row in policy_event_rows
            ]

        if payload.include_operation_runs:
            operation_run_rows = list(
                (
                    await session.execute(
                        select(ObservabilityOperationRun)
                        .where(
                            ObservabilityOperationRun.org_id == payload.org_id,
                            ObservabilityOperationRun.started_at >= from_time,
                            ObservabilityOperationRun.started_at <= to_time,
                        )
                        .order_by(desc(ObservabilityOperationRun.started_at))
                        .limit(payload.max_records_per_type)
                    )
                ).scalars().all()
            )
            operation_runs = [
                {
                    "id": str(row.id),
                    "run_type": row.run_type,
                    "started_at": _to_iso(row.started_at),
                    "completed_at": _to_iso(row.completed_at),
                    "success": row.success,
                    "error_message": row.error_message,
                    "detector_summary": row.detector_summary or {},
                    "policy_summary": row.policy_summary or {},
                    "notification_summary": row.notification_summary or {},
                }
                for row in operation_run_rows
            ]

        if payload.include_audit_events:
            audit_rows = list(
                (
                    await session.execute(
                        select(SystemAuditEvent)
                        .where(
                            SystemAuditEvent.org_id == payload.org_id,
                            SystemAuditEvent.occurred_at >= from_time,
                            SystemAuditEvent.occurred_at <= to_time,
                        )
                        .order_by(desc(SystemAuditEvent.occurred_at))
                        .limit(payload.max_records_per_type)
                    )
                ).scalars().all()
            )
            audit_events = [
                {
                    "id": str(row.id),
                    "occurred_at": _to_iso(row.occurred_at),
                    "actor_subject": row.actor_subject,
                    "actor_roles": row.actor_roles or [],
                    "action": row.action,
                    "resource_type": row.resource_type,
                    "resource_id": row.resource_id,
                    "request_id": row.request_id,
                    "success": row.success,
                    "details": row.details or {},
                }
                for row in audit_rows
            ]

    counts = {
        "anomalies": len(anomalies),
        "policy_events": len(policy_events),
        "operation_runs": len(operation_runs),
        "audit_events": len(audit_events),
    }
    export_payload = {
        "event_type": "observability_siem_export",
        "export_id": export_id,
        "org_id": payload.org_id,
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "counts": counts,
        "data": {
            "anomalies": anomalies,
            "policy_events": policy_events,
            "operation_runs": operation_runs,
            "audit_events": audit_events,
        },
    }

    notification_result: Optional[dict[str, Any]] = None
    if not payload.dry_run:
        if not payload.target_webhook:
            raise HTTPException(
                status_code=400,
                detail="target_webhook is required when dry_run=false",
            )
        notification_result = await send_webhook_notifications(
            [payload.target_webhook],
            export_payload,
            timeout_seconds=settings.observability_notification_timeout_seconds,
            max_attempts=settings.observability_notification_max_attempts,
            retry_backoff_seconds=settings.observability_notification_retry_backoff_seconds,
        )

    await _store_audit_event(
        storage,
        auth=auth,
        org_id=payload.org_id,
        action="siem_export",
        resource_type="org",
        resource_id=payload.org_id,
        request_id=_request_id(request),
        details={
            "export_id": export_id,
            "counts": counts,
            "dry_run": payload.dry_run,
            "has_target_webhook": bool(payload.target_webhook),
        },
    )

    return SiemExportResponse.model_validate(
        {
            "export_id": export_id,
            "org_id": payload.org_id,
            "from": from_time,
            "to": to_time,
            "dry_run": payload.dry_run,
            "counts": counts,
            "notification_result": notification_result,
            "sample": {
                "anomalies": anomalies[:2],
                "policy_events": policy_events[:2],
                "operation_runs": operation_runs[:2],
                "audit_events": audit_events[:2],
            },
        }
    )


@router.get("/dashboard/fleet", response_model=FleetDashboardResponse)
async def get_fleet_dashboard(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    deployment_id: Optional[UUID] = Query(None),
    from_time: datetime = Query(..., alias="from"),
    to_time: datetime = Query(..., alias="to"),
    granularity: str = Query("5m", pattern="^(1m|5m|1h)$"),
) -> FleetDashboardResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    from_time = _to_db_datetime(from_time)
    to_time = _to_db_datetime(to_time)

    async with storage.session_factory() as session:
        action_filters = [
            AgentAction.occurred_at >= from_time,
            AgentAction.occurred_at <= to_time,
            AgentDeployment.org_id == org_id,
        ]
        if deployment_id:
            action_filters.append(AgentSession.deployment_id == deployment_id)

        totals_q = (
            select(
                func.count(AgentAction.id),
                func.sum(case((AgentAction.success.is_(False), 1), else_=0)),
                func.sum(AgentAction.estimated_cost_usd),
                func.sum(AgentAction.input_tokens),
                func.sum(AgentAction.output_tokens),
            )
            .join(AgentSession, AgentAction.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*action_filters)
        )
        totals_row = (await session.execute(totals_q)).one()
        action_count = int(totals_row[0] or 0)
        error_count = int(totals_row[1] or 0)
        total_cost = float(totals_row[2] or 0.0)
        total_input_tokens = int(totals_row[3] or 0)
        total_output_tokens = int(totals_row[4] or 0)

        active_sessions_q = (
            select(func.count(AgentSession.id))
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(
                AgentDeployment.org_id == org_id,
                AgentSession.status == SessionStatus.ACTIVE.value,
            )
        )
        if deployment_id:
            active_sessions_q = active_sessions_q.where(AgentSession.deployment_id == deployment_id)
        active_sessions = int((await session.execute(active_sessions_q)).scalar() or 0)

        series_q = (
            select(
                func.date_trunc("minute", AgentAction.occurred_at).label("bucket"),
                func.count(AgentAction.id),
                func.sum(case((AgentAction.success.is_(False), 1), else_=0)),
                func.sum(AgentAction.estimated_cost_usd),
                func.sum(AgentAction.input_tokens),
                func.sum(AgentAction.output_tokens),
            )
            .join(AgentSession, AgentAction.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*action_filters)
            .group_by("bucket")
            .order_by("bucket")
        )
        series_rows = (await session.execute(series_q)).all()
        grouped: dict[datetime, dict[str, float]] = defaultdict(
            lambda: {
                "action_count": 0,
                "error_count": 0,
                "total_cost_usd": 0.0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            }
        )

        for row in series_rows:
            bucket = _normalize_bucket(row[0], granularity)
            grouped[bucket]["action_count"] += int(row[1] or 0)
            grouped[bucket]["error_count"] += int(row[2] or 0)
            grouped[bucket]["total_cost_usd"] += float(row[3] or 0.0)
            grouped[bucket]["total_input_tokens"] += int(row[4] or 0)
            grouped[bucket]["total_output_tokens"] += int(row[5] or 0)

        timeseries: list[dict[str, Any]] = []
        for bucket in sorted(grouped.keys()):
            item = grouped[bucket]
            count = int(item["action_count"])
            timeseries.append(
                {
                    "bucket": bucket.isoformat(),
                    "action_count": count,
                    "error_rate": (float(item["error_count"]) / count) if count > 0 else 0.0,
                    "total_cost_usd": float(item["total_cost_usd"]),
                    "total_input_tokens": int(item["total_input_tokens"]),
                    "total_output_tokens": int(item["total_output_tokens"]),
                }
            )

        top_agents_q = (
            select(
                AgentSession.agent_id,
                func.count(AgentAction.id).label("action_count"),
                func.sum(AgentAction.estimated_cost_usd).label("total_cost_usd"),
            )
            .join(AgentSession, AgentAction.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*action_filters)
            .group_by(AgentSession.agent_id)
            .order_by(desc("action_count"))
            .limit(10)
        )
        top_agents_rows = (await session.execute(top_agents_q)).all()
        top_agents = [
            {
                "agent_id": row[0],
                "action_count": int(row[1] or 0),
                "total_cost_usd": float(row[2] or 0.0),
            }
            for row in top_agents_rows
        ]

        top_resources_q = (
            select(
                AgentAction.resource,
                func.count(AgentAction.id).label("action_count"),
            )
            .join(AgentSession, AgentAction.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*action_filters, AgentAction.resource.is_not(None))
            .group_by(AgentAction.resource)
            .order_by(desc("action_count"))
            .limit(10)
        )
        top_resources_rows = (await session.execute(top_resources_q)).all()
        top_resources = [
            {"resource": row[0], "action_count": int(row[1] or 0)} for row in top_resources_rows
        ]

        window = FleetWindow.model_validate(
            {"from": from_time, "to": to_time, "granularity": granularity}
        )

        return FleetDashboardResponse(
            window=window,
            totals=FleetTotals(
                active_sessions=active_sessions,
                action_count=action_count,
                error_rate=(error_count / action_count) if action_count > 0 else 0.0,
                total_cost_usd=total_cost,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
            ),
            timeseries=timeseries,
            top_agents=top_agents,
            top_resources=top_resources,
        )


@router.get("/costs/summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    from_time: datetime = Query(..., alias="from"),
    to_time: datetime = Query(..., alias="to"),
    deployment_id: Optional[UUID] = Query(None),
    agent_id: Optional[str] = Query(None),
) -> CostSummaryResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    from_time = _to_db_datetime(from_time)
    to_time = _to_db_datetime(to_time)

    async with storage.session_factory() as session:
        action_filters = [
            AgentAction.occurred_at >= from_time,
            AgentAction.occurred_at <= to_time,
            AgentDeployment.org_id == org_id,
        ]
        if deployment_id:
            action_filters.append(AgentSession.deployment_id == deployment_id)
        if agent_id:
            action_filters.append(AgentSession.agent_id == agent_id)

        totals_q = (
            select(
                func.sum(AgentAction.estimated_cost_usd),
                func.sum(AgentAction.input_tokens),
                func.sum(AgentAction.output_tokens),
                func.count(AgentAction.id),
            )
            .join(AgentSession, AgentAction.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*action_filters)
        )
        totals = (await session.execute(totals_q)).one()

        by_agent_q = (
            select(
                AgentSession.agent_id,
                func.sum(AgentAction.estimated_cost_usd),
                func.sum(AgentAction.input_tokens),
                func.sum(AgentAction.output_tokens),
                func.count(AgentAction.id),
            )
            .join(AgentSession, AgentAction.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*action_filters)
            .group_by(AgentSession.agent_id)
            .order_by(desc(func.sum(AgentAction.estimated_cost_usd)))
        )
        by_agent_rows = (await session.execute(by_agent_q)).all()

        policy_q = select(BudgetPolicy).where(BudgetPolicy.org_id == org_id)
        if deployment_id:
            policy_q = policy_q.where(
                or_(BudgetPolicy.deployment_id == deployment_id, BudgetPolicy.deployment_id.is_(None))
            )
        if agent_id:
            policy_q = policy_q.where(or_(BudgetPolicy.agent_id == agent_id, BudgetPolicy.agent_id.is_(None)))

        policies = list((await session.execute(policy_q)).scalars().all())
        budgets: list[dict[str, Any]] = []
        total_cost = float(totals[0] or 0.0)

        for policy in policies:
            scope_cost = total_cost
            if policy.scope_type == BudgetScopeType.DEPLOYMENT and policy.deployment_id:
                scope_cost_q = (
                    select(func.sum(AgentAction.estimated_cost_usd))
                    .join(AgentSession, AgentAction.session_id == AgentSession.id)
                    .where(
                        AgentSession.deployment_id == policy.deployment_id,
                        AgentAction.occurred_at >= from_time,
                        AgentAction.occurred_at <= to_time,
                    )
                )
                scope_cost = float((await session.execute(scope_cost_q)).scalar() or 0.0)
            elif policy.scope_type == BudgetScopeType.AGENT and policy.agent_id:
                scope_cost_q = (
                    select(func.sum(AgentAction.estimated_cost_usd))
                    .join(AgentSession, AgentAction.session_id == AgentSession.id)
                    .where(
                        AgentSession.agent_id == policy.agent_id,
                        AgentAction.occurred_at >= from_time,
                        AgentAction.occurred_at <= to_time,
                    )
                )
                scope_cost = float((await session.execute(scope_cost_q)).scalar() or 0.0)

            utilization = (
                (scope_cost / policy.max_cost_usd) if policy.max_cost_usd and policy.max_cost_usd > 0 else None
            )
            budgets.append(
                {
                    "policy_id": str(policy.id),
                    "policy_name": policy.policy_name,
                    "scope_type": _enum_str(policy.scope_type),
                    "status": _enum_str(policy.status),
                    "max_cost_usd": policy.max_cost_usd,
                    "current_cost_usd": scope_cost,
                    "utilization": utilization,
                    "action_on_breach": _enum_str(policy.action_on_breach),
                }
            )

        return CostSummaryResponse(
            totals={
                "cost_usd": float(totals[0] or 0.0),
                "input_tokens": int(totals[1] or 0),
                "output_tokens": int(totals[2] or 0),
                "action_count": int(totals[3] or 0),
            },
            by_agent=[
                {
                    "agent_id": row[0],
                    "cost_usd": float(row[1] or 0.0),
                    "input_tokens": int(row[2] or 0),
                    "output_tokens": int(row[3] or 0),
                    "action_count": int(row[4] or 0),
                }
                for row in by_agent_rows
            ],
            budgets=budgets,
        )


@router.get("/memory/consistency", response_model=MemoryConsistencyResponse)
async def get_memory_consistency(
    storage: StorageDep,
    auth: AuthDep,
    org_id: str = Query(...),
    from_time: datetime = Query(..., alias="from"),
    to_time: datetime = Query(..., alias="to"),
    deployment_id: Optional[UUID] = Query(None),
    session_id: Optional[UUID] = Query(None),
) -> MemoryConsistencyResponse:
    _require_viewer(auth)
    _enforce_org_scope(auth, org_id)
    from_time = _to_db_datetime(from_time)
    to_time = _to_db_datetime(to_time)

    async with storage.session_factory() as session:
        filters = [
            MemorySnapshot.observed_at >= from_time,
            MemorySnapshot.observed_at <= to_time,
            AgentDeployment.org_id == org_id,
        ]
        if deployment_id:
            filters.append(AgentSession.deployment_id == deployment_id)
        if session_id:
            filters.append(MemorySnapshot.session_id == session_id)

        summary_q = (
            select(
                func.count(MemorySnapshot.id),
                func.sum(case((MemorySnapshot.consistency_state == MemoryConsistencyState.DIVERGED.value, 1), else_=0)),
            )
            .join(AgentSession, MemorySnapshot.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*filters)
        )
        summary = (await session.execute(summary_q)).one()
        snapshot_count = int(summary[0] or 0)
        diverged_count = int(summary[1] or 0)

        hotspot_q = (
            select(
                MemorySnapshot.memory_namespace,
                MemorySnapshot.memory_key,
                func.count(MemorySnapshot.id),
                func.avg(MemorySnapshot.divergence_score),
            )
            .join(AgentSession, MemorySnapshot.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*filters, MemorySnapshot.consistency_state == MemoryConsistencyState.DIVERGED.value)
            .group_by(MemorySnapshot.memory_namespace, MemorySnapshot.memory_key)
            .order_by(desc(func.count(MemorySnapshot.id)))
            .limit(20)
        )
        hotspots = [
            {
                "memory_namespace": row[0],
                "memory_key": row[1],
                "diverged_count": int(row[2] or 0),
                "avg_divergence_score": float(row[3] or 0.0),
            }
            for row in (await session.execute(hotspot_q)).all()
        ]

        recent_q = (
            select(MemorySnapshot)
            .join(AgentSession, MemorySnapshot.session_id == AgentSession.id)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(*filters)
            .order_by(desc(MemorySnapshot.observed_at))
            .limit(50)
        )
        recent_rows = list((await session.execute(recent_q)).scalars().all())
        recent = [
            {
                "id": str(row.id),
                "session_id": str(row.session_id),
                "trace_id": str(row.trace_id) if row.trace_id else None,
                "memory_namespace": row.memory_namespace,
                "memory_key": row.memory_key,
                "consistency_state": _enum_str(row.consistency_state),
                "divergence_score": row.divergence_score,
                "observed_at": _to_iso(row.observed_at),
            }
            for row in recent_rows
        ]

        return MemoryConsistencyResponse(
            summary={
                "snapshot_count": snapshot_count,
                "diverged_count": diverged_count,
                "divergence_rate": (diverged_count / snapshot_count) if snapshot_count > 0 else 0.0,
            },
            hotspots=hotspots,
            recent=recent,
        )


@router.get("/chains/{trace_id}", response_model=DelegationChainResponse)
async def get_delegation_chain(
    trace_id: UUID,
    storage: StorageDep,
    auth: AuthDep,
) -> DelegationChainResponse:
    _require_viewer(auth)
    async with storage.session_factory() as session:
        trace = await session.get(AITrace, trace_id)
        if trace and trace.org_id:
            _enforce_org_scope(auth, trace.org_id)
        edge_q = select(DelegationEdge).where(DelegationEdge.trace_id == trace_id).order_by(
            DelegationEdge.started_at.asc()
        )
        edges = list((await session.execute(edge_q)).scalars().all())

        session_ids: set[UUID] = set()
        for edge in edges:
            session_ids.add(edge.parent_session_id)
            session_ids.add(edge.child_session_id)

        if not session_ids:
            if trace and trace.session_id:
                session_ids.add(trace.session_id)

        sessions_by_id: dict[UUID, AgentSession] = {}
        if session_ids:
            session_q = select(AgentSession).where(AgentSession.id.in_(session_ids))
            sessions = list((await session.execute(session_q)).scalars().all())
            sessions_by_id = {row.id: row for row in sessions}

        node_list: list[dict[str, Any]] = []
        for row in sessions_by_id.values():
            node_list.append(
                {
                    "session_id": str(row.id),
                    "agent_id": row.agent_id,
                    "status": _enum_str(row.status),
                    "started_at": _to_iso(row.started_at),
                    "ended_at": _to_iso(row.ended_at),
                }
            )

        edge_list: list[dict[str, Any]] = []
        adjacency: dict[UUID, list[UUID]] = defaultdict(list)
        children: set[UUID] = set()

        for edge in edges:
            duration = edge.duration_ms
            if duration is None and edge.completed_at is not None:
                duration = int((edge.completed_at - edge.started_at).total_seconds() * 1000)

            edge_list.append(
                {
                    "delegation_id": str(edge.id),
                    "parent_session_id": str(edge.parent_session_id),
                    "child_session_id": str(edge.child_session_id),
                    "status": _enum_str(edge.status),
                    "requested_capabilities": edge.requested_capabilities or [],
                    "duration_ms": duration,
                    "started_at": _to_iso(edge.started_at),
                    "completed_at": _to_iso(edge.completed_at),
                }
            )
            adjacency[edge.parent_session_id].append(edge.child_session_id)
            children.add(edge.child_session_id)

        roots = [node for node in adjacency.keys() if node not in children]
        if not roots and adjacency:
            roots = [next(iter(adjacency.keys()))]

        def depth_from(node: UUID, seen: set[UUID]) -> int:
            if node in seen:
                return 0
            descendants = adjacency.get(node, [])
            if not descendants:
                return 1
            next_seen = set(seen)
            next_seen.add(node)
            return 1 + max(depth_from(child, next_seen) for child in descendants)

        depth = 0
        for root in roots:
            depth = max(depth, depth_from(root, set()))
        if depth == 0 and node_list:
            depth = 1

        return DelegationChainResponse(
            trace_id=str(trace_id),
            nodes=node_list,
            edges=edge_list,
            stats={
                "depth": depth,
                "total_nodes": len(node_list),
                "total_edges": len(edge_list),
                "failed_edges": sum(1 for edge in edges if edge.status == DelegationStatus.FAILED),
            },
        )


@router.get("/dashboard/ui", response_class=HTMLResponse)
async def dashboard_ui(auth: AuthDep) -> HTMLResponse:
    """Simple built-in dashboard for runtime observability inspection."""
    _require_viewer(auth)
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Trace Observability Console</title>
  <style>
    :root {
      --bg: #f3f6f8;
      --panel: #ffffff;
      --ink: #12202f;
      --muted: #5b6b7c;
      --primary: #0f766e;
      --critical: #b91c1c;
      --warning: #b45309;
      --ok: #166534;
      --border: #d0dae4;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Source Sans Pro", "Segoe UI", sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at 10% 10%, #e4f4f2, #f3f6f8 35%),
                  radial-gradient(circle at 90% 0%, #e7edf7, #f3f6f8 40%);
      min-height: 100vh;
    }
    .wrap { max-width: 1200px; margin: 24px auto; padding: 0 16px 24px; }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    .title h1 { margin: 0; font-size: 26px; letter-spacing: 0.02em; }
    .title p { margin: 2px 0 0; color: var(--muted); }
    .controls {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    input, button {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 14px;
    }
    input { background: #fff; min-width: 220px; }
    button {
      background: var(--primary);
      border-color: var(--primary);
      color: #fff;
      font-weight: 600;
      cursor: pointer;
    }
    button.secondary {
      background: #fff;
      color: var(--ink);
      border-color: var(--border);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 12px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 1px 2px rgba(18, 32, 47, 0.06);
    }
    .card h3 {
      margin: 0 0 10px;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }
    .metric {
      font-size: 30px;
      font-weight: 700;
      line-height: 1;
    }
    .row {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 12px;
      margin-bottom: 12px;
    }
    .list { margin: 0; padding: 0; list-style: none; }
    .list li {
      padding: 8px 0;
      border-top: 1px solid #edf1f5;
      font-size: 14px;
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    .list li:first-child { border-top: none; padding-top: 0; }
    .tag {
      display: inline-block;
      font-size: 11px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 999px;
      background: #e4f4f2;
      color: #0f5f58;
    }
    .critical { color: var(--critical); font-weight: 700; }
    .warning { color: var(--warning); font-weight: 700; }
    .ok { color: var(--ok); font-weight: 700; }
    .muted { color: var(--muted); }
    pre {
      background: #0f1720;
      color: #d4deea;
      padding: 12px;
      border-radius: 12px;
      overflow-x: auto;
      margin: 0;
      font-size: 12px;
      min-height: 110px;
    }
    @media (max-width: 1000px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .row { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
      .grid { grid-template-columns: 1fr; }
      .metric { font-size: 26px; }
      input { min-width: 100%; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="title">
        <h1>AI Trace Runtime Console</h1>
        <p>Fleet, sessions, anomalies, and budget control loop in one view</p>
      </div>
      <div class="controls">
        <input id="orgId" placeholder="org_id (required)" />
        <button id="refreshBtn">Refresh</button>
        <button id="detectBtn" class="secondary">Run Detectors</button>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Active Sessions</h3>
        <div id="activeSessions" class="metric">-</div>
      </div>
      <div class="card">
        <h3>Actions (Window)</h3>
        <div id="actionCount" class="metric">-</div>
      </div>
      <div class="card">
        <h3>Error Rate</h3>
        <div id="errorRate" class="metric">-</div>
      </div>
      <div class="card">
        <h3>Cost (USD)</h3>
        <div id="totalCost" class="metric">-</div>
      </div>
    </div>

    <div class="row">
      <div class="card">
        <h3>Top Agents</h3>
        <ul id="topAgents" class="list"></ul>
      </div>
      <div class="card">
        <h3>Anomalies (Last 24h)</h3>
        <ul id="anomalyList" class="list"></ul>
      </div>
    </div>

    <div class="row">
      <div class="card">
        <h3>Budget Policies</h3>
        <ul id="budgetList" class="list"></ul>
      </div>
      <div class="card">
        <h3>Latest Detector Run</h3>
        <pre id="detectorResult">{}</pre>
      </div>
    </div>
  </div>

  <script>
    const byId = (id) => document.getElementById(id);
    let latestDetectorResult = {};

    function iso(ts) {
      return new Date(ts).toISOString();
    }

    function nowWindow(hours) {
      const end = new Date();
      const start = new Date(end.getTime() - (hours * 60 * 60 * 1000));
      return [iso(start), iso(end)];
    }

    function formatRate(v) {
      const pct = (v * 100).toFixed(2);
      return pct + "%";
    }

    function formatUsd(v) {
      return "$" + Number(v || 0).toFixed(4);
    }

    async function fetchJson(url, options) {
      const res = await fetch(url, options);
      if (!res.ok) {
        const txt = await res.text();
        throw new Error("HTTP " + res.status + " " + txt);
      }
      return res.json();
    }

    function renderTopAgents(items) {
      const el = byId("topAgents");
      el.innerHTML = "";
      if (!items.length) {
        el.innerHTML = '<li class="muted">No agent activity in window</li>';
        return;
      }
      for (const row of items) {
        const li = document.createElement("li");
        li.innerHTML =
          "<span>" + row.agent_id + "</span>" +
          "<span><span class='tag'>" + row.action_count + " actions</span> " + formatUsd(row.total_cost_usd) + "</span>";
        el.appendChild(li);
      }
    }

    function renderAnomalies(items) {
      const el = byId("anomalyList");
      el.innerHTML = "";
      if (!items.length) {
        el.innerHTML = '<li class="ok">No anomalies in selected window</li>';
        return;
      }
      for (const row of items.slice(0, 10)) {
        const li = document.createElement("li");
        const sevClass = row.severity === "critical" ? "critical" : (row.severity === "high" ? "warning" : "muted");
        li.innerHTML =
          "<span>" + row.title + "</span>" +
          "<span class='" + sevClass + "'>" + row.severity + "</span>";
        el.appendChild(li);
      }
    }

    function renderBudgets(items) {
      const el = byId("budgetList");
      el.innerHTML = "";
      if (!items.length) {
        el.innerHTML = '<li class="muted">No budget policies configured</li>';
        return;
      }
      for (const row of items.slice(0, 10)) {
        const util = row.utilization == null ? "n/a" : (row.utilization * 100).toFixed(1) + "%";
        const cls = row.utilization != null && row.utilization >= 1 ? "critical" : "ok";
        const li = document.createElement("li");
        li.innerHTML =
          "<span>" + row.policy_name + "</span>" +
          "<span class='" + cls + "'>" + util + "</span>";
        el.appendChild(li);
      }
    }

    async function refresh() {
      const orgId = byId("orgId").value.trim();
      if (!orgId) {
        return;
      }
      const [fromTs, toTs] = nowWindow(24);
      const params = new URLSearchParams({ org_id: orgId, from: fromTs, to: toTs, granularity: "5m" });
      const fleet = await fetchJson("/api/v1/observability/dashboard/fleet?" + params.toString());
      const anomalies = await fetchJson("/api/v1/observability/anomalies?" + new URLSearchParams({
        org_id: orgId, from: fromTs, to: toTs
      }).toString());
      const costs = await fetchJson("/api/v1/observability/costs/summary?" + new URLSearchParams({
        org_id: orgId, from: fromTs, to: toTs
      }).toString());

      byId("activeSessions").textContent = fleet.totals.active_sessions;
      byId("actionCount").textContent = fleet.totals.action_count;
      byId("errorRate").textContent = formatRate(fleet.totals.error_rate);
      byId("totalCost").textContent = formatUsd(fleet.totals.total_cost_usd);

      renderTopAgents(fleet.top_agents || []);
      renderAnomalies(anomalies.anomalies || []);
      renderBudgets(costs.budgets || []);
      byId("detectorResult").textContent = JSON.stringify(latestDetectorResult, null, 2);
    }

    async function runDetectors() {
      const orgId = byId("orgId").value.trim();
      if (!orgId) {
        return;
      }
      latestDetectorResult = await fetchJson("/api/v1/observability/detectors/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: orgId })
      });
      await refresh();
    }

    byId("refreshBtn").addEventListener("click", () => refresh().catch((e) => alert(e.message)));
    byId("detectBtn").addEventListener("click", () => runDetectors().catch((e) => alert(e.message)));
    setInterval(() => refresh().catch(() => {}), 15000);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)
