"""Runtime-facing durable control delivery endpoints."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...dependencies import AuthDep, SettingsDep, StorageDep
from ...security import AuthContext, require_org_access, require_roles
from ...services.runtime_controls import (
    RuntimeControlConflictError,
    RuntimeControlNotFoundError,
    acknowledge_runtime_control,
    claim_runtime_controls,
)

router = APIRouter(prefix="/api/v1/runtime-controls", tags=["runtime-controls"])


class RuntimeControlClaimRequest(BaseModel):
    org_id: str = Field(min_length=1, max_length=255)
    deployment_id: UUID
    session_id: UUID
    runtime_instance_id: str = Field(min_length=1, max_length=255)
    max_items: int = Field(default=10, ge=1, le=100)


class RuntimeControlClaimResponse(BaseModel):
    controls: list[dict[str, Any]]


class RuntimeControlAckRequest(BaseModel):
    org_id: str = Field(min_length=1, max_length=255)
    runtime_instance_id: str = Field(min_length=1, max_length=255)
    lease_token: str = Field(min_length=32, max_length=255)
    acknowledgement_id: str = Field(min_length=1, max_length=255)
    outcome: Literal["applied", "failed"]
    details: dict[str, Any] = Field(default_factory=dict)


class RuntimeControlAckResponse(BaseModel):
    control: dict[str, Any]
    idempotent_replay: bool


def _require_runtime_auth(auth: AuthContext, org_id: str) -> None:
    if auth.authentication_method != "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Runtime control delivery requires API-key authentication",
        )
    require_roles(auth, "operator", "admin")
    require_org_access(auth, org_id)


def _translate_runtime_control_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeControlNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/claim", response_model=RuntimeControlClaimResponse)
async def claim_controls(
    body: RuntimeControlClaimRequest,
    storage: StorageDep,
    settings: SettingsDep,
    auth: AuthDep,
) -> RuntimeControlClaimResponse:
    _require_runtime_auth(auth, body.org_id)
    async with storage.session_factory() as db:
        try:
            controls = await claim_runtime_controls(
                db,
                org_id=body.org_id,
                deployment_id=body.deployment_id,
                session_id=body.session_id,
                runtime_instance_id=body.runtime_instance_id,
                actor_subject=auth.subject,
                actor_roles=sorted(auth.roles),
                lease_seconds=settings.runtime_control_lease_seconds,
                max_delivery_attempts=settings.runtime_control_max_delivery_attempts,
                max_items=body.max_items,
            )
            await db.commit()
        except (RuntimeControlNotFoundError, RuntimeControlConflictError) as exc:
            await db.rollback()
            raise _translate_runtime_control_error(exc) from exc
    return RuntimeControlClaimResponse(controls=controls)


@router.post("/{control_id}/ack", response_model=RuntimeControlAckResponse)
async def acknowledge_control(
    control_id: UUID,
    body: RuntimeControlAckRequest,
    storage: StorageDep,
    auth: AuthDep,
) -> RuntimeControlAckResponse:
    _require_runtime_auth(auth, body.org_id)
    async with storage.session_factory() as db:
        try:
            control, idempotent_replay = await acknowledge_runtime_control(
                db,
                control_id=control_id,
                org_id=body.org_id,
                runtime_instance_id=body.runtime_instance_id,
                lease_token=body.lease_token,
                acknowledgement_id=body.acknowledgement_id,
                outcome=body.outcome,
                details=body.details,
                actor_subject=auth.subject,
                actor_roles=sorted(auth.roles),
            )
            await db.commit()
        except (RuntimeControlNotFoundError, RuntimeControlConflictError) as exc:
            await db.rollback()
            raise _translate_runtime_control_error(exc) from exc
    return RuntimeControlAckResponse(
        control=control,
        idempotent_replay=idempotent_replay,
    )
