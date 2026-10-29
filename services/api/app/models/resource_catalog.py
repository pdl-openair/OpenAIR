"""Read-only Resource Catalog API contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ResourceType(StrEnum):
    SKILL = "skill"
    AGENT = "agent"
    WORKFLOW = "workflow"


class ResourceAvailability(StrEnum):
    TRYABLE = "tryable"
    CONFIGURATION_REQUIRED = "configuration_required"
    VIEW_ONLY = "view_only"
    UNAVAILABLE = "unavailable"


class ResourceCatalogSort(StrEnum):
    RELEVANCE = "relevance"
    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"


class ResourceDomain(StrEnum):
    CS_RESEARCH = "cs_research"
    GENERAL = "general"
    MATERIALS = "materials"
    OPE = "ope"
    OPENTRAJECTORY = "opentrajectory"
    SDR = "sdr"


class ResourceScenario(StrEnum):
    PROBLEM_MODELING = "problem_modeling"
    SOLUTION_DESIGN = "solution_design"
    EXECUTION_VALIDATION = "execution_validation"
    INSIGHT_GENERATION = "insight_generation"


class ResourceCatalogItemOut(BaseModel):
    """Resource Plaza's runtime catalog contract.

    ``kind``/``inputs``/``outputs``/``usageConditions``/``safetyStatus`` are
    explicit FE-02 adapter aliases.  Their canonical API counterparts are
    ``resourceType``/``input``/``output``/``requirements``/``safetyRisk``.
    They intentionally carry identical values so FE-02 can consume this route
    without client-side guessing.
    """

    id: str
    name: str
    resourceType: ResourceType
    description: str = ""
    input: list[str] = Field(default_factory=list)
    output: list[str] = Field(default_factory=list)
    trustSummary: str
    requirements: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    source: str | None = None
    maintainer: str | None = None
    releaseVersion: str | None = None
    visibleOnHome: bool
    visibleInCatalog: bool
    visibleInDetail: bool
    usagePolicy: str | None = None
    licenseSummary: str | None = None
    evidenceStatus: str | None = None
    evidenceConflictDisposition: str | None = None
    configurationRequirements: list[str] = Field(default_factory=list)
    permissionRequirements: list[str] = Field(default_factory=list)
    executionEntry: str | None = None
    availability: ResourceAvailability
    executionBlocked: bool
    safetyRisk: str | None = None
    primaryAction: str
    capabilityId: str
    durableExecutable: bool | None = None
    launchMode: str | None = None
    dependencyGaps: list[str] = Field(default_factory=list)
    unavailableReason: str | None = None
    runtimeFamily: str | None = None
    domains: list[ResourceDomain] = Field(default_factory=list)
    scenarios: list[ResourceScenario] = Field(default_factory=list)

    # Frozen FE-02 adapter field names.  These are not independently derived.
    kind: ResourceType
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    usageConditions: list[str] = Field(default_factory=list)
    safetyStatus: str | None = None
    keywords: list[str] = Field(default_factory=list)


class ResourceCatalogListOut(BaseModel):
    resources: list[ResourceCatalogItemOut]
    total: int
    page: int
    pageSize: int
