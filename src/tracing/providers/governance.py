"""Fail-closed authorization for external AI provider execution."""


class ProviderExecutionDisabledError(RuntimeError):
    """Raised when an external provider call is attempted without authorization."""


def validate_execution_enabled(value: object) -> bool:
    """Accept only literal booleans for provider authorization."""
    if type(value) is not bool:
        raise TypeError("execution_enabled must be a boolean")
    return value is True


def require_provider_execution(enabled: object, *, provider: str) -> None:
    """Reject provider execution unless the owning product explicitly enabled it."""
    if enabled is not True:
        raise ProviderExecutionDisabledError(
            f"{provider} provider execution is disabled; "
            "set PROVIDER_EXECUTION_ENABLED=true only after explicit authorization"
        )
