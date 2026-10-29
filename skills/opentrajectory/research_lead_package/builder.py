"""Builders for generic research-lead packages."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .domain_profiles import get_domain_profile
from .gap_records import derive_gap_records
from .schema import empty_package, validate_research_lead_package
from .source_mapping import (
    artifact_refs_from_recorded_run,
    read_json,
    read_text_if_present,
    recorded_run_source_mapping,
)


def build_research_lead_package(
    *,
    proposal: dict[str, Any],
    domain_profile: Any | None = None,
    domain_profile_id: str | None = None,
    task_domain: str,
    task_subdomain: str,
    evaluation_mode: str = "offline_proposal",
    source_mapping: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate a generic package from normalized inputs."""

    profile = _resolve_domain_profile(domain_profile=domain_profile, domain_profile_id=domain_profile_id)
    package = empty_package()
    package["evaluation_mode"] = evaluation_mode
    package["domain_profile"] = profile.profile_id
    package["task_domain"] = task_domain
    package["task_subdomain"] = task_subdomain
    package["source_mapping"].update(source_mapping or {})
    package["proposal"].update(proposal)

    if evaluation_mode == "offline_proposal":
        package["proposal"]["claimed_result_status"] = "planned"

    package["domain_normalized"] = {
        "profile_id": profile.profile_id,
        "fields": profile.normalize(source or {}, package),
    }
    package["proposal"]["gap_records"] = derive_gap_records(package)

    errors = validate_research_lead_package(package)
    if errors:
        raise ValueError("; ".join(errors))
    return package


def build_research_lead_package_from_recorded_run(
    run_dir: str | Path,
    domain_profile: Any | None = None,
    domain_profile_id: str | None = None,
) -> dict[str, Any]:
    """Build a generic package from a recorded-run directory."""

    run_path = Path(run_dir)
    profile = _resolve_domain_profile(domain_profile=domain_profile, domain_profile_id=domain_profile_id)
    task = read_json(run_path / "task.json")
    proposal_md = read_text_if_present(run_path / "proposal-v3.md")
    experiment_md = read_text_if_present(run_path / "experiment_design-v2.md")
    task_plan_md = read_text_if_present(run_path / "task_plan.md")
    summary_md = read_text_if_present(run_path / "summary.md")

    source = {
        "task": task,
        "proposal_md": proposal_md,
        "experiment_md": experiment_md,
        "task_plan_md": task_plan_md,
        "summary_md": summary_md,
    }
    proposal = profile.proposal_from_recorded_run(
        source=source,
        artifact_refs=artifact_refs_from_recorded_run(run_path),
    )
    return build_research_lead_package(
        proposal=proposal,
        domain_profile=profile,
        task_domain=profile.task_domain_for_recorded_run(source),
        task_subdomain=profile.task_subdomain_for_recorded_run(source),
        evaluation_mode="offline_proposal",
        source_mapping=recorded_run_source_mapping(run_path),
        source=source,
    )


def _resolve_domain_profile(domain_profile: Any | None, domain_profile_id: str | None) -> Any:
    if domain_profile is None and domain_profile_id is None:
        raise ValueError("domain_profile is required")
    if domain_profile is not None and domain_profile_id is not None:
        actual_id = getattr(domain_profile, "profile_id", None)
        if actual_id != domain_profile_id:
            raise ValueError("domain_profile_id does not match domain_profile.profile_id")
    return domain_profile if domain_profile is not None else get_domain_profile(str(domain_profile_id))
