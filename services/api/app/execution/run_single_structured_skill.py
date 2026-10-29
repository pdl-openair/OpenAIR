"""CLI bridge for isolated Skill Lab live execution.

P0.5d keeps Skill Lab independent from the production task orchestrator while
supporting two explicitly allowlisted execution modes:
- Phase1-3 structured transformation Skills via the LLM runner.
- Self-contained metadata retrieval and deterministic source-candidate Skills via Python adapters.

The bridge validates input and output schemas for both modes and writes adapter
outputs into the temporary workspace for parity with the LLM runner.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from jsonschema import ValidationError

from app.execution.llm_structured_runner import _failure_payload, run_skill_with_llm
from app.execution.durable_skill_runtime import (
    freeze_registered_source_contract,
    list_registered_durable_skills,
)
from app.execution.structured_skill_schema_runtime import (
    find_openair_repository_root,
    validate_instance_against_schema_file,
)
from app.execution.opentrajectory_structured_runtime_guard import RuntimeGuardRejected
from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import (
    PYTHON_ADAPTER_SKILLS as _PYTHON_ADAPTER_SKILLS,
    resolve_python_adapter_skill,
)

# Backward-compatible export for callers/tests that imported this symbol from
# the Skill Lab bridge before the allowlist became shared.
PYTHON_ADAPTER_SKILLS = _PYTHON_ADAPTER_SKILLS

LLM_SKILLS = {
    "structured-requirement-generation": "skills/opentrajectory/structured-requirement-generation",
    "structured_requirement_generation": "skills/opentrajectory/structured-requirement-generation",
    "constraint-interpretation-skill": "skills/opentrajectory/constraint-interpretation-skill",
    "constraint_interpretation_skill": "skills/opentrajectory/constraint-interpretation-skill",
    "retrieval-matrix-generation": "skills/opentrajectory/retrieval-matrix-generation",
    "retrieval_matrix_generation": "skills/opentrajectory/retrieval-matrix-generation",
    "evidence-node-extraction-skill": "skills/opentrajectory/evidence-node-extraction-skill",
    "evidence_node_extraction": "skills/opentrajectory/evidence-node-extraction-skill",
    "evidence-schema-design-skill": "skills/opentrajectory/evidence-schema-design-skill",
    "evidence_schema_design": "skills/opentrajectory/evidence-schema-design-skill",
    "mechanism-chain-synthesis-skill": "skills/opentrajectory/mechanism-chain-synthesis-skill",
    "mechanism_chain_synthesis": "skills/opentrajectory/mechanism-chain-synthesis-skill",
}


def _configure_stdio_utf8() -> None:
    """Use UTF-8 for the machine-readable CLI bridge on every platform."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def _repo_root() -> Path:
    return find_openair_repository_root(Path(__file__).resolve())


def _read_input_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"stdin is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stdin JSON must be an object")
    return payload


def _frozen_registry_source_contract(relative_skill_path: str) -> dict[str, Any]:
    matches = [
        row
        for row in list_registered_durable_skills()
        if row.get("skillPath") == relative_skill_path
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "structured Skill must resolve to exactly one canonical Registry row: "
            f"{relative_skill_path}"
        )
    row = matches[0]
    return freeze_registered_source_contract(
        dict(row["sourceContract"]),
        skill_path=str(row["skillPath"]),
    )



def _validate_payload(
    *,
    payload: dict[str, Any],
    schema_path: Path,
    label: str,
    repository_root: Path,
) -> None:
    try:
        validate_instance_against_schema_file(
            payload, schema_path, allowed_root=repository_root
        )
    except ValidationError as exc:
        raise RuntimeError(f"{label} does not match schema: {exc.message}") from exc


def _write_adapter_output(*, skill_dir: Path, output: dict[str, Any], workspace_dir: Path, task_id: str) -> None:
    skill_yaml_path = skill_dir / "skill.yaml"
    if not skill_yaml_path.exists():
        raise RuntimeError(f"skill.yaml not found: {skill_yaml_path}")
    skill_meta = yaml.safe_load(skill_yaml_path.read_text(encoding="utf-8")) or {}
    output_file = skill_meta.get("output_file")
    if not output_file:
        raise RuntimeError(f"output_file not configured in skill.yaml: {skill_yaml_path}")
    output_path = workspace_dir / task_id / str(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_owner(task_id: str, owner_arg: str | None) -> str | None:
    """Resolve the task owner for billing (U-01).

    Prefer an explicit ``--owner-id``; otherwise reverse-lookup from the task
    store by task_id. Returns None when neither is available (best-effort).
    """
    if owner_arg and owner_arg.strip():
        return owner_arg.strip()
    try:
        from app.modules.task_store import store

        task = store.get_task(task_id)
        if task:
            owner = str(task.get("ownerId") or task.get("owner_id") or "").strip()
            if owner:
                return owner
    except Exception:  # noqa: BLE001 — CLI must not fail on billing lookup
        logging.getLogger(__name__).warning(
            "structured-skill owner reverse-lookup failed task=%s", task_id, exc_info=True
        )
    return None


def _record_structured_skill_usage(owner_id: str | None, task_id: str, diagnostics: dict[str, Any]) -> None:
    """Best-effort metering for one structured-skill run (U-04).

    Owner-resolved CLI / runtime-guard paths still burn provider tokens for a
    user task, so settle via ``meter_call`` (not ledger-only ``record_call_usage``).
    """
    if not owner_id:
        return
    usage_wire = {
        "inputTokens": int(diagnostics.get("prompt_tokens") or 0),
        "outputTokens": int(diagnostics.get("completion_tokens") or 0),
        "totalTokens": int(diagnostics.get("total_tokens") or 0),
        "estimated": False,
    }
    if not any(usage_wire[k] for k in ("inputTokens", "outputTokens", "totalTokens")):
        return  # nothing to bookkeep (e.g. python_adapter path or run before any usage)
    try:
        from app.modules.token_ledger_repository import meter_call

        meter_call(
            user_id=owner_id,
            source="structured_skill",
            usage_wire=usage_wire,
            task_id=task_id,
            provider=str(diagnostics.get("provider") or "") or None,
            model=str(diagnostics.get("model") or "") or None,
            status="success",
        )
    except Exception:  # noqa: BLE001 — ledger is a satellite system; never fail the CLI on it
        logging.getLogger(__name__).warning(
            "structured-skill ledger record failed task=%s owner=%s", task_id, owner_id, exc_info=True
        )


def main() -> int:
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-id", required=True)
    parser.add_argument("--workspace-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--owner-id",
        default=None,
        help="task owner for token billing; if omitted, reverse-looked-up from the task store by --task-id",
    )
    args = parser.parse_args()

    try:
        repo_root = _repo_root()
        input_payload = _read_input_payload()
        python_adapter_spec = resolve_python_adapter_skill(args.skill_id)

        if args.skill_id in LLM_SKILLS:
            relative_skill_path = LLM_SKILLS[args.skill_id]
            skill_dir = repo_root / relative_skill_path
            source_contract = _frozen_registry_source_contract(relative_skill_path)
            # LLM input/output validation is owned by run_skill_with_llm so the
            # file-backed schema document context is established exactly once.
            os.environ.setdefault("OPENTRAJECTORY_LLM_PROVIDER", "huiyan_chat_completions")
            output, elapsed, diagnostics = run_skill_with_llm(
                skill_dir=skill_dir,
                input_payload=input_payload,
                workspace_dir=Path(args.workspace_dir),
                task_id=args.task_id,
                source_contract=source_contract,
            )
            diagnostics = {
                **diagnostics,
                "provider": os.environ.get("OPENTRAJECTORY_LLM_PROVIDER", "huiyan_chat_completions"),
                "runtime_mode": "llm_structured",
                "input_schema_passed": True,
                "output_schema_passed": True,
            }
        elif python_adapter_spec is not None:
            canonical_skill_id, relative_skill_path = python_adapter_spec
            skill_dir = repo_root / relative_skill_path
            _validate_payload(
                payload=input_payload,
                schema_path=skill_dir / "input.schema.json",
                label="input",
                repository_root=repo_root,
            )
            started = time.monotonic()
            output = run_python_skill_adapter(
                repo_root=repo_root,
                skill_id=canonical_skill_id,
                skill_path=relative_skill_path,
                input_payload=input_payload,
            )
            elapsed = time.monotonic() - started
            _validate_payload(
                payload=output,
                schema_path=skill_dir / "output.schema.json",
                label="output",
                repository_root=repo_root,
            )
            _write_adapter_output(
                skill_dir=skill_dir,
                output=output,
                workspace_dir=Path(args.workspace_dir),
                task_id=args.task_id,
            )
            diagnostics = {
                "provider": "python_adapter",
                "runtime_mode": "python_adapter",
                "canonical_skill_id": canonical_skill_id,
                "input_schema_passed": True,
                "output_schema_passed": True,
            }
        else:
            raise RuntimeError(f"live execution is not enabled for skill: {args.skill_id}")

        # U-01: best-effort token ledger record for the structured-skill run.
        _record_structured_skill_usage(
            _resolve_owner(args.task_id, args.owner_id), args.task_id, diagnostics
        )

        print(json.dumps({"ok": True, "output": output, "elapsed_seconds": elapsed, "diagnostics": diagnostics}, ensure_ascii=False))
        return 0
    except RuntimeGuardRejected as exc:
        print(json.dumps({
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "diagnostics": {"runtime_guard": exc.diagnostics},
        }, ensure_ascii=False))
        return 1
    except Exception as exc:  # CLI boundary: return a machine-readable failure without secrets.
        failure = _failure_payload(exc)
        print(json.dumps({
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            **failure,
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
