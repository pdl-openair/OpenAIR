"""Registry-driven deterministic finalization for structured Skills.

A Skill owns deterministic post-LLM rules. Canonical registration declares the
finalizer entrypoint and semantic-draft schema; the Registry freezes those
artifacts into SkillDefinition.sourceContract. Runtime executes only that frozen
contract and always validates the finalized value against the frozen formal
output schema.
"""

from __future__ import annotations

from app.modules.schema_source_cache import validate_schema_document

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Mapping

from jsonschema.validators import validator_for

FINALIZATION_CONTRACT_VERSION = 1
FINALIZER_TIMEOUT_SECONDS = 15
_DECLARATION_KEYS = frozenset({"contract_version", "entrypoint", "draft_schema"})
_ENTRYPOINT_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):"
    r"(?P<callable>[A-Za-z_][A-Za-z0-9_]*)$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_RESULT_STATUSES = frozenset({"succeeded", "partial", "degraded"})


class SkillFinalizationError(RuntimeError):
    """Fail-closed finalization failure with bounded machine diagnostics."""

    def __init__(self, code: str, *, errors: tuple[Any, ...] = ()) -> None:
        self.code = code
        bounded_errors: list[Any] = []
        for item in errors[:24]:
            if isinstance(item, Mapping):
                serialized = json.dumps(dict(item), ensure_ascii=False, separators=(",", ":"))
                if len(serialized) <= 2000:
                    bounded_errors.append(json.loads(serialized))
                else:
                    bounded_errors.append(
                        {"code": "structured_error_truncated", "detail": serialized[:1900]}
                    )
            else:
                bounded_errors.append(str(item)[:500])
        self.errors = tuple(bounded_errors)
        self.diagnostics = {
            "finalization": {
                "code": self.code,
                "errors": list(self.errors),
            }
        }
        super().__init__(code)


@dataclass(frozen=True)
class FinalizedSkillOutput:
    output: dict[str, Any]
    status: str
    errors: tuple[Any, ...] = ()
    unfinalized: bool = False


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative_json_path(value: Any, *, label: str) -> PurePosixPath:
    text = str(value or "").strip()
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
        or path.suffix.lower() != ".json"
    ):
        raise ValueError(f"{label}_invalid")
    return path


def _entrypoint_parts(value: Any) -> tuple[str, str]:
    match = _ENTRYPOINT_RE.fullmatch(str(value or "").strip())
    if match is None:
        raise ValueError("finalization_entrypoint_invalid")
    return match.group("module"), match.group("callable")


def _module_path(skill_root: Path, module_name: str) -> Path:
    relative = PurePosixPath(*module_name.split(".")).with_suffix(".py")
    root = skill_root.resolve()
    candidate = (root / relative.as_posix()).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("finalization_entrypoint_outside_skill")
    return candidate


def _module_declares_callable(module_path: Path, callable_name: str) -> bool:
    try:
        module_ast = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("finalization_entrypoint_invalid_python") from exc
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == callable_name
        for node in module_ast.body
    )


def _finalizer_code_sha256(skill_root: Path, module_name: str) -> str:
    """Fingerprint the Skill-local Python package containing the entrypoint.

    The scope is derived only from registered entrypoint metadata. For an
    entrypoint under ``scripts.*`` this fingerprints every Python file in that
    Skill-local package so helper-module changes cannot silently escape the
    frozen Registry identity.
    """
    module_path = _module_path(skill_root, module_name)
    top_level = (skill_root / module_name.split(".", 1)[0]).resolve()
    if top_level.is_dir():
        code_files = sorted(path for path in top_level.rglob("*.py") if path.is_file())
    else:
        code_files = [module_path]
    if not code_files or module_path not in code_files:
        raise ValueError("finalization_code_unavailable")
    digest = hashlib.sha256()
    root = skill_root.resolve()
    for path in code_files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_finalization_declaration(value: Any) -> dict[str, Any]:
    """Validate canonical Registry finalization declaration metadata."""
    if not isinstance(value, Mapping) or set(value) != _DECLARATION_KEYS:
        raise ValueError("finalization_contract_shape_invalid")
    if value.get("contract_version") != FINALIZATION_CONTRACT_VERSION:
        raise ValueError("finalization_contract_version_invalid")
    module_name, callable_name = _entrypoint_parts(value.get("entrypoint"))
    draft_path = _safe_relative_json_path(
        value.get("draft_schema"), label="finalization_draft_schema"
    )
    return {
        "contract_version": FINALIZATION_CONTRACT_VERSION,
        "entrypoint": f"{module_name}:{callable_name}",
        "draft_schema": draft_path.as_posix(),
    }


def build_frozen_finalization_contract(
    skill_path: str,
    declaration: Any,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    """Freeze one registered Skill-local finalizer into immutable Registry data."""
    normalized = validate_finalization_declaration(declaration)
    skill_root = (repo_root / str(skill_path or "")).resolve()
    root = repo_root.resolve()
    if skill_root == root or root not in skill_root.parents:
        raise ValueError("finalization_skill_path_invalid")

    module_name, callable_name = _entrypoint_parts(normalized["entrypoint"])
    module_path = _module_path(skill_root, module_name)
    draft_path = (skill_root / normalized["draft_schema"]).resolve()
    if not module_path.is_file():
        raise ValueError("finalization_entrypoint_unavailable")
    if not _module_declares_callable(module_path, callable_name):
        raise ValueError("finalization_callable_unavailable")
    if draft_path != skill_root and skill_root not in draft_path.parents:
        raise ValueError("finalization_draft_schema_outside_skill")
    if not draft_path.is_file():
        raise ValueError("finalization_draft_schema_unavailable")

    try:
        draft_schema = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("finalization_draft_schema_invalid") from exc
    if not isinstance(draft_schema, dict):
        raise ValueError("finalization_draft_schema_invalid")
    validate_schema_document(draft_schema)

    return {
        "contractVersion": FINALIZATION_CONTRACT_VERSION,
        "entrypoint": {"module": module_name, "callable": callable_name},
        "codeSha256": _finalizer_code_sha256(skill_root, module_name),
        "draftSchema": {
            "path": normalized["draft_schema"],
            "sha256": _sha256_bytes(_canonical_json_bytes(draft_schema)),
            "schema": draft_schema,
        },
    }


def validate_frozen_finalization_contract(value: Any) -> dict[str, Any]:
    """Validate and normalize the immutable runtime view of one finalizer."""
    if not isinstance(value, Mapping) or set(value) != {
        "contractVersion",
        "entrypoint",
        "codeSha256",
        "draftSchema",
    }:
        raise ValueError("frozen_finalization_contract_shape_invalid")
    if value.get("contractVersion") != FINALIZATION_CONTRACT_VERSION:
        raise ValueError("frozen_finalization_contract_version_invalid")

    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, Mapping) or set(entrypoint) != {"module", "callable"}:
        raise ValueError("frozen_finalization_entrypoint_invalid")
    module_name, callable_name = _entrypoint_parts(
        f"{entrypoint.get('module', '')}:{entrypoint.get('callable', '')}"
    )

    code_sha = str(value.get("codeSha256") or "").lower()
    if _SHA256_RE.fullmatch(code_sha) is None:
        raise ValueError("frozen_finalization_code_digest_invalid")

    draft = value.get("draftSchema")
    if not isinstance(draft, Mapping) or set(draft) != {"path", "sha256", "schema"}:
        raise ValueError("frozen_finalization_draft_schema_invalid")
    draft_path = _safe_relative_json_path(
        draft.get("path"), label="frozen_finalization_draft_schema"
    )
    draft_sha = str(draft.get("sha256") or "").lower()
    schema = draft.get("schema")
    if _SHA256_RE.fullmatch(draft_sha) is None or not isinstance(schema, dict):
        raise ValueError("frozen_finalization_draft_schema_invalid")
    validate_schema_document(schema)
    if _sha256_bytes(_canonical_json_bytes(schema)) != draft_sha:
        raise ValueError("frozen_finalization_draft_schema_digest_mismatch")

    return {
        "contractVersion": FINALIZATION_CONTRACT_VERSION,
        "entrypoint": {"module": module_name, "callable": callable_name},
        "codeSha256": code_sha,
        "draftSchema": {
            "path": draft_path.as_posix(),
            "sha256": draft_sha,
            "schema": schema,
        },
    }


def _registered_entrypoint_path(
    *,
    skill_root: Path,
    frozen_contract: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    contract = validate_frozen_finalization_contract(frozen_contract)
    entrypoint = contract["entrypoint"]
    module_path = _module_path(skill_root, entrypoint["module"])
    if not module_path.is_file():
        raise SkillFinalizationError("finalizer_entrypoint_unavailable")
    try:
        live_digest = _finalizer_code_sha256(skill_root, entrypoint["module"])
    except ValueError as exc:
        raise SkillFinalizationError("finalizer_code_unavailable") from exc
    if live_digest != contract["codeSha256"]:
        raise SkillFinalizationError("finalizer_code_snapshot_mismatch")
    return module_path, contract


def _execute_finalizer_subprocess(
    *,
    skill_root: Path,
    contract: Mapping[str, Any],
    input_payload: Mapping[str, Any],
    draft_payload: Mapping[str, Any],
) -> Any:
    # Each Skill package commonly exposes a top-level ``scripts`` package. A
    # short isolated process prevents Python module-cache collisions between
    # different Skill packages while keeping the shared runtime generic.
    #
    # The parent/child JSON bridge must not depend on the host locale.  Inputs
    # are already ASCII-escaped; keep outputs ASCII-escaped as well so Windows
    # GBK/cp936, UTF-8 and other ASCII-compatible process encodings transport
    # exactly the same JSON value.
    worker = r"""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import traceback

skill_root, module_name, callable_name = sys.argv[1:4]


def bounded_error(exc):
    frames = traceback.extract_tb(exc.__traceback__)
    source = {}
    if frames:
        frame = frames[-1]
        try:
            filename = Path(frame.filename).resolve()
            root = Path(skill_root).resolve()
            if filename == root or root in filename.parents:
                display_file = filename.relative_to(root).as_posix()
            else:
                display_file = filename.name
        except Exception:
            display_file = Path(str(frame.filename)).name
        source = {
            "file": str(display_file)[:240],
            "line": int(frame.lineno),
            "function": str(frame.name)[:160],
        }
    message = str(exc)
    return {
        "code": "finalizer_child_exception",
        "exceptionType": type(exc).__name__[:160],
        "messageSha256": hashlib.sha256(
            message.encode("utf-8", errors="backslashreplace")
        ).hexdigest(),
        "messageChars": len(message),
        "source": source,
    }


try:
    sys.path.insert(0, skill_root)
    payload = json.load(sys.stdin)
    module = importlib.import_module(module_name)
    fn = getattr(module, callable_name)
    result = fn(payload["input"], payload["draft"])
    if isinstance(result, dict):
        wire = {"kind": "mapping", "output": result}
    else:
        wire = {
            "kind": "result",
            "status": str(getattr(result, "status", "") or ""),
            "output": getattr(result, "output", None),
            "errors": list(getattr(result, "errors", ()) or ()),
        }
    encoded = json.dumps(wire, ensure_ascii=True, separators=(",", ":"))
except Exception as exc:
    encoded = json.dumps(
        {"kind": "error", "error": bounded_error(exc)},
        ensure_ascii=True,
        separators=(",", ":"),
    )

sys.stdout.write(encoded)
"""
    entrypoint = contract["entrypoint"]
    payload = json.dumps(
        {"input": dict(input_payload), "draft": dict(draft_payload)},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                worker,
                str(skill_root),
                str(entrypoint["module"]),
                str(entrypoint["callable"]),
            ],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=FINALIZER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillFinalizationError("finalizer_execution_timeout") from exc
    if proc.returncode != 0:
        stderr = str(proc.stderr or "")
        raise SkillFinalizationError(
            "finalizer_execution_failed",
            errors=(
                {
                    "code": "finalizer_subprocess_nonzero",
                    "returnCode": int(proc.returncode),
                    "stderrSha256": hashlib.sha256(
                        stderr.encode("utf-8", errors="backslashreplace")
                    ).hexdigest(),
                    "stderrChars": len(stderr),
                },
            ),
        )
    try:
        wire = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SkillFinalizationError(
            "finalizer_result_invalid",
            errors=(
                {
                    "code": "finalizer_result_json_invalid",
                    "stdoutSha256": hashlib.sha256(
                        str(proc.stdout or "").encode("utf-8", errors="backslashreplace")
                    ).hexdigest(),
                    "stdoutChars": len(str(proc.stdout or "")),
                },
            ),
        ) from exc
    if not isinstance(wire, dict):
        raise SkillFinalizationError("finalizer_result_invalid")
    if wire.get("kind") == "error":
        error = wire.get("error")
        bounded = (
            dict(error) if isinstance(error, Mapping) else {"code": "finalizer_child_exception"}
        )
        raise SkillFinalizationError("finalizer_execution_failed", errors=(bounded,))
    if wire.get("kind") == "mapping":
        return wire.get("output")
    if wire.get("kind") == "result":
        return type(
            "FinalizerWireResult",
            (),
            {
                "status": wire.get("status"),
                "output": wire.get("output"),
                "errors": wire.get("errors") or (),
            },
        )()
    raise SkillFinalizationError("finalizer_result_invalid")


def _normalize_result(value: Any) -> FinalizedSkillOutput:
    if isinstance(value, Mapping):
        return FinalizedSkillOutput(output=dict(value), status="succeeded")

    status = str(getattr(value, "status", "") or "").strip().lower()
    output = getattr(value, "output", None)
    raw_errors = getattr(value, "errors", ())
    errors = (
        tuple(dict(item) if isinstance(item, Mapping) else str(item) for item in raw_errors)
        if isinstance(raw_errors, (list, tuple))
        else ()
    )
    if status == "failed":
        raise SkillFinalizationError("finalizer_rejected", errors=errors)
    if status not in _ALLOWED_RESULT_STATUSES or not isinstance(output, Mapping):
        raise SkillFinalizationError("finalizer_result_invalid")
    return FinalizedSkillOutput(output=dict(output), status=status, errors=errors)


def execute_registered_finalizer(
    *,
    repo_root: Path,
    skill_path: str,
    frozen_contract: Mapping[str, Any],
    input_payload: Mapping[str, Any],
    draft_payload: Mapping[str, Any],
) -> FinalizedSkillOutput:
    """Execute exactly the finalizer frozen in the Registry snapshot."""
    root = repo_root.resolve()
    skill_root = (root / str(skill_path or "")).resolve()
    if skill_root == root or root not in skill_root.parents:
        raise SkillFinalizationError("finalizer_skill_path_invalid")
    _module_path_value, contract = _registered_entrypoint_path(
        skill_root=skill_root,
        frozen_contract=frozen_contract,
    )
    result = _execute_finalizer_subprocess(
        skill_root=skill_root,
        contract=contract,
        input_payload=input_payload,
        draft_payload=draft_payload,
    )
    return _normalize_result(result)


def _required_schema_key_errors(instance: Any, schema: Mapping[str, Any]) -> tuple[str, ...]:
    schema_dict = dict(schema)
    validator_cls = validator_for(schema_dict)
    validator_cls.check_schema(schema_dict)
    errors = sorted(
        (error for error in validator_cls(schema_dict).iter_errors(instance)
         if error.validator == "required"),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    output: list[str] = []
    for error in errors[:12]:
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        output.append(f"{path or '/'}:{error.message}")
    return tuple(output)


def finalize_and_validate_registered_output(
    *,
    repo_root: Path,
    skill_path: str,
    frozen_contract: Mapping[str, Any] | None,
    input_payload: Mapping[str, Any],
    draft_payload: Mapping[str, Any],
    final_schema: Mapping[str, Any],
) -> FinalizedSkillOutput:
    """Finalize valid drafts; preserve schema gaps as explicitly unfinalized material.

    A degraded result is usable context, not validated formal scientific facts.
    Runtime, identity and deterministic finalizer execution failures remain failures.
    """
    if frozen_contract is None:
        candidate = FinalizedSkillOutput(output=dict(draft_payload), status="succeeded")
    else:
        contract = validate_frozen_finalization_contract(frozen_contract)
        draft_errors = _required_schema_key_errors(draft_payload, contract["draftSchema"]["schema"])
        if draft_errors:
            return FinalizedSkillOutput(
                output=dict(draft_payload), status="degraded", errors=draft_errors, unfinalized=True
            )
        try:
            candidate = execute_registered_finalizer(
                repo_root=repo_root,
                skill_path=skill_path,
                frozen_contract=contract,
                input_payload=input_payload,
                draft_payload=draft_payload,
            )
        except SkillFinalizationError as exc:
            if exc.code not in {
                "finalizer_rejected", "finalizer_result_invalid",
                "finalizer_execution_failed", "finalizer_execution_timeout",
            }:
                raise
            return FinalizedSkillOutput(
                output=dict(draft_payload), status="degraded", errors=exc.errors or (exc.code,), unfinalized=True
            )

    final_errors = _required_schema_key_errors(candidate.output, final_schema)
    if final_errors:
        return FinalizedSkillOutput(
            output=dict(candidate.output), status="degraded", errors=final_errors, unfinalized=True
        )
    return candidate
