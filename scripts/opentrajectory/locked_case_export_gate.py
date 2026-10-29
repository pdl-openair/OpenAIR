#!/usr/bin/env python3
"""Fail-closed locked case export gate for OpenTrajectory case packages.

The gate is intentionally small: it checks whether each named case has the
minimum real inputs needed to export a locked research-lead package. It never
fills missing fields, migrates evidence, or rewrites source packages.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "opentrajectory-locked-case-export-gate"


@dataclass(frozen=True)
class CaseContract:
    case_id: str
    case_package: Path
    evidence_index: Path
    gap_normalization: Path
    planner_action: Path
    boundary_ledger: Path
    next_real_input_needed: str
    require_authentic_package_fields: bool = True
    require_planner_actions: bool = True
    require_source_artifact_path: bool = True
    require_negative_only_boundary: bool = False


def _repo_path(path: str) -> Path:
    return REPO_ROOT / path


CASE_CONTRACTS: tuple[CaseContract, ...] = (
    CaseContract(
        case_id="PaperBench",
        case_package=_repo_path("artifacts/locked-case-inputs/PaperBench/case_package.json"),
        evidence_index=_repo_path("artifacts/locked-case-inputs/PaperBench/evidence_index.md"),
        gap_normalization=_repo_path("artifacts/locked-case-inputs/PaperBench/gap_normalization.json"),
        planner_action=_repo_path("artifacts/locked-case-inputs/PaperBench/planner_action.json"),
        boundary_ledger=_repo_path("artifacts/locked-case-inputs/PaperBench/boundary_ledger.md"),
        next_real_input_needed=(
            "Provide a real PaperBench locked case package with benchmark task/run IDs, "
            "source evidence index, gap-normalization output, planner action output, "
            "and a boundary ledger stating benchmark grading is not scientific validation."
        ),
    ),
    CaseContract(
        case_id="ASSB",
        case_package=_repo_path("artifacts/locked-case-inputs/ASSB/case_package.json"),
        evidence_index=_repo_path("artifacts/locked-case-inputs/ASSB/evidence_index.md"),
        gap_normalization=_repo_path("artifacts/locked-case-inputs/ASSB/gap_normalization.json"),
        planner_action=_repo_path("artifacts/locked-case-inputs/ASSB/planner_action.json"),
        boundary_ledger=_repo_path("artifacts/locked-case-inputs/ASSB/boundary_ledger.md"),
        next_real_input_needed=(
            "Provide a real ASSB locked case package with source-grounded evidence "
            "anchors, visible gaps, gap-normalization output, planner action output, "
            "and a boundary ledger blocking discovery/breakthrough/universal-rule claims."
        ),
    ),
    CaseContract(
        case_id="BFE-F2DEM",
        case_package=_repo_path("artifacts/locked-case-inputs/BFE-F2DEM/case_package.json"),
        evidence_index=_repo_path("artifacts/locked-case-inputs/BFE-F2DEM/evidence_index.md"),
        gap_normalization=_repo_path("artifacts/locked-case-inputs/BFE-F2DEM/gap_normalization.json"),
        planner_action=_repo_path("artifacts/locked-case-inputs/BFE-F2DEM/planner_action.json"),
        boundary_ledger=_repo_path("artifacts/locked-case-inputs/BFE-F2DEM/boundary_ledger.md"),
        next_real_input_needed=(
            "Provide a real BFE/F2DEM locked case package preserving NEGATIVE_ONLY "
            "status, with source evidence index, gap-normalization output, planner "
            "action output, and a boundary ledger blocking positive/reproduced/"
            "decision-ready validation claims."
        ),
        require_negative_only_boundary=True,
    ),
)

CASE_CONTRACT_BY_ID = {contract.case_id: contract for contract in CASE_CONTRACTS}


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _read_json(path: Path, missing: list[str], diagnostics: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        missing.append(f"missing file: {_relative(path)}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        diagnostics.append(f"invalid JSON in {_relative(path)}: {exc}")
        return None
    if not isinstance(data, dict):
        diagnostics.append(f"JSON root is not an object: {_relative(path)}")
        return None
    return data


def _read_text(path: Path, missing: list[str], diagnostics: list[str]) -> str | None:
    if not path.exists():
        missing.append(f"missing file: {_relative(path)}")
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        diagnostics.append(f"cannot decode {_relative(path)} as UTF-8: {exc}")
        return None


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _check_authentic_package(
    contract: CaseContract,
    package_data: dict[str, Any] | None,
    missing: list[str],
    diagnostics: list[str],
) -> None:
    if package_data is None:
        return

    status = str(package_data.get("status") or "").upper()
    if status in {"NOT_PRODUCED", "NOT_RUN", "BLOCKED", "PARTIAL"}:
        diagnostics.append(f"case package status is {status}; locked export requires produced package")

    if package_data.get("is_authentic_research_lead_package") is not True:
        missing.append("case package is not marked is_authentic_research_lead_package=true")

    for field in ("run_id", "trace_id", "case_id"):
        if not _nonempty(package_data.get(field)):
            missing.append(f"case package missing non-empty {field}")

    if not _nonempty(package_data.get("research_leads")):
        missing.append("case package missing non-empty research_leads")

    evidence_status = str(package_data.get("evidence_status_fields") or "").upper()
    if not evidence_status or "PARTIAL" in evidence_status or "ONLY" in evidence_status:
        missing.append("case package missing final evidence/status fields")

    visible_gaps = str(package_data.get("visible_gaps") or "").upper()
    if not visible_gaps or "PARTIAL" in visible_gaps or "ONLY" in visible_gaps:
        missing.append("case package missing final visible gaps")

    if package_data.get("fixture_copy_used") is True:
        diagnostics.append("case package says fixture_copy_used=true")

    if contract.require_source_artifact_path:
        source_path = package_data.get("source_artifact_path")
        if not _nonempty(source_path):
            missing.append("case package missing source_artifact_path")
            return
        resolved = (REPO_ROOT / str(source_path)).resolve()
        try:
            resolved.relative_to(REPO_ROOT.resolve())
        except ValueError:
            diagnostics.append(f"source_artifact_path leaves repo: {source_path}")
            return
        if not resolved.exists():
            missing.append(f"source_artifact_path does not exist: {source_path}")


def _check_planner_action(
    contract: CaseContract,
    planner_data: dict[str, Any] | None,
    missing: list[str],
    diagnostics: list[str],
) -> None:
    if planner_data is None:
        return

    status = str(planner_data.get("status") or "").upper()
    if status in {"NOT_RUN", "NOT_PRODUCED", "BLOCKED", "PARTIAL"}:
        diagnostics.append(f"planner action status is {status}; locked export requires executed planner output")

    if contract.require_planner_actions:
        actions_count = planner_data.get("targeted_actions_count")
        if not isinstance(actions_count, int) or actions_count <= 0:
            missing.append("planner action missing targeted_actions_count > 0")

    if planner_data.get("evidence_unchanged") is not True:
        missing.append("planner action missing evidence_unchanged=true")

    if planner_data.get("status_unchanged") is not True:
        missing.append("planner action missing status_unchanged=true")

    if contract.require_negative_only_boundary:
        negative_status = str(planner_data.get("bfe_f2dem_negative_only_preserved") or "").upper()
        if negative_status != "TRUE":
            missing.append("planner action missing bfe_f2dem_negative_only_preserved=true")


def _check_boundary_ledger(
    contract: CaseContract,
    text: str | None,
    missing: list[str],
    diagnostics: list[str],
) -> None:
    if text is None:
        return
    upper = text.upper()
    if "FAIL" in upper:
        diagnostics.append("boundary ledger contains FAIL")
    if "PARTIAL" in upper:
        diagnostics.append("boundary ledger contains PARTIAL")
    required_phrases = {
        "PaperBench": "NO PAPERBENCH SCIENTIFIC-VALIDATION CLAIM",
        "ASSB": "NO ASSB DISCOVERY/BREAKTHROUGH/UNIVERSAL-RULE CLAIM",
        "BFE-F2DEM": "NO BFE/F2DEM POSITIVE/REPRODUCED/DECISION-READY CLAIM",
    }
    phrase = required_phrases.get(contract.case_id)
    if phrase and phrase not in upper:
        missing.append(f"boundary ledger missing phrase: {phrase}")


def evaluate_case(contract: CaseContract) -> dict[str, Any]:
    missing: list[str] = []
    diagnostics: list[str] = []

    package_data = _read_json(contract.case_package, missing, diagnostics)
    _read_text(contract.evidence_index, missing, diagnostics)
    _read_text(contract.gap_normalization, missing, diagnostics)
    planner_data = _read_json(contract.planner_action, missing, diagnostics)
    boundary_text = _read_text(contract.boundary_ledger, missing, diagnostics)

    if contract.require_authentic_package_fields:
        _check_authentic_package(contract, package_data, missing, diagnostics)
    _check_planner_action(contract, planner_data, missing, diagnostics)
    _check_boundary_ledger(contract, boundary_text, missing, diagnostics)

    missing_unique = list(dict.fromkeys(missing))
    diagnostics_unique = list(dict.fromkeys(diagnostics))
    status = "PASS" if not missing_unique and not diagnostics_unique else "BLOCKED"
    blocked_reason = "none"
    if status == "BLOCKED":
        first_missing = missing_unique[0] if missing_unique else None
        first_diag = diagnostics_unique[0] if diagnostics_unique else None
        blocked_reason = first_diag or first_missing or "unknown locked export blocker"

    return {
        "case_id": contract.case_id,
        "status": status,
        "blocked_reason": blocked_reason,
        "missing_requirements": missing_unique,
        "diagnostics": diagnostics_unique,
        "next_real_input_needed": "none" if status == "PASS" else contract.next_real_input_needed,
        "inputs": {
            "case_package": _relative(contract.case_package),
            "evidence_index": _relative(contract.evidence_index),
            "gap_normalization": _relative(contract.gap_normalization),
            "planner_action": _relative(contract.planner_action),
            "boundary_ledger": _relative(contract.boundary_ledger),
        },
    }


def _parse_case_root(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--case-root must use CASE_ID=path")
    case_id, raw_path = value.split("=", 1)
    case_id = case_id.strip()
    if case_id not in CASE_CONTRACT_BY_ID:
        known = ", ".join(sorted(CASE_CONTRACT_BY_ID))
        raise argparse.ArgumentTypeError(f"unknown case_id {case_id!r}; expected one of: {known}")
    if not raw_path.strip():
        raise argparse.ArgumentTypeError(f"empty path for case_id {case_id}")
    return case_id, Path(raw_path)


def _contract_from_root(base: CaseContract, root: Path) -> CaseContract:
    if not root.is_absolute():
        root = REPO_ROOT / root
    return CaseContract(
        case_id=base.case_id,
        case_package=root / "case_package.json",
        evidence_index=root / "evidence_index.md",
        gap_normalization=root / "gap_normalization.json",
        planner_action=root / "planner_action.json",
        boundary_ledger=root / "boundary_ledger.md",
        next_real_input_needed=base.next_real_input_needed,
        require_authentic_package_fields=base.require_authentic_package_fields,
        require_planner_actions=base.require_planner_actions,
        require_source_artifact_path=base.require_source_artifact_path,
        require_negative_only_boundary=base.require_negative_only_boundary,
    )


def _apply_case_root_overrides(
    contracts: tuple[CaseContract, ...], overrides: list[tuple[str, Path]]
) -> list[CaseContract]:
    by_id = {contract.case_id: contract for contract in contracts}
    for case_id, root in overrides:
        by_id[case_id] = _contract_from_root(by_id[case_id], root)
    return [by_id[contract.case_id] for contract in contracts]


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| case | status | blocked_reason | missing_requirements | next_real_input_needed |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        missing = "<br>".join(row["missing_requirements"] + row["diagnostics"]) or "none"
        lines.append(
            "| {case_id} | {status} | {blocked_reason} | {missing} | {next_input} |".format(
                case_id=row["case_id"],
                status=row["status"],
                blocked_reason=str(row["blocked_reason"]).replace("|", "\\|"),
                missing=missing.replace("|", "\\|"),
                next_input=str(row["next_real_input_needed"]).replace("|", "\\|"),
            )
        )
    return "\n".join(lines)


def build_report(rows: list[dict[str, Any]]) -> str:
    can_locked_export = all(row["status"] == "PASS" for row in rows)
    three_case_ids = {"PaperBench", "ASSB", "BFE-F2DEM"}
    can_three_case_suite = all(
        row["status"] == "PASS" for row in rows if row["case_id"] in three_case_ids
    )
    blocked_inputs = [
        {
            "case_id": row["case_id"],
            "missing_requirements": row["missing_requirements"],
            "diagnostics": row["diagnostics"],
            "next_real_input_needed": row["next_real_input_needed"],
        }
        for row in rows
        if row["status"] != "PASS"
    ]

    payload = {
        "export_gate_status": "PASS" if can_locked_export else "BLOCKED",
        "can_run_locked_three_case_suite": "YES" if can_three_case_suite else "NO",
        "recommend_cs_value_demonstration_case": "YES",
        "cs_case_locked_input_conditions": [
            "case_package is a real run output, not a fixture copy",
            "case_package has non-empty run_id, trace_id, case_id, research_leads",
            "case_package includes final evidence/status fields and visible gaps",
            "source_artifact_path exists and points to raw or replayable run artifacts",
            "evidence_index exists and maps claims to source anchors or explicit gaps",
            "gap_normalization exists and is derived from the case package",
            "planner_action exists, has targeted_actions_count > 0, and preserves evidence/status unchanged",
            "boundary_ledger exists and blocks forbidden positive/scientific-validation claims",
        ],
        "blocked_inputs": blocked_inputs,
    }

    lines = [
        "# Locked Case Export Gate",
        "",
        "## Verdict",
        "",
        f"- Export gate status: {payload['export_gate_status']}",
        f"- Now able to run locked three-case suite: {payload['can_run_locked_three_case_suite']}",
        "- If not, missing real inputs are listed case-by-case below.",
        f"- Recommend next CS value-demonstration case: {payload['recommend_cs_value_demonstration_case']}",
        "",
        "## Case Readiness Table",
        "",
        _markdown_table(rows),
        "",
        "## CS Case Locked Input Conditions",
        "",
    ]
    lines.extend(f"- {condition}" for condition in payload["cs_case_locked_input_conditions"])
    lines.extend(["", "## Machine Verdict", "", "```json", json.dumps(payload, indent=2), "```", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fail-closed locked case export gate.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for readiness JSON and Markdown diagnostics.",
    )
    parser.add_argument(
        "--case-root",
        action="append",
        type=_parse_case_root,
        default=[],
        metavar="CASE_ID=PATH",
        help=(
            "Override one case input directory. The directory must contain "
            "case_package.json, evidence_index.md, gap_normalization.json, "
            "planner_action.json, and boundary_ledger.md. May be repeated."
        ),
    )
    args = parser.parse_args()

    contracts = _apply_case_root_overrides(CASE_CONTRACTS, args.case_root)
    rows = [evaluate_case(contract) for contract in contracts]
    gate_status = "PASS" if all(row["status"] == "PASS" for row in rows) else "BLOCKED"
    output = {
        "gate": "locked_case_export",
        "status": gate_status,
        "cases": rows,
    }

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "locked_case_readiness.json").write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "LOCKED_CASE_READINESS.md").write_text(build_report(rows), encoding="utf-8")

    print(f"LOCKED_CASE_EXPORT_GATE={gate_status}")
    print(f"readiness_json={_relative(output_dir / 'locked_case_readiness.json')}")
    print(f"readiness_md={_relative(output_dir / 'LOCKED_CASE_READINESS.md')}")
    return 0 if gate_status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
