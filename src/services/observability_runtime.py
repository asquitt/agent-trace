"""Runtime policy evaluation, control-request, and anomaly detector services."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.observability import (
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
    MemoryConsistencyState,
    MemorySnapshot,
    PolicyActionApproval,
    PolicyActionType,
    PolicyApprovalStatus,
    PolicyStatus,
    SessionStatus,
)
from ..utils.time import to_naive_utc, utc_now_naive


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _action_priority(action: str) -> int:
    return {
        PolicyActionType.ALERT.value: 1,
        PolicyActionType.THROTTLE.value: 2,
        PolicyActionType.REQUIRE_APPROVAL.value: 3,
        PolicyActionType.SHUTDOWN.value: 4,
    }.get(action, 0)


def ensure_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize datetimes for DB columns stored without timezone."""
    return to_naive_utc(value)


def _severity_rank(severity: AnomalySeverity | str) -> int:
    severity_value = _enum_value(severity)
    return {
        AnomalySeverity.LOW.value: 1,
        AnomalySeverity.MEDIUM.value: 2,
        AnomalySeverity.HIGH.value: 3,
        AnomalySeverity.CRITICAL.value: 4,
    }.get(severity_value, 0)


@dataclass(frozen=True)
class DetectorConfig:
    """Tuning values for anomaly detectors."""

    current_window_minutes: int = 15
    baseline_window_hours: int = 24
    api_spike_multiplier: float = 10.0
    cost_spike_multiplier: float = 5.0
    unusual_resource_min_calls: int = 3
    memory_divergence_threshold: float = 0.30
    anomaly_dedupe_window_minutes: int = 30
    anomaly_reopen_acknowledged: bool = True


def _period_window_start(period: BudgetPeriodType, as_of: datetime) -> datetime:
    period_value = _enum_value(period)
    if period_value == BudgetPeriodType.HOUR.value:
        return as_of.replace(minute=0, second=0, microsecond=0)
    if period_value == BudgetPeriodType.DAY.value:
        return as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    return as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _scope_session_filters(policy: BudgetPolicy, org_id: str) -> list[Any]:
    filters: list[Any] = [AgentDeployment.org_id == org_id]
    scope_type = _enum_value(policy.scope_type)
    if scope_type == BudgetScopeType.DEPLOYMENT.value and policy.deployment_id is not None:
        filters.append(AgentSession.deployment_id == policy.deployment_id)
    if scope_type == BudgetScopeType.AGENT.value and policy.agent_id:
        filters.append(AgentSession.agent_id == policy.agent_id)
    return filters


def _scope_action_filters(
    policy: BudgetPolicy,
    org_id: str,
    period_start: datetime,
    as_of: datetime,
) -> list[Any]:
    filters: list[Any] = [
        AgentAction.occurred_at >= period_start,
        AgentAction.occurred_at <= as_of,
        AgentDeployment.org_id == org_id,
    ]
    scope_type = _enum_value(policy.scope_type)
    if scope_type == BudgetScopeType.DEPLOYMENT.value and policy.deployment_id is not None:
        filters.append(AgentSession.deployment_id == policy.deployment_id)
    if scope_type == BudgetScopeType.AGENT.value and policy.agent_id:
        filters.append(AgentSession.agent_id == policy.agent_id)
    return filters


async def _find_target_sessions(
    db: AsyncSession,
    policy: BudgetPolicy,
    org_id: str,
) -> list[AgentSession]:
    statuses = [SessionStatus.ACTIVE.value, SessionStatus.IDLE.value]
    query = (
        select(AgentSession)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentSession.status.in_(statuses),
            *_scope_session_filters(policy, org_id),
        )
    )
    return list((await db.execute(query)).scalars().all())


def _session_minutes_in_window(
    started_at: datetime,
    ended_at: Optional[datetime],
    window_start: datetime,
    as_of: datetime,
) -> float:
    effective_start = max(started_at, window_start)
    effective_end = min(ended_at or as_of, as_of)
    if effective_end <= effective_start:
        return 0.0
    return (effective_end - effective_start).total_seconds() / 60.0


def _merge_session_metadata(
    session_row: AgentSession,
    action: str,
    policy: BudgetPolicy,
    now: datetime,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    metadata = dict(session_row.session_metadata or {})
    control_meta = dict(metadata.get("control", {}))
    control_meta["state"] = "requested"
    control_meta["requested_action"] = action
    control_meta["delivery_status"] = "pending_runtime_adapter"
    control_meta["execution_confirmed"] = False
    control_meta["priority"] = _action_priority(action)
    control_meta["policy_id"] = str(policy.id)
    control_meta["policy_name"] = policy.policy_name
    control_meta["updated_at"] = now.isoformat()
    if policy.throttle_rate is not None:
        control_meta["throttle_rate"] = policy.throttle_rate
    if extra:
        control_meta.update(extra)
    metadata["control"] = control_meta
    session_row.session_metadata = metadata


def _policy_action_name(action: str) -> str:
    return {
        PolicyActionType.ALERT.value: "policy_alert",
        PolicyActionType.THROTTLE.value: "policy_throttle_requested",
        PolicyActionType.REQUIRE_APPROVAL.value: "policy_require_approval_requested",
        PolicyActionType.SHUTDOWN.value: "policy_shutdown_requested",
    }[action]


async def _apply_policy_action(
    db: AsyncSession,
    policy: BudgetPolicy,
    org_id: str,
    now: datetime,
    breaches: list[dict[str, Any]],
    execute_actions: bool,
    require_shutdown_approval: bool,
    approval_max_age_minutes: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "policy_id": str(policy.id),
        "policy_name": policy.policy_name,
        "action": _enum_value(policy.action_on_breach),
        "status": "noop",
        "affected_sessions": 0,
        "affected_session_ids": [],
        "skipped_session_ids": [],
        "approval_required": False,
        "approval_id": None,
        "execution_confirmed": False,
        "delivery_status": None,
    }
    if not execute_actions:
        result["status"] = "dry_run"
        return result

    sessions = await _find_target_sessions(db, policy, org_id)
    affected_ids: list[str] = []
    skipped_ids: list[str] = []

    policy_action = _enum_value(policy.action_on_breach)
    if policy_action == PolicyActionType.SHUTDOWN.value and require_shutdown_approval:
        approval_cutoff = now - timedelta(minutes=max(approval_max_age_minutes, 1))
        approval_query = (
            select(PolicyActionApproval)
            .where(
                PolicyActionApproval.org_id == org_id,
                PolicyActionApproval.policy_id == policy.id,
                PolicyActionApproval.action_type == PolicyActionType.SHUTDOWN.value,
                PolicyActionApproval.status == PolicyApprovalStatus.APPROVED.value,
                PolicyActionApproval.requested_at >= approval_cutoff,
                or_(
                    PolicyActionApproval.expires_at.is_(None),
                    PolicyActionApproval.expires_at >= now,
                ),
            )
            .order_by(desc(PolicyActionApproval.requested_at))
            .limit(1)
        )
        approval = (await db.execute(approval_query)).scalar_one_or_none()
        if approval is None:
            result["status"] = "approval_required"
            result["approval_required"] = True
            return result
        result["approval_id"] = str(approval.id)

    policy_priority = _action_priority(policy_action)
    for session_row in sessions:
        existing_control = dict((session_row.session_metadata or {}).get("control", {}))
        existing_priority = int(existing_control.get("priority") or 0)
        if policy_action != PolicyActionType.ALERT.value and existing_priority > policy_priority:
            skipped_ids.append(str(session_row.id))
            continue

        metadata_extra = {"breaches": breaches}
        if policy_action in {
            PolicyActionType.SHUTDOWN.value,
            PolicyActionType.THROTTLE.value,
        }:
            _merge_session_metadata(
                session_row,
                policy_action,
                policy,
                now,
                extra=metadata_extra,
            )
            affected_ids.append(str(session_row.id))
        elif policy_action == PolicyActionType.REQUIRE_APPROVAL.value:
            _merge_session_metadata(
                session_row,
                policy_action,
                policy,
                now,
                extra={"requires_human_approval": True, "breaches": breaches},
            )
            affected_ids.append(str(session_row.id))

    policy_action_metadata = {
        "policy_id": str(policy.id),
        "policy_name": policy.policy_name,
        "breaches": breaches,
        "request_status": "persisted",
        "delivery_status": "pending_runtime_adapter",
        "execution_confirmed": False,
    }

    if policy_action != PolicyActionType.ALERT.value:
        for session_id in affected_ids:
            db.add(
                AgentAction(
                    session_id=UUID(session_id),
                    action_type=ActionType.POLICY_ACTION,
                    action_name=_policy_action_name(policy_action),
                    resource=f"policy:{policy.id}",
                    provider="ai-trace-control",
                    model="policy-engine-v1",
                    success=True,
                    occurred_at=now,
                    action_metadata=policy_action_metadata,
                )
            )
        result["status"] = "requested"
        result["delivery_status"] = "pending_runtime_adapter"

    if policy_action == PolicyActionType.ALERT.value:
        result["status"] = "alert_only"
    result["affected_sessions"] = len(affected_ids)
    result["affected_session_ids"] = affected_ids
    result["skipped_session_ids"] = skipped_ids
    return result


async def evaluate_budget_policies(
    db: AsyncSession,
    org_id: str,
    *,
    as_of: Optional[datetime] = None,
    execute_actions: bool = True,
    require_shutdown_approval: bool = False,
    approval_max_age_minutes: int = 60,
) -> dict[str, Any]:
    """Evaluate budget policies and persist configured control requests."""
    now = ensure_naive_utc(as_of) or utc_now_naive()
    policies_query = select(BudgetPolicy).where(
        BudgetPolicy.org_id == org_id,
        BudgetPolicy.status == PolicyStatus.ACTIVE.value,
    )
    policies = list((await db.execute(policies_query)).scalars().all())

    summary: dict[str, Any] = {
        "evaluated_at": now.isoformat(),
        "org_id": org_id,
        "evaluated_policies": len(policies),
        "breached_policies": 0,
        "events_created": 0,
        "actions_executed": 0,
        "actions_requested": 0,
        "alerts_triggered": 0,
        "results": [],
    }

    for policy in policies:
        window_start = _period_window_start(policy.period_type, now)
        action_filters = _scope_action_filters(policy, org_id, window_start, now)

        usage_query = (
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
        usage_row = (await db.execute(usage_query)).one()
        observed_cost = float(usage_row[0] or 0.0)
        observed_input = int(usage_row[1] or 0)
        observed_output = int(usage_row[2] or 0)
        observed_actions = int(usage_row[3] or 0)

        session_minutes = 0.0
        session_query = (
            select(AgentSession.started_at, AgentSession.ended_at)
            .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
            .where(
                AgentSession.started_at <= now,
                *_scope_session_filters(policy, org_id),
                or_(AgentSession.ended_at.is_(None), AgentSession.ended_at >= window_start),
            )
        )
        for row in (await db.execute(session_query)).all():
            session_minutes = max(
                session_minutes,
                _session_minutes_in_window(row[0], row[1], window_start, now),
            )

        breaches: list[dict[str, Any]] = []
        if policy.max_cost_usd is not None and observed_cost > policy.max_cost_usd:
            breaches.append(
                {
                    "trigger_type": "max_cost_usd",
                    "observed_value": observed_cost,
                    "threshold_value": float(policy.max_cost_usd),
                }
            )
        if policy.max_input_tokens is not None and observed_input > policy.max_input_tokens:
            breaches.append(
                {
                    "trigger_type": "max_input_tokens",
                    "observed_value": float(observed_input),
                    "threshold_value": float(policy.max_input_tokens),
                }
            )
        if policy.max_output_tokens is not None and observed_output > policy.max_output_tokens:
            breaches.append(
                {
                    "trigger_type": "max_output_tokens",
                    "observed_value": float(observed_output),
                    "threshold_value": float(policy.max_output_tokens),
                }
            )
        if policy.max_actions is not None and observed_actions > policy.max_actions:
            breaches.append(
                {
                    "trigger_type": "max_actions",
                    "observed_value": float(observed_actions),
                    "threshold_value": float(policy.max_actions),
                }
            )
        if policy.max_session_minutes is not None and session_minutes > policy.max_session_minutes:
            breaches.append(
                {
                    "trigger_type": "max_session_minutes",
                    "observed_value": session_minutes,
                    "threshold_value": float(policy.max_session_minutes),
                }
            )

        policy_result: dict[str, Any] = {
            "policy_id": str(policy.id),
            "policy_name": policy.policy_name,
            "scope_type": _enum_value(policy.scope_type),
            "notification_targets": list(policy.notification_targets or []),
            "window_start": window_start.isoformat(),
            "window_end": now.isoformat(),
            "breaches": breaches,
            "usage": {
                "cost_usd": observed_cost,
                "input_tokens": observed_input,
                "output_tokens": observed_output,
                "action_count": observed_actions,
                "max_session_minutes": session_minutes,
            },
            "action_result": None,
            "cooldown": False,
        }

        if not breaches:
            summary["results"].append(policy_result)
            continue

        summary["breached_policies"] += 1

        latest_event_query = (
            select(BudgetPolicyEvent)
            .where(
                BudgetPolicyEvent.policy_id == policy.id,
                BudgetPolicyEvent.triggered_at <= now,
            )
            .order_by(desc(BudgetPolicyEvent.triggered_at))
            .limit(1)
        )
        latest_event = (await db.execute(latest_event_query)).scalar_one_or_none()
        in_cooldown = False
        if policy.cooldown_seconds and latest_event is not None:
            cooldown_until = latest_event.triggered_at + timedelta(seconds=policy.cooldown_seconds)
            in_cooldown = now < cooldown_until
            if in_cooldown:
                policy_result["cooldown"] = True
                policy_result["cooldown_until"] = cooldown_until.isoformat()

        if in_cooldown:
            summary["results"].append(policy_result)
            continue

        action_result = await _apply_policy_action(
            db,
            policy,
            org_id,
            now,
            breaches,
            execute_actions,
            require_shutdown_approval,
            approval_max_age_minutes,
        )
        policy_result["action_result"] = action_result
        if action_result["status"] == "requested":
            summary["actions_requested"] += 1
        elif action_result["status"] == "alert_only":
            summary["alerts_triggered"] += 1

        for breach in breaches:
            db.add(
                BudgetPolicyEvent(
                    policy_id=policy.id,
                    trigger_type=breach["trigger_type"],
                    triggered_at=now,
                    observed_value=breach["observed_value"],
                    threshold_value=breach["threshold_value"],
                    action_executed=policy.action_on_breach,
                    action_status=action_result["status"],
                    details={
                        "policy_name": policy.policy_name,
                        "action_result": action_result,
                        "window_start": window_start.isoformat(),
                        "window_end": now.isoformat(),
                    },
                )
            )
            summary["events_created"] += 1

        summary["results"].append(policy_result)

    return summary


async def _maybe_create_anomaly(
    db: AsyncSession,
    *,
    deployment_id: Optional[UUID],
    session_id: Optional[UUID],
    trace_id: Optional[UUID],
    action_id: Optional[UUID],
    anomaly_type: AnomalyType,
    severity: AnomalySeverity,
    detector_name: str,
    baseline_value: Optional[float],
    observed_value: Optional[float],
    deviation_ratio: Optional[float],
    score: Optional[float],
    title: str,
    description: str,
    detected_at: datetime,
    metadata: dict[str, Any],
    dedupe_window_minutes: int,
    reopen_acknowledged: bool,
) -> tuple[Optional[AnomalyEvent], bool]:
    dedupe_window_start = detected_at - timedelta(minutes=max(dedupe_window_minutes, 1))
    duplicate_q = select(AnomalyEvent).where(
        AnomalyEvent.anomaly_type == anomaly_type.value,
        AnomalyEvent.status.in_([AnomalyStatus.OPEN.value, AnomalyStatus.ACKNOWLEDGED.value]),
        AnomalyEvent.title == title,
        AnomalyEvent.detected_at >= dedupe_window_start,
    )
    if deployment_id is None:
        duplicate_q = duplicate_q.where(AnomalyEvent.deployment_id.is_(None))
    else:
        duplicate_q = duplicate_q.where(AnomalyEvent.deployment_id == deployment_id)
    if session_id is None:
        duplicate_q = duplicate_q.where(AnomalyEvent.session_id.is_(None))
    else:
        duplicate_q = duplicate_q.where(AnomalyEvent.session_id == session_id)

    duplicate = (await db.execute(duplicate_q.limit(1))).scalar_one_or_none()
    if duplicate:
        duplicate_metadata: dict[str, Any] = dict(duplicate.anomaly_metadata or {})
        detection_stats: dict[str, Any] = dict(duplicate_metadata.get("detection_stats") or {})
        existing_occurrences = int(detection_stats.get("occurrences") or 1)
        detection_stats["occurrences"] = existing_occurrences + 1
        detection_stats.setdefault("first_detected_at", duplicate.detected_at.isoformat())
        detection_stats["last_detected_at"] = detected_at.isoformat()
        detection_stats["last_detector_name"] = detector_name
        detection_stats["highest_severity"] = (
            _enum_value(severity)
            if _severity_rank(severity) > _severity_rank(duplicate.severity)
            else _enum_value(duplicate.severity)
        )
        if baseline_value is not None:
            detection_stats["latest_baseline_value"] = baseline_value
        if observed_value is not None:
            detection_stats["latest_observed_value"] = observed_value
        if deviation_ratio is not None:
            detection_stats["latest_deviation_ratio"] = deviation_ratio
        if score is not None:
            detection_stats["latest_score"] = score

        duplicate_metadata["detection_stats"] = detection_stats
        duplicate_metadata["latest_detection_metadata"] = metadata
        duplicate.anomaly_metadata = duplicate_metadata

        duplicate.detected_at = detected_at
        duplicate.detector_name = detector_name
        duplicate.baseline_value = baseline_value
        duplicate.observed_value = observed_value
        duplicate.description = description

        if deviation_ratio is not None:
            if duplicate.deviation_ratio is None:
                duplicate.deviation_ratio = deviation_ratio
            else:
                duplicate.deviation_ratio = max(float(duplicate.deviation_ratio), deviation_ratio)

        if score is not None:
            duplicate.score = max(float(duplicate.score or 0.0), score)

        if _severity_rank(severity) > _severity_rank(duplicate.severity):
            duplicate.severity = severity

        if reopen_acknowledged and _enum_value(duplicate.status) == AnomalyStatus.ACKNOWLEDGED.value:
            duplicate.status = AnomalyStatus.OPEN
            duplicate.updated_by = "system:detectors"
            duplicate.note = "Automatically reopened after repeated detector trigger."
            duplicate.resolved_at = None

        await db.flush()
        return duplicate, False

    detection_stats: dict[str, Any] = {
        "occurrences": 1,
        "first_detected_at": detected_at.isoformat(),
        "last_detected_at": detected_at.isoformat(),
        "last_detector_name": detector_name,
        "highest_severity": _enum_value(severity),
    }
    if baseline_value is not None:
        detection_stats["latest_baseline_value"] = baseline_value
    if observed_value is not None:
        detection_stats["latest_observed_value"] = observed_value
    if deviation_ratio is not None:
        detection_stats["latest_deviation_ratio"] = deviation_ratio
    if score is not None:
        detection_stats["latest_score"] = score

    anomaly_metadata = dict(metadata)
    anomaly_metadata["detection_stats"] = detection_stats
    anomaly = AnomalyEvent(
        deployment_id=deployment_id,
        session_id=session_id,
        trace_id=trace_id,
        action_id=action_id,
        anomaly_type=anomaly_type,
        severity=severity,
        status=AnomalyStatus.OPEN,
        detector_name=detector_name,
        baseline_value=baseline_value,
        observed_value=observed_value,
        deviation_ratio=deviation_ratio,
        score=score,
        title=title,
        description=description,
        detected_at=detected_at,
        anomaly_metadata=anomaly_metadata,
    )
    db.add(anomaly)
    await db.flush()
    return anomaly, True


def _severity_for_ratio(ratio: float) -> AnomalySeverity:
    if ratio >= 20:
        return AnomalySeverity.CRITICAL
    if ratio >= 10:
        return AnomalySeverity.HIGH
    return AnomalySeverity.MEDIUM


async def _detect_api_spikes(
    db: AsyncSession,
    org_id: str,
    as_of: datetime,
    current_start: datetime,
    baseline_start: datetime,
    config: DetectorConfig,
) -> dict[str, Any]:
    current_q = (
        select(
            AgentSession.deployment_id,
            AgentSession.agent_id,
            func.count(AgentAction.id),
        )
        .join(AgentSession, AgentAction.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            AgentAction.occurred_at >= current_start,
            AgentAction.occurred_at <= as_of,
        )
        .group_by(AgentSession.deployment_id, AgentSession.agent_id)
    )
    baseline_q = (
        select(
            AgentSession.deployment_id,
            AgentSession.agent_id,
            func.count(AgentAction.id),
        )
        .join(AgentSession, AgentAction.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            AgentAction.occurred_at >= baseline_start,
            AgentAction.occurred_at < current_start,
        )
        .group_by(AgentSession.deployment_id, AgentSession.agent_id)
    )
    current_rows = (await db.execute(current_q)).all()
    baseline_rows = (await db.execute(baseline_q)).all()
    windows = max(
        int((config.baseline_window_hours * 60) / max(config.current_window_minutes, 1)),
        1,
    )
    baseline_map = {(row[0], row[1]): int(row[2] or 0) for row in baseline_rows}

    created: list[str] = []
    deduplicated: list[str] = []
    scanned = len(current_rows)
    for row in current_rows:
        deployment_id = row[0]
        agent_id = row[1]
        current_count = int(row[2] or 0)
        baseline_total = baseline_map.get((deployment_id, agent_id), 0)
        baseline_avg = baseline_total / windows
        if baseline_avg < 1.0 or current_count < 3:
            continue
        ratio = current_count / baseline_avg
        if ratio < config.api_spike_multiplier:
            continue

        anomaly, was_created = await _maybe_create_anomaly(
            db,
            deployment_id=deployment_id,
            session_id=None,
            trace_id=None,
            action_id=None,
            anomaly_type=AnomalyType.API_SPIKE,
            severity=_severity_for_ratio(ratio),
            detector_name="runtime-api-spike-v1",
            baseline_value=baseline_avg,
            observed_value=float(current_count),
            deviation_ratio=ratio,
            score=min(ratio / config.api_spike_multiplier, 5.0),
            title=f"API call spike for agent {agent_id}",
            description=(
                f"Current window action count {current_count} is {ratio:.2f}x baseline "
                f"average {baseline_avg:.2f}."
            ),
            detected_at=as_of,
            metadata={
                "agent_id": agent_id,
                "current_window_minutes": config.current_window_minutes,
                "baseline_window_hours": config.baseline_window_hours,
                "baseline_total": baseline_total,
            },
            dedupe_window_minutes=config.anomaly_dedupe_window_minutes,
            reopen_acknowledged=config.anomaly_reopen_acknowledged,
        )
        if anomaly is not None and was_created:
            created.append(str(anomaly.id))
        elif anomaly is not None:
            deduplicated.append(str(anomaly.id))

    return {
        "detector": "api_spike",
        "scanned": scanned,
        "created_ids": created,
        "deduplicated_ids": deduplicated,
        "deduplicated_count": len(deduplicated),
    }


async def _detect_cost_spikes(
    db: AsyncSession,
    org_id: str,
    as_of: datetime,
    current_start: datetime,
    baseline_start: datetime,
    config: DetectorConfig,
) -> dict[str, Any]:
    current_q = (
        select(
            AgentSession.deployment_id,
            AgentSession.agent_id,
            func.sum(AgentAction.estimated_cost_usd),
        )
        .join(AgentSession, AgentAction.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            AgentAction.occurred_at >= current_start,
            AgentAction.occurred_at <= as_of,
        )
        .group_by(AgentSession.deployment_id, AgentSession.agent_id)
    )
    baseline_q = (
        select(
            AgentSession.deployment_id,
            AgentSession.agent_id,
            func.sum(AgentAction.estimated_cost_usd),
        )
        .join(AgentSession, AgentAction.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            AgentAction.occurred_at >= baseline_start,
            AgentAction.occurred_at < current_start,
        )
        .group_by(AgentSession.deployment_id, AgentSession.agent_id)
    )
    current_rows = (await db.execute(current_q)).all()
    baseline_rows = (await db.execute(baseline_q)).all()
    windows = max(
        int((config.baseline_window_hours * 60) / max(config.current_window_minutes, 1)),
        1,
    )
    baseline_map = {(row[0], row[1]): float(row[2] or 0.0) for row in baseline_rows}

    created: list[str] = []
    deduplicated: list[str] = []
    scanned = len(current_rows)
    for row in current_rows:
        deployment_id = row[0]
        agent_id = row[1]
        current_cost = float(row[2] or 0.0)
        baseline_total = baseline_map.get((deployment_id, agent_id), 0.0)
        baseline_avg = baseline_total / windows
        if baseline_avg < 0.01 or current_cost < 0.01:
            continue
        ratio = current_cost / baseline_avg
        if ratio < config.cost_spike_multiplier:
            continue

        anomaly, was_created = await _maybe_create_anomaly(
            db,
            deployment_id=deployment_id,
            session_id=None,
            trace_id=None,
            action_id=None,
            anomaly_type=AnomalyType.COST_SPIKE,
            severity=_severity_for_ratio(ratio),
            detector_name="runtime-cost-spike-v1",
            baseline_value=baseline_avg,
            observed_value=current_cost,
            deviation_ratio=ratio,
            score=min(ratio / config.cost_spike_multiplier, 5.0),
            title=f"Cost spike for agent {agent_id}",
            description=(
                f"Current window cost ${current_cost:.4f} is {ratio:.2f}x "
                f"baseline average ${baseline_avg:.4f}."
            ),
            detected_at=as_of,
            metadata={
                "agent_id": agent_id,
                "current_window_minutes": config.current_window_minutes,
                "baseline_window_hours": config.baseline_window_hours,
                "baseline_total_cost": baseline_total,
            },
            dedupe_window_minutes=config.anomaly_dedupe_window_minutes,
            reopen_acknowledged=config.anomaly_reopen_acknowledged,
        )
        if anomaly is not None and was_created:
            created.append(str(anomaly.id))
        elif anomaly is not None:
            deduplicated.append(str(anomaly.id))

    return {
        "detector": "cost_spike",
        "scanned": scanned,
        "created_ids": created,
        "deduplicated_ids": deduplicated,
        "deduplicated_count": len(deduplicated),
    }


async def _detect_unusual_resources(
    db: AsyncSession,
    org_id: str,
    as_of: datetime,
    current_start: datetime,
    baseline_start: datetime,
    config: DetectorConfig,
) -> dict[str, Any]:
    baseline_q = (
        select(
            AgentSession.deployment_id,
            AgentSession.agent_id,
            AgentAction.resource,
        )
        .join(AgentSession, AgentAction.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            AgentAction.occurred_at >= baseline_start,
            AgentAction.occurred_at < current_start,
            AgentAction.resource.is_not(None),
        )
        .distinct()
    )
    current_q = (
        select(
            AgentSession.deployment_id,
            AgentSession.agent_id,
            AgentAction.resource,
            func.count(AgentAction.id),
        )
        .join(AgentSession, AgentAction.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            AgentAction.occurred_at >= current_start,
            AgentAction.occurred_at <= as_of,
            AgentAction.resource.is_not(None),
        )
        .group_by(AgentSession.deployment_id, AgentSession.agent_id, AgentAction.resource)
    )
    baseline_rows = (await db.execute(baseline_q)).all()
    current_rows = (await db.execute(current_q)).all()

    baseline_set: set[tuple[UUID, str, str]] = set()
    for row in baseline_rows:
        baseline_set.add((row[0], row[1], row[2]))

    created: list[str] = []
    deduplicated: list[str] = []
    scanned = len(current_rows)
    for row in current_rows:
        deployment_id = row[0]
        agent_id = row[1]
        resource = row[2]
        resource_count = int(row[3] or 0)
        if (deployment_id, agent_id, resource) in baseline_set:
            continue
        if resource_count < config.unusual_resource_min_calls:
            continue

        anomaly, was_created = await _maybe_create_anomaly(
            db,
            deployment_id=deployment_id,
            session_id=None,
            trace_id=None,
            action_id=None,
            anomaly_type=AnomalyType.UNUSUAL_RESOURCE_ACCESS,
            severity=AnomalySeverity.HIGH,
            detector_name="runtime-unusual-resource-v1",
            baseline_value=0.0,
            observed_value=float(resource_count),
            deviation_ratio=None,
            score=1.0,
            title=f"Unusual resource access by agent {agent_id}",
            description=(
                f"Resource {resource} was accessed {resource_count} times in the current "
                "window but was not present in baseline behavior."
            ),
            detected_at=as_of,
            metadata={
                "agent_id": agent_id,
                "resource": resource,
                "current_window_minutes": config.current_window_minutes,
                "baseline_window_hours": config.baseline_window_hours,
            },
            dedupe_window_minutes=config.anomaly_dedupe_window_minutes,
            reopen_acknowledged=config.anomaly_reopen_acknowledged,
        )
        if anomaly is not None and was_created:
            created.append(str(anomaly.id))
        elif anomaly is not None:
            deduplicated.append(str(anomaly.id))

    return {
        "detector": "unusual_resource_access",
        "scanned": scanned,
        "created_ids": created,
        "deduplicated_ids": deduplicated,
        "deduplicated_count": len(deduplicated),
    }


async def _detect_memory_divergence(
    db: AsyncSession,
    org_id: str,
    as_of: datetime,
    current_start: datetime,
    baseline_start: datetime,
    config: DetectorConfig,
) -> dict[str, Any]:
    current_q = (
        select(
            AgentSession.deployment_id,
            func.count(MemorySnapshot.id),
            func.sum(
                case(
                    (
                        MemorySnapshot.consistency_state
                        == MemoryConsistencyState.DIVERGED.value,
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .join(AgentSession, MemorySnapshot.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            MemorySnapshot.observed_at >= current_start,
            MemorySnapshot.observed_at <= as_of,
        )
        .group_by(AgentSession.deployment_id)
    )
    baseline_q = (
        select(
            AgentSession.deployment_id,
            func.count(MemorySnapshot.id),
            func.sum(
                case(
                    (
                        MemorySnapshot.consistency_state
                        == MemoryConsistencyState.DIVERGED.value,
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .join(AgentSession, MemorySnapshot.session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            MemorySnapshot.observed_at >= baseline_start,
            MemorySnapshot.observed_at < current_start,
        )
        .group_by(AgentSession.deployment_id)
    )
    current_rows = (await db.execute(current_q)).all()
    baseline_rows = (await db.execute(baseline_q)).all()
    baseline_map = {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in baseline_rows}

    created: list[str] = []
    deduplicated: list[str] = []
    scanned = len(current_rows)
    for row in current_rows:
        deployment_id = row[0]
        current_total = int(row[1] or 0)
        current_diverged = int(row[2] or 0)
        if current_total < 5:
            continue
        current_rate = current_diverged / current_total

        baseline_total, baseline_diverged = baseline_map.get(deployment_id, (0, 0))
        baseline_rate = (baseline_diverged / baseline_total) if baseline_total > 0 else 0.0

        if current_rate < config.memory_divergence_threshold:
            continue
        if current_rate <= baseline_rate + 0.10:
            continue

        severity = AnomalySeverity.CRITICAL if current_rate >= 0.60 else AnomalySeverity.HIGH
        anomaly, was_created = await _maybe_create_anomaly(
            db,
            deployment_id=deployment_id,
            session_id=None,
            trace_id=None,
            action_id=None,
            anomaly_type=AnomalyType.MEMORY_DIVERGENCE,
            severity=severity,
            detector_name="runtime-memory-divergence-v1",
            baseline_value=baseline_rate,
            observed_value=current_rate,
            deviation_ratio=(current_rate / baseline_rate) if baseline_rate > 0 else None,
            score=current_rate,
            title="Memory divergence rate anomaly",
            description=(
                f"Current memory divergence rate {current_rate:.2%} exceeds baseline "
                f"{baseline_rate:.2%}."
            ),
            detected_at=as_of,
            metadata={
                "current_diverged": current_diverged,
                "current_total": current_total,
                "baseline_diverged": baseline_diverged,
                "baseline_total": baseline_total,
            },
            dedupe_window_minutes=config.anomaly_dedupe_window_minutes,
            reopen_acknowledged=config.anomaly_reopen_acknowledged,
        )
        if anomaly is not None and was_created:
            created.append(str(anomaly.id))
        elif anomaly is not None:
            deduplicated.append(str(anomaly.id))

    return {
        "detector": "memory_divergence",
        "scanned": scanned,
        "created_ids": created,
        "deduplicated_ids": deduplicated,
        "deduplicated_count": len(deduplicated),
    }


def _find_cycles(edges: list[tuple[UUID, UUID]]) -> list[list[UUID]]:
    adjacency: dict[UUID, list[UUID]] = defaultdict(list)
    for parent_id, child_id in edges:
        adjacency[parent_id].append(child_id)

    cycles: list[list[UUID]] = []
    visiting: set[UUID] = set()
    visited: set[UUID] = set()
    stack: list[UUID] = []

    def dfs(node: UUID) -> None:
        if node in visiting:
            if node in stack:
                idx = stack.index(node)
                cycles.append([*stack[idx:], node])
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for neighbor in adjacency.get(node, []):
            dfs(neighbor)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in adjacency:
        dfs(node)
    return cycles


async def _detect_delegation_loops(
    db: AsyncSession,
    org_id: str,
    as_of: datetime,
    current_start: datetime,
    config: DetectorConfig,
) -> dict[str, Any]:
    edges_q = (
        select(
            DelegationEdge.parent_session_id,
            DelegationEdge.child_session_id,
            AgentSession.deployment_id,
        )
        .join(AgentSession, DelegationEdge.parent_session_id == AgentSession.id)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentDeployment.org_id == org_id,
            DelegationEdge.started_at >= current_start,
            DelegationEdge.started_at <= as_of,
        )
    )
    rows = (await db.execute(edges_q)).all()
    edges_by_deployment: dict[UUID, list[tuple[UUID, UUID]]] = defaultdict(list)
    for row in rows:
        edges_by_deployment[row[2]].append((row[0], row[1]))

    created: list[str] = []
    deduplicated: list[str] = []
    scanned = len(rows)
    for deployment_id, edges in edges_by_deployment.items():
        cycles = _find_cycles(edges)
        if not cycles:
            continue

        anomaly, was_created = await _maybe_create_anomaly(
            db,
            deployment_id=deployment_id,
            session_id=None,
            trace_id=None,
            action_id=None,
            anomaly_type=AnomalyType.DELEGATION_LOOP,
            severity=AnomalySeverity.CRITICAL,
            detector_name="runtime-delegation-loop-v1",
            baseline_value=0.0,
            observed_value=float(len(cycles)),
            deviation_ratio=None,
            score=min(float(len(cycles)), 10.0),
            title="Delegation loop detected",
            description=f"Detected {len(cycles)} delegation cycle(s) in current window.",
            detected_at=as_of,
            metadata={
                "cycles": [[str(node) for node in cycle] for cycle in cycles[:5]],
                "cycle_count": len(cycles),
            },
            dedupe_window_minutes=config.anomaly_dedupe_window_minutes,
            reopen_acknowledged=config.anomaly_reopen_acknowledged,
        )
        if anomaly is not None and was_created:
            created.append(str(anomaly.id))
        elif anomaly is not None:
            deduplicated.append(str(anomaly.id))

    return {
        "detector": "delegation_loop",
        "scanned": scanned,
        "created_ids": created,
        "deduplicated_ids": deduplicated,
        "deduplicated_count": len(deduplicated),
    }


async def run_anomaly_detectors(
    db: AsyncSession,
    org_id: str,
    *,
    as_of: Optional[datetime] = None,
    config: Optional[DetectorConfig] = None,
) -> dict[str, Any]:
    """Run all built-in anomaly detectors and persist findings."""
    run_config = config or DetectorConfig()
    now = ensure_naive_utc(as_of) or utc_now_naive()
    current_start = now - timedelta(minutes=run_config.current_window_minutes)
    baseline_start = current_start - timedelta(hours=run_config.baseline_window_hours)

    detector_results = [
        await _detect_api_spikes(db, org_id, now, current_start, baseline_start, run_config),
        await _detect_cost_spikes(db, org_id, now, current_start, baseline_start, run_config),
        await _detect_unusual_resources(db, org_id, now, current_start, baseline_start, run_config),
        await _detect_memory_divergence(db, org_id, now, current_start, baseline_start, run_config),
        await _detect_delegation_loops(db, org_id, now, current_start, run_config),
    ]

    created_ids: list[str] = []
    deduplicated_ids: list[str] = []
    for result in detector_results:
        created_ids.extend(result["created_ids"])
        deduplicated_ids.extend(result["deduplicated_ids"])

    return {
        "run_at": now.isoformat(),
        "org_id": org_id,
        "window": {
            "current_start": current_start.isoformat(),
            "current_end": now.isoformat(),
            "baseline_start": baseline_start.isoformat(),
            "baseline_end": current_start.isoformat(),
        },
        "detectors": detector_results,
        "created_anomaly_ids": created_ids,
        "created_anomalies": len(created_ids),
        "deduplicated_anomaly_ids": deduplicated_ids,
        "deduplicated_anomalies": len(deduplicated_ids),
    }
