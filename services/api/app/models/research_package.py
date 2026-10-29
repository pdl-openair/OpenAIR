"""HTTP models for the Research Package asset lifecycle."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ResearchPackageCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: dict[str, Any]
    sourceTaskId: str | None = None


class ResearchPackageAssetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifactId: str
    packageId: str
    version: str
    specVersion: str
    name: str
    summary: str
    contentSha256: str
    sourceTaskId: str | None = None
    package: dict[str, Any]
