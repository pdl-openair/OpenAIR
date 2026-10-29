"""Domain profile registry for research-lead package normalization."""
from __future__ import annotations

from importlib import import_module
from typing import Any

_PROFILE_REGISTRY: dict[str, Any] = {}
_LAZY_PROFILE_MODULES = {
    "cs_v0.3": "skills.opentrajectory.research_lead_package.domain_profiles.cs",
}


def register_domain_profile(profile: Any) -> Any:
    """Register a domain profile instance by its profile_id."""

    profile_id = getattr(profile, "profile_id", "")
    if not profile_id:
        raise ValueError("domain profile must define profile_id")
    _PROFILE_REGISTRY[str(profile_id)] = profile
    return profile


def get_domain_profile(profile_id: str) -> Any:
    """Return a registered domain profile by id."""

    if profile_id not in _PROFILE_REGISTRY and profile_id in _LAZY_PROFILE_MODULES:
        import_module(_LAZY_PROFILE_MODULES[profile_id])

    try:
        return _PROFILE_REGISTRY[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown domain_profile_id: {profile_id}") from exc


def list_domain_profiles() -> tuple[str, ...]:
    """Return known domain profile ids."""

    for profile_id in tuple(_LAZY_PROFILE_MODULES):
        if profile_id not in _PROFILE_REGISTRY:
            import_module(_LAZY_PROFILE_MODULES[profile_id])
    return tuple(sorted(_PROFILE_REGISTRY))


__all__ = ["get_domain_profile", "list_domain_profiles", "register_domain_profile"]
