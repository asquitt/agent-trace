"""Durable runtime-control delivery and acknowledgement."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.observability import (
    AgentDeployment,
    AgentSession,
    PolicyActionApproval,
    PolicyApprovalStatus,
    RuntimeControlRequest,
    RuntimeControlStatus,
    SessionStatus,
    SystemAuditEvent,
)
from ..utils.time import utc_now_naive


class RuntimeControlNotFoundError(Exception):
    """The requested runtime-control scope does not exist."""


class RuntimeControlConflictError(Exception):
    """The runtime-control operation conflicts with durable state."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_payload_hash(*, outcome: str, details: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"outcome": outcome, "details": details},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return _sha256(encoded)


def runtime_control_idempotency_key(
    *,
    policy_id: UUID,
    session_id: UUID,
    action_type: str,
    period_window_start: datetime,
    policy_updated_at: datetime | None,
    throttle_rate: float | None,
    approval_id: UUID | None,
) -> str:
    """Build the stable producer identity for one policy/session/window command."""
    encoded = json.dumps(
        {
            "policy_id": str(policy_id),
            "session_id": str(session_id),
            "action_type": action_type,
            "period_window_start": period_window_start.isoformat(),
            "policy_updated_at": policy_updated_at.isoformat() if policy_updated_at else None,
            "throttle_rate": throttle_rate,
            "approval_id": str(approval_id) if approval_id else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(encoded)


def _audit_event(
    *,
    actor_subject: str,
    actor_roles: list[str],
    org_id: str,
    action: str,
    control: RuntimeControlRequest,
    success: bool = True,
    details: dict[str, Any] | None = None,
    occurred_at: datetime,
) -> SystemAuditEvent:
    return SystemAuditEvent(
        occurred_at=occurred_at,
        actor_subject=actor_subject,
        actor_roles=actor_roles,
        org_id=org_id,
        action=action,
        resource_type="runtime_control_request",
        resource_id=str(control.id),
        success=success,
        details=details or {},
    )


def serialize_runtime_control(
    control: RuntimeControlRequest,
    *,
    lease_token: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(control.id),
        "org_id": control.org_id,
        "deployment_id": str(control.deployment_id),
        "session_id": str(control.session_id),
        "action_type": control.action_type,
        "payload": control.payload,
        "status": control.status,
        "delivery_attempts": control.delivery_attempts,
        "lease_token": lease_token,
        "lease_expires_at": (
            control.lease_expires_at.isoformat() if control.lease_expires_at else None
        ),
        "acknowledged_at": (
            control.acknowledged_at.isoformat() if control.acknowledged_at else None
        ),
    }


async def _validate_runtime_scope(
    db: AsyncSession,
    *,
    org_id: str,
    deployment_id: UUID,
    session_id: UUID,
    runtime_instance_id: str,
) -> AgentSession:
    query = (
        select(AgentSession, AgentDeployment)
        .join(AgentDeployment, AgentSession.deployment_id == AgentDeployment.id)
        .where(
            AgentSession.id == session_id,
            AgentSession.deployment_id == deployment_id,
            AgentDeployment.id == deployment_id,
            AgentDeployment.org_id == org_id,
        )
    )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise RuntimeControlNotFoundError("Runtime session was not found")
    session_row, deployment = row
    if not deployment.is_active:
        raise RuntimeControlConflictError("Runtime deployment is inactive")
    if session_row.status not in {SessionStatus.ACTIVE, SessionStatus.IDLE}:
        raise RuntimeControlConflictError("Runtime session is not active")
    if (
        session_row.agent_instance_id
        and session_row.agent_instance_id != runtime_instance_id
    ):
        raise RuntimeControlConflictError("Runtime instance does not own this session")
    return session_row


async def claim_runtime_controls(
    db: AsyncSession,
    *,
    org_id: str,
    deployment_id: UUID,
    session_id: UUID,
    runtime_instance_id: str,
    actor_subject: str,
    actor_roles: list[str],
    lease_seconds: int,
    max_delivery_attempts: int,
    max_items: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Lease pending or expired commands to one authenticated runtime instance."""
    claimed_at = now or utc_now_naive()
    await _validate_runtime_scope(
        db,
        org_id=org_id,
        deployment_id=deployment_id,
        session_id=session_id,
        runtime_instance_id=runtime_instance_id,
    )

    reclaimable = or_(
        RuntimeControlRequest.status == RuntimeControlStatus.PENDING.value,
        and_(
            RuntimeControlRequest.status == RuntimeControlStatus.LEASED.value,
            RuntimeControlRequest.lease_expires_at <= claimed_at,
        ),
    )
    exhausted_query = (
        select(RuntimeControlRequest)
        .where(
            RuntimeControlRequest.org_id == org_id,
            RuntimeControlRequest.deployment_id == deployment_id,
            RuntimeControlRequest.session_id == session_id,
            RuntimeControlRequest.status == RuntimeControlStatus.LEASED.value,
            RuntimeControlRequest.lease_expires_at <= claimed_at,
            RuntimeControlRequest.delivery_attempts >= max_delivery_attempts,
        )
        .with_for_update(skip_locked=True)
    )
    exhausted = list((await db.execute(exhausted_query)).scalars().all())
    for control in exhausted:
        control.status = RuntimeControlStatus.FAILED.value
        control.failure_reason = "delivery_attempts_exhausted"
        control.acknowledged_at = claimed_at
        control.lease_token_hash = None
        control.lease_expires_at = None
        db.add(
            _audit_event(
                actor_subject=actor_subject,
                actor_roles=actor_roles,
                org_id=org_id,
                action="runtime_control.delivery_exhausted",
                control=control,
                success=False,
                details={"delivery_attempts": control.delivery_attempts},
                occurred_at=claimed_at,
            )
        )

    shutdown_query = (
        select(RuntimeControlRequest, PolicyActionApproval)
        .outerjoin(
            PolicyActionApproval,
            PolicyActionApproval.id == RuntimeControlRequest.approval_id,
        )
        .where(
            RuntimeControlRequest.org_id == org_id,
            RuntimeControlRequest.deployment_id == deployment_id,
            RuntimeControlRequest.session_id == session_id,
            RuntimeControlRequest.action_type == "shutdown",
            reclaimable,
        )
        .with_for_update(of=RuntimeControlRequest, skip_locked=True)
    )
    shutdown_rows = list((await db.execute(shutdown_query)).all())
    for control, approval in shutdown_rows:
        approval_valid = (
            approval is not None
            and approval.status == PolicyApprovalStatus.APPROVED
            and (approval.expires_at is None or approval.expires_at >= claimed_at)
            and (
                control.authorization_expires_at is None
                or control.authorization_expires_at >= claimed_at
            )
        )
        if approval_valid:
            continue
        control.status = RuntimeControlStatus.FAILED.value
        control.failure_reason = "authorization_invalid_or_expired"
        control.acknowledged_at = claimed_at
        control.lease_token_hash = None
        control.lease_expires_at = None
        db.add(
            _audit_event(
                actor_subject=actor_subject,
                actor_roles=actor_roles,
                org_id=org_id,
                action="runtime_control.authorization_rejected",
                control=control,
                success=False,
                occurred_at=claimed_at,
            )
        )

    valid_shutdown_approval = and_(
        RuntimeControlRequest.approval_id.is_not(None),
        or_(
            RuntimeControlRequest.authorization_expires_at.is_(None),
            RuntimeControlRequest.authorization_expires_at >= claimed_at,
        ),
        PolicyActionApproval.id == RuntimeControlRequest.approval_id,
        PolicyActionApproval.status == PolicyApprovalStatus.APPROVED.value,
        or_(
            PolicyActionApproval.expires_at.is_(None),
            PolicyActionApproval.expires_at >= claimed_at,
        ),
    )
    query = (
        select(RuntimeControlRequest)
        .outerjoin(
            PolicyActionApproval,
            PolicyActionApproval.id == RuntimeControlRequest.approval_id,
        )
        .where(
            RuntimeControlRequest.org_id == org_id,
            RuntimeControlRequest.deployment_id == deployment_id,
            RuntimeControlRequest.session_id == session_id,
            RuntimeControlRequest.available_at <= claimed_at,
            RuntimeControlRequest.delivery_attempts < max_delivery_attempts,
            reclaimable,
            or_(
                RuntimeControlRequest.action_type != "shutdown",
                valid_shutdown_approval,
            ),
        )
        .order_by(
            RuntimeControlRequest.priority.desc(),
            RuntimeControlRequest.created_at.asc(),
        )
        .limit(max_items)
        .with_for_update(of=RuntimeControlRequest, skip_locked=True)
    )
    controls = list((await db.execute(query)).scalars().all())
    lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
    claimed: list[dict[str, Any]] = []
    for control in controls:
        raw_token = secrets.token_urlsafe(32)
        control.status = RuntimeControlStatus.LEASED.value
        control.lease_token_hash = _sha256(raw_token)
        control.lease_owner_subject = actor_subject
        control.lease_runtime_instance_id = runtime_instance_id
        control.lease_expires_at = lease_expires_at
        control.delivery_attempts += 1
        db.add(
            _audit_event(
                actor_subject=actor_subject,
                actor_roles=actor_roles,
                org_id=org_id,
                action="runtime_control.claimed",
                control=control,
                details={
                    "runtime_instance_id": runtime_instance_id,
                    "delivery_attempt": control.delivery_attempts,
                    "lease_expires_at": lease_expires_at.isoformat(),
                },
                occurred_at=claimed_at,
            )
        )
        claimed.append(serialize_runtime_control(control, lease_token=raw_token))
    return claimed


async def acknowledge_runtime_control(
    db: AsyncSession,
    *,
    control_id: UUID,
    org_id: str,
    runtime_instance_id: str,
    lease_token: str,
    acknowledgement_id: str,
    outcome: str,
    details: dict[str, Any],
    actor_subject: str,
    actor_roles: list[str],
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist an exact-owner terminal acknowledgement atomically."""
    acknowledged_at = now or utc_now_naive()
    payload_hash = _canonical_payload_hash(outcome=outcome, details=details)
    query = (
        select(RuntimeControlRequest)
        .where(
            RuntimeControlRequest.id == control_id,
            RuntimeControlRequest.org_id == org_id,
        )
        .with_for_update()
    )
    control = (await db.execute(query)).scalar_one_or_none()
    if control is None:
        raise RuntimeControlNotFoundError("Runtime control was not found")

    if control.status in {
        RuntimeControlStatus.APPLIED.value,
        RuntimeControlStatus.FAILED.value,
    }:
        if (
            control.acknowledgement_id == acknowledgement_id
            and control.acknowledgement_payload_hash == payload_hash
            and control.lease_owner_subject == actor_subject
            and control.lease_runtime_instance_id == runtime_instance_id
            and control.lease_token_hash is not None
            and secrets.compare_digest(control.lease_token_hash, _sha256(lease_token))
        ):
            return serialize_runtime_control(control), True
        raise RuntimeControlConflictError("Runtime control already has a different acknowledgement")

    if control.status != RuntimeControlStatus.LEASED.value:
        raise RuntimeControlConflictError("Runtime control is not leased")
    if control.lease_expires_at is None or control.lease_expires_at <= acknowledged_at:
        raise RuntimeControlConflictError("Runtime control lease has expired")
    if control.lease_owner_subject != actor_subject:
        raise RuntimeControlConflictError("Runtime control lease owner does not match")
    if control.lease_runtime_instance_id != runtime_instance_id:
        raise RuntimeControlConflictError("Runtime control runtime instance does not match")
    if control.lease_token_hash is None or not secrets.compare_digest(
        control.lease_token_hash,
        _sha256(lease_token),
    ):
        raise RuntimeControlConflictError("Runtime control lease token is invalid")

    control.status = (
        RuntimeControlStatus.APPLIED.value
        if outcome == "applied"
        else RuntimeControlStatus.FAILED.value
    )
    control.acknowledgement_id = acknowledgement_id
    control.acknowledgement_payload_hash = payload_hash
    control.acknowledged_at = acknowledged_at
    control.acknowledgement_details = details
    failure_reason = details.get("reason")
    control.failure_reason = (
        str(failure_reason or "runtime_reported_failure")
        if outcome == "failed"
        else None
    )
    control.lease_expires_at = None

    session_query = select(AgentSession).where(
        AgentSession.id == control.session_id,
        AgentSession.deployment_id == control.deployment_id,
    ).with_for_update()
    session_row = (await db.execute(session_query)).scalar_one_or_none()
    if session_row is None:
        raise RuntimeControlConflictError("Runtime session no longer exists")

    metadata = dict(session_row.session_metadata or {})
    current = dict(metadata.get("control", {}))
    if current.get("request_id") == str(control.id):
        current["state"] = control.status
        current["delivery_status"] = control.status
        current["execution_confirmed"] = outcome == "applied"
        current["acknowledged_at"] = acknowledged_at.isoformat()
        current["acknowledgement_id"] = acknowledgement_id
        metadata["control"] = current
        session_row.session_metadata = metadata

    if outcome == "applied" and control.action_type == "shutdown":
        session_row.status = SessionStatus.TERMINATED
        session_row.ended_at = acknowledged_at
        session_row.last_activity_at = acknowledged_at

    db.add(
        _audit_event(
            actor_subject=actor_subject,
            actor_roles=actor_roles,
            org_id=org_id,
            action=f"runtime_control.{outcome}",
            control=control,
            success=outcome == "applied",
            details={
                "runtime_instance_id": runtime_instance_id,
                "acknowledgement_id": acknowledgement_id,
                "outcome": outcome,
            },
            occurred_at=acknowledged_at,
        )
    )
    return serialize_runtime_control(control), False
