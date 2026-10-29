from __future__ import annotations
import json, os, re, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = Path(__file__).resolve().parents[1]
_REQUIRED_ENV = ("LLM_PROVIDER_API_KEY", "LLM_PROVIDER_BASE_URL")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_missing_env_from_file(env_file: Path) -> bool:
    if not env_file.is_file():
        return False
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            continue
        os.environ.setdefault(key, _unquote(value))
    return True


def _prepare_repo_root_env() -> str:
    if all(os.environ.get(name) for name in _REQUIRED_ENV):
        return "PROCESS_ENV"
    candidates = [(ROOT / ".env", "OPENAIR_REPO_ROOT_DOTENV")]
    fallback_root = (os.environ.get("OPENTRAJECTORY_ENV_FALLBACK_ROOT") or "").strip()
    if fallback_root:
        candidates.append((Path(fallback_root) / ".env", "SKILLLAB_REPO_ROOT_DOTENV_FALLBACK"))
    loaded_sources: list[str] = []
    for env_file, source_name in candidates:
        if _load_missing_env_from_file(env_file):
            loaded_sources.append(source_name)
        if all(os.environ.get(name) for name in _REQUIRED_ENV):
            return "+".join(loaded_sources) if loaded_sources else "PROCESS_ENV"
    searched = ", ".join(str(path) for path, _ in candidates)
    missing = ", ".join(name for name in _REQUIRED_ENV if not os.environ.get(name))
    raise RuntimeError(
        f"P0.8a live smoke requires repository-root .env values for {missing}; searched: {searched}; secret values are never printed"
    )


LIVE_ENV_SOURCE = _prepare_repo_root_env()
request = json.loads((SKILL_DIR / "examples/request.json").read_text(encoding="utf-8"))
env = dict(os.environ)
env["PYTHONPATH"] = str(ROOT / "services/api") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
with tempfile.TemporaryDirectory(prefix="p08a_live_") as tmp:
    cmd = [
        sys.executable,
        str(ROOT / "services/api/app/execution/run_single_structured_skill.py"),
        "--skill-id",
        "research-lead-hypothesis-generation-skill",
        "--workspace-dir",
        tmp,
        "--task-id",
        "p08a-live",
    ]
    proc = subprocess.run(
        cmd,
        input=json.dumps(request, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"P0.8a live smoke failed exit={proc.returncode} stdout={proc.stdout} stderr={proc.stderr}")
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True, payload
    out = payload["output"]
    assert out["direct_answer"]["answer"].strip(), out
    assert len(out["research_leads"]) >= 1, out
    assert len(out["hypotheses"]) >= 1, out
    assert all(x.get("hypothesis_id") and x.get("research_lead_id") and x.get("falsifiable_prediction") for x in out["hypotheses"]), out
    schema = json.loads((SKILL_DIR / "output.schema.json").read_text(encoding="utf-8"))
    expected_trace = schema["properties"]["audit"]["properties"]["traceability_mode"]["const"]
    assert out["audit"]["traceability_mode"] == expected_trace, out
    print("P08A_LIVE_SMOKE=PASS")
    print(f"LIVE_ENV_SOURCE={LIVE_ENV_SOURCE}")
    print(f"LIVE_EVIDENCE_SUPPORTED_CONCLUSION_COUNT={len(out['evidence_supported_conclusions'])}")
    print(f"LIVE_RESEARCH_LEAD_COUNT={len(out['research_leads'])}")
    print(f"LIVE_HYPOTHESIS_COUNT={len(out['hypotheses'])}")
    print(f"LIVE_DIRECT_ANSWER_CONFIDENCE={out['direct_answer']['confidence']}")
    print(f"LIVE_TRACEABILITY_MODE={out['audit']['traceability_mode']}")
