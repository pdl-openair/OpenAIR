"""Current dispatch fixture using the production physical-attempt identity."""

from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    DispatchFence,
    initial_external_attempt_identity,
)


def authorized_dispatch(**fields) -> AuthorizedDispatch:
    fence = DispatchFence(**{
        name: fields[name] for name in DispatchFence.__dataclass_fields__
    })
    fields.setdefault("external_attempt", initial_external_attempt_identity(fence))
    return AuthorizedDispatch(**fields)
