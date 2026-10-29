#!/usr/bin/env python3
"""Deterministic CatalogRelease generator for the official Resource Plaza catalog.

Usage (from repo root, with PYTHONPATH=services/api):
  python scripts/resource_catalog/generate_catalog_releases.py
  python scripts/resource_catalog/generate_catalog_releases.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.modules.agent_registry import load_agent_registry  # noqa: E402
from app.modules.repo_paths import repo_root  # noqa: E402
from app.modules.resource_capability_facts import resolve_resource_capability_facts  # noqa: E402
from app.modules.resource_catalog import (  # noqa: E402
    CATALOG_RELEASES_PATH,
    NON_PRODUCT_SKILL_IDS,
    _load_skill_entries,
)

DEFAULT_FIRST_RELEASE = "0.1.0"
OFFICIAL_SOURCE = "OpenAIR 官方"
OFFICIAL_MAINTAINER = "OpenAIR 官方"

ALLOWED_DOMAINS = frozenset({"cs_research", "general", "materials", "ope", "opentrajectory", "sdr"})
ALLOWED_SCENARIOS = frozenset(
    {"problem_modeling", "solution_design", "execution_validation", "insight_generation"}
)

PRESERVE_FIELDS = (
    "releaseVersion",
    "source",
    "maintainer",
    "usagePolicy",
    "licenseSummary",
    "evidenceStatus",
    "evidenceConflictDisposition",
    "permissionRequirements",
    "visibleOnHome",
)


def _read_skill_version(skill_path_rel: object) -> str | None:
    if not isinstance(skill_path_rel, str) or not skill_path_rel.strip():
        return None
    path = repo_root() / skill_path_rel / "skill.yaml"
    if not path.is_file():
        return None
    try:
        meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    ver = meta.get("version")
    if isinstance(ver, (int, float)):
        return str(ver)
    if isinstance(ver, str) and ver.strip():
        return ver.strip()
    return None


def _load_existing_releases() -> dict[str, dict[str, Any]]:
    if not CATALOG_RELEASES_PATH.is_file():
        return {}
    payload = yaml.safe_load(CATALOG_RELEASES_PATH.read_text(encoding="utf-8")) or {}
    rows = payload.get("catalogReleases") or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("resourceId"), str):
            out[row["resourceId"]] = dict(row)
    return out


def _primary_domain(values: list[str]) -> str:
    cleaned = [v for v in values if v in ALLOWED_DOMAINS]
    if not cleaned:
        return "general"
    counts = Counter(cleaned)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _primary_scenarios(values: list[str]) -> list[str]:
    cleaned = sorted({v for v in values if v in ALLOWED_SCENARIOS})
    return cleaned or ["problem_modeling"]


def _tryable_fields(*, resource_type: str) -> dict[str, Any]:
    entry = "durable_skill" if resource_type == "skill" else "durable_agent"
    return {
        "availability": "tryable",
        "executionBlocked": False,
        "primaryAction": "try",
        "executionEntry": entry,
        "configurationRequirements": [],
        "permissionRequirements": ["OpenAIR 受控试用范围内试用"],
        "usagePolicy": "仅限 OpenAIR 受控试用范围内试用；不得导出、再分发或用于未授权外部复用。",
        "licenseSummary": "仅限 OpenAIR 受控试用范围内试用；不得导出、再分发或用于未授权外部复用。",
        "safetyRisk": None,
    }


def _view_only_fields() -> dict[str, Any]:
    return {
        "availability": "view_only",
        "executionBlocked": True,
        "primaryAction": "view",
        "executionEntry": None,
        "configurationRequirements": [],
        "permissionRequirements": ["OpenAIR 受控试用范围内查看"],
        "usagePolicy": "仅限 OpenAIR 受控试用范围内查看；暂不开放资源执行、导出、再分发或外部复用。",
        "licenseSummary": "仅限 OpenAIR 受控试用范围内查看；暂不开放资源执行、导出、再分发或外部复用。",
        "safetyRisk": None,
    }


def _base_release(resource_id: str, resource_type: str, version: str, domains: list[str], scenarios: list[str]) -> dict[str, Any]:
    return {
        "resourceId": resource_id,
        "resourceType": resource_type,
        "releaseVersion": version,
        "catalogReleased": True,
        "discoverable": True,
        "visibleOnHome": False,
        "visibleInCatalog": True,
        "visibleInDetail": True,
        "source": OFFICIAL_SOURCE,
        "maintainer": OFFICIAL_MAINTAINER,
        "evidenceStatus": "verified_execution_evidence",
        "evidenceConflictDisposition": "no_conflict_recorded",
        "domains": domains,
        "scenarios": scenarios,
    }


def _merge_preserved(existing: dict[str, Any] | None, generated: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return generated
    merged = dict(generated)
    for field in PRESERVE_FIELDS:
        if field in existing and existing[field] not in (None, "", []):
            merged[field] = existing[field]
    # Keep human availability only when still consistent with runtime decision below;
    # caller overwrites availability fields after merge.
    return merged


def build_releases() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing = _load_existing_releases()
    entries = _load_skill_entries()
    skill_by_id = {
        str(entry["skill_id"]).strip(): entry
        for entry in entries
        if isinstance(entry.get("skill_id"), str) and entry["skill_id"].strip()
    }
    low_confidence: list[dict[str, Any]] = []
    releases: list[dict[str, Any]] = []

    for skill_id, entry in sorted(skill_by_id.items()):
        if skill_id in NON_PRODUCT_SKILL_IDS:
            continue
        resource_id = f"skill:{skill_id}"
        domain = str(entry.get("domain") or "").strip()
        if domain not in ALLOWED_DOMAINS:
            domain = "general"
            low_confidence.append(
                {
                    "resourceId": resource_id,
                    "chosenScenario": _primary_scenarios(list(entry.get("stages") or []))[0],
                    "chosenDomain": domain,
                    "classificationBasis": "fallback_general_domain",
                    "reasonForLowConfidence": "registry_domain_missing_or_invalid",
                }
            )
        scenarios = _primary_scenarios([str(v) for v in (entry.get("stages") or [])])
        if not (entry.get("stages") or []):
            low_confidence.append(
                {
                    "resourceId": resource_id,
                    "chosenScenario": scenarios[0],
                    "chosenDomain": domain,
                    "classificationBasis": "fallback_problem_modeling",
                    "reasonForLowConfidence": "registry_stages_missing",
                }
            )

        version = _read_skill_version(entry.get("skill_path"))
        prior = existing.get(resource_id)
        if version is None and prior and isinstance(prior.get("releaseVersion"), (str, int, float)):
            version = str(prior["releaseVersion"]).strip() or None
        if version is None:
            version = DEFAULT_FIRST_RELEASE

        facts = resolve_resource_capability_facts(resource_id, "skill")
        package_ok = False
        rel = entry.get("skill_path")
        if isinstance(rel, str) and rel.strip():
            skill_dir = repo_root() / rel
            package_ok = skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file()

        tryable = (
            bool(facts.capability_id)
            and bool(version)
            and package_ok
            and facts.durable_executable
            and not facts.dependency_gaps
            and facts.unavailable_reason is None
        )
        generated = _base_release(resource_id, "skill", version, [domain], scenarios)
        generated.update(_tryable_fields(resource_type="skill") if tryable else _view_only_fields())
        releases.append(_merge_preserved(prior, generated))

    registry = load_agent_registry(validate=False)
    for agent in sorted(registry.list_agents(), key=lambda row: row.agentId):
        if not agent.enabled:
            continue
        resource_id = f"agent:{agent.agentId}"
        grant_domains: list[str] = []
        for skill_id in agent.skillIds:
            entry = skill_by_id.get(skill_id)
            if entry and isinstance(entry.get("domain"), str):
                grant_domains.append(entry["domain"].strip())
        domain = _primary_domain(grant_domains)
        scenarios = _primary_scenarios(
            [str(mapping.get("stage") or "") for mapping in agent.sdr_mappings]
        )
        if not grant_domains:
            low_confidence.append(
                {
                    "resourceId": resource_id,
                    "chosenScenario": scenarios[0],
                    "chosenDomain": domain,
                    "classificationBasis": "agent_skill_grant_domains",
                    "reasonForLowConfidence": "no_grant_domain_fallback_general",
                }
            )

        prior = existing.get(resource_id)
        version = DEFAULT_FIRST_RELEASE
        if prior and isinstance(prior.get("releaseVersion"), (str, int, float)):
            version = str(prior["releaseVersion"]).strip() or DEFAULT_FIRST_RELEASE

        facts = resolve_resource_capability_facts(resource_id, "agent")
        tryable = (
            bool(facts.capability_id)
            and bool(version)
            and facts.durable_executable
            and not facts.dependency_gaps
            and facts.unavailable_reason is None
        )
        generated = _base_release(resource_id, "agent", version, [domain], scenarios)
        generated.update(_tryable_fields(resource_type="agent") if tryable else _view_only_fields())
        releases.append(_merge_preserved(prior, generated))

    releases.sort(key=lambda row: (row["resourceType"], row["resourceId"]))
    return releases, low_confidence


def _validate(releases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids = [row["resourceId"] for row in releases]
    if len(ids) != 133:
        errors.append(f"expected_133_got_{len(ids)}")
    if "skill:fixture_recorded_run_creation" in ids:
        errors.append("non_product_fixture_present")
    if len(set(ids)) != len(ids):
        errors.append("duplicate_resource_id")
    pairs = [(row["resourceId"], str(row["releaseVersion"])) for row in releases]
    if len(set(pairs)) != len(pairs):
        errors.append("duplicate_resource_id_version")
    for row in releases:
        domains = row.get("domains") or []
        scenarios = row.get("scenarios") or []
        if not domains or any(d not in ALLOWED_DOMAINS for d in domains):
            errors.append(f"invalid_domains:{row['resourceId']}")
        if not scenarios or any(s not in ALLOWED_SCENARIOS for s in scenarios):
            errors.append(f"invalid_scenarios:{row['resourceId']}")
        if row["availability"] == "tryable":
            if row["executionBlocked"] or row["primaryAction"] != "try" or not row.get("executionEntry"):
                errors.append(f"tryable_state_conflict:{row['resourceId']}")
        if row["availability"] == "view_only":
            if (not row["executionBlocked"]) or row["primaryAction"] != "view" or row.get("executionEntry") is not None:
                errors.append(f"view_only_state_conflict:{row['resourceId']}")
    return errors


def _dump_yaml(releases: list[dict[str, Any]]) -> str:
    payload = {"catalogReleases": releases}
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-low-confidence", action="store_true", default=True)
    args = parser.parse_args()

    releases, low_confidence = build_releases()
    errors = _validate(releases)
    rendered = _dump_yaml(releases)

    low_path = repo_root() / ".run" / "full-catalog-low-confidence.json"
    if args.write_low_confidence:
        low_path.parent.mkdir(parents=True, exist_ok=True)
        low_path.write_text(json.dumps(low_confidence, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.check:
        current = CATALOG_RELEASES_PATH.read_text(encoding="utf-8") if CATALOG_RELEASES_PATH.is_file() else ""
        if errors:
            print("CHECK_FAIL", *errors, sep="\n")
            return 2
        if current.replace("\r\n", "\n") != rendered.replace("\r\n", "\n"):
            print("CHECK_FAIL yaml_diff")
            return 3
        print(f"CHECK_OK total={len(releases)} low_confidence={len(low_confidence)}")
        return 0

    if errors:
        print("GENERATE_FAIL", *errors, sep="\n")
        return 2
    CATALOG_RELEASES_PATH.write_text(rendered, encoding="utf-8")
    print(f"WRITE_OK path={CATALOG_RELEASES_PATH} total={len(releases)} low_confidence={len(low_confidence)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
