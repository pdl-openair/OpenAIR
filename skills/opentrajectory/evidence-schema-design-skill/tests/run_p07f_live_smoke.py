from __future__ import annotations
import json, os, re, sys, tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api"))
from app.execution.llm_structured_runner import run_skill_with_llm

_REQUIRED_ENV = ("LLM_PROVIDER_API_KEY", "LLM_PROVIDER_BASE_URL")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_GAPS = {"missing_mechanism", "missing_condition", "missing_metric", "missing_baseline", "conflict", "not_comparable"}

MATERIAL_ROLE_GROUPS = [
    {"composition"},
    {"condition"},
    {"metric"},
    {"mechanism"},
    {"characterization"},
    {"theoretical_explanation"},
    {"baseline", "comparability"},
]

def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value

def _load_missing_env_from_file(path: Path) -> bool:
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and _ENV_KEY.fullmatch(key):
            os.environ.setdefault(key, _unquote(value))
    return True

def _prepare_env() -> str:
    if all(os.environ.get(x) for x in _REQUIRED_ENV):
        return "PROCESS_ENV"
    candidates = [(ROOT / ".env", "OPENAIR_REPO_ROOT_DOTENV")]
    fallback = (os.environ.get("OPENTRAJECTORY_ENV_FALLBACK_ROOT") or "").strip()
    if fallback:
        candidates.append((Path(fallback) / ".env", "FALLBACK_ROOT_DOTENV"))
    loaded = []
    for path, name in candidates:
        if _load_missing_env_from_file(path):
            loaded.append(name)
        if all(os.environ.get(x) for x in _REQUIRED_ENV):
            return "+".join(loaded) if loaded else "PROCESS_ENV"
    missing = ", ".join(x for x in _REQUIRED_ENV if not os.environ.get(x))
    raise RuntimeError(f"P0.7f live smoke requires {missing}; secret values are never printed")

def _read(name):
    return json.loads((SKILL_DIR / "examples" / name).read_text(encoding="utf-8"))

def _roles(output):
    return {row["scientific_role"] for row in output["evidence_schema"]["evidence_targets"]}

def _assert_role_groups(actual_roles, groups):
    for group in groups:
        assert actual_roles.intersection(group), (actual_roles, group)

def _assert_output(output, case):
    schema = output["evidence_schema"]
    targets = schema["evidence_targets"]
    ids = [x["target_id"] for x in targets]
    assert targets and len(ids) == len(set(ids)), output
    assert set(schema["gap_taxonomy"]).issubset(ALLOWED_GAPS), output
    assert not any(key in output for key in ("evidence_items", "mechanism_edges", "research_leads", "hypotheses")), output

    if case == "materials":
        assert len(targets) >= 6, targets
        _assert_role_groups(_roles(output), MATERIAL_ROLE_GROUPS)
        assert {"missing_mechanism", "missing_condition", "missing_metric", "missing_baseline", "not_comparable"}.issubset(set(schema["gap_taxonomy"])), schema
        assert schema["uncertainty_rules"], schema

    if case == "cs":
        assert len(targets) >= 5, targets
        _assert_role_groups(_roles(output), [{"condition"}, {"metric"}, {"baseline", "comparability"}, {"limitation"}])
        assert schema["uncertainty_rules"], schema

def main():
    env_source = _prepare_env()
    run_root = Path(tempfile.gettempdir()) / "opentrajectory-p07f-live-runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)
    for case, request_name in (("materials", "request.materials.json"), ("cs", "request.cs.json")):
        request = _read(request_name)
        output, elapsed, diagnostics = run_skill_with_llm(
            skill_dir=SKILL_DIR,
            input_payload=request,
            workspace_dir=run_root,
            task_id=case,
        )
        case_dir = run_root / case
        case_dir.mkdir(parents=True, exist_ok=True)
        (run_root / f"{case}-result.raw.json").write_text(
            json.dumps({"output": output, "elapsed_seconds": elapsed, "diagnostics": diagnostics}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (case_dir / "evidence_schema.raw.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            _assert_output(output, case)
        except Exception:
            print(f"P07F_LIVE_CONTENT_QUALITY_GATE=FAIL")
            print(f"P07F_LIVE_FAILED_CASE={case}")
            print(f"P07F_LIVE_SMOKE_RUN_DIR={run_root}")
            raise
        (run_root / f"{case}-result.json").write_text(
            json.dumps({"output": output, "elapsed_seconds": elapsed, "diagnostics": diagnostics}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (case_dir / "evidence_schema.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print("P07F_LIVE_SMOKE=PASS")
    print("P07F_LIVE_CONTENT_QUALITY_GATE=PASS")
    print(f"P07F_LIVE_ENV_SOURCE={env_source}")
    print(f"P07F_LIVE_SMOKE_RUN_DIR={run_root}")
    print("P07F_SECRET_VALUE_PRINTED=NO")

if __name__ == "__main__":
    main()
