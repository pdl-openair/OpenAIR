from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ApplicabilityResult:
    status: str
    profile_id: str
    profile_version: int
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = list(self.issues)
        return data








def evaluate_process_context(process_context, profile):
    return ApplicabilityResult(
        status="eligible", profile_id=str(profile["profile_id"]),
        profile_version=int(profile["profile_version"]), issues=(),
    )
