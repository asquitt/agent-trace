"""Fail-closed boundary for autonomous governance and outbound effects."""

from ..config import Settings

RUNTIME_GOVERNANCE_DISABLED_DETAIL = "runtime_governance_disabled"


class RuntimeGovernanceDisabledError(RuntimeError):
    """Raised before a frozen governance or outbound effect can start."""


def require_runtime_governance(settings: Settings) -> None:
    """Reject governance execution unless the owner explicitly enables it."""
    require_runtime_governance_enabled(settings.runtime_governance_enabled)


def require_runtime_governance_enabled(enabled: bool) -> None:
    """Apply the same boundary in services that do not own application settings."""
    if not enabled:
        raise RuntimeGovernanceDisabledError(RUNTIME_GOVERNANCE_DISABLED_DETAIL)
