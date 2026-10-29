"""Run PV2 and adjacent protection and retain reproducible raw evidence.

Use the task-specified existing Python with PYTHONPATH=services/api;packages/llm_core.
Run from repository root. This collector does not approve blocking policy.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).parent
PRODUCTION = [
    "planner_semantic_content.py",
    "planner_target_associations.py",
    "research_design_workflow_semantics.py",
    "research_design_freeform_semantics.py",
    "research_design_planner_assembly.py",
    "target_output_ownership.py",
    "research_design_compiler.py",
    "research_design_service.py",
    "research_design_workflow_semantic_provider.py",
    "research_design_freeform_semantic_provider.py",
    "research_design_agent_revision.py",
    "research_finalization.py",
    "target_output_completion.py",
    "research_completion_decision.py",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest():
    files = [ROOT / "services/api/app/modules" / name for name in PRODUCTION]
    files.extend(sorted((ROOT / "schemas/workflows").glob("*.yaml")))
    files.extend([ROOT / "skills/registry.yaml"])
    return {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path) for path in files}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_command(destination, name, arguments, environment):
    command = [sys.executable, *arguments]
    with (destination / f"{name}.log").open("wb") as output:
        result = subprocess.run(
            command, cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT, check=False
        )
    return {
        "command": command,
        "exitCode": result.returncode,
        "output": f"{name}.log",
        "outputSha256": digest(destination / f"{name}.log"),
    }


def collect_diff(destination):
    paths = [f"services/api/app/modules/{name}" for name in PRODUCTION]
    result = subprocess.run(
        ["git", "diff", "HEAD", "--", *paths], cwd=ROOT, capture_output=True, check=True
    )
    contents = result.stdout
    for name in ("planner_semantic_content.py", "planner_target_associations.py"):
        path = ROOT / "services/api/app/modules" / name
        contents += b"\n# NEW FILE: " + name.encode() + b"\n" + path.read_bytes()
    with gzip.open(destination / "candidate-production.patch.gz", "wb") as stream:
        stream.write(contents)


def forbidden(*_args, **_kwargs):
    raise AssertionError("PV2 audit forbids live HTTP and business database access")


def main():
    destination = HERE / "evidence" / (sys.argv[1] if len(sys.argv) > 1 else "candidate")
    destination.mkdir(parents=True, exist_ok=True)
    evidence = destination / "stages.jsonl.gz"
    if evidence.exists():
        raise FileExistsError("Preserve earlier evidence; choose a new run directory")
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "services/api") + os.pathsep + str(ROOT / "packages/llm_core"),
            "PYTHONIOENCODING": "utf-8",
            "OPENAIR_REPO_ROOT": str(ROOT),
            "OPENAIR_ENV": "test",
            "OPENAIR_DEV_SEED_FIXTURES": "0",
            "LLM_PROVIDER_MODEL": "test-model",
            "LLM_PROVIDER_MODEL_TERRA": "test-model",
            "LLM_PROVIDER_BASE_URL": "https://example.test",
            "LLM_PROVIDER_API_KEY": "test-key",
        }
    )
    os.environ.update(environment)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    before = manifest()
    dump(destination / "source-before.json", before)
    collect_diff(destination)
    own_environment = {**environment, "PV2_EVIDENCE_PATH": str(evidence)}
    commands = [
        run_command(
            destination,
            "pv2",
            [
                "-m",
                "pytest",
                str(HERE.relative_to(ROOT)),
                "-q",
                "-p",
                "no:cacheprovider",
                "--tb=short",
                f"--junitxml={destination / 'pv2.xml'}",
            ],
            own_environment,
        )
    ]
    if "--suite-only" not in sys.argv:
        commands.append(
            run_command(
                destination,
                "adjacent",
                [
                    "-m",
                    "pytest",
                    "services/api/app/modules/tests/target_output_ownership",
                    "services/api/app/modules/tests/research_design_workflow_planner",
                    "services/api/app/modules/tests/research_design_freeform_semantics",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "--tb=short",
                    f"--junitxml={destination / 'adjacent.xml'}",
                ],
                environment,
            )
        )
    import httpx
    import psycopg
    from app.modules.production_definition_source import ProductionDefinitionSource
    from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository
    from app.modules.tests.planner_semantic_handoff.audit import blocking_probes

    os.environ["PV2_EVIDENCE_PATH"] = str(evidence)
    with (
        patch.object(httpx.Client, "send", forbidden),
        patch.object(psycopg, "connect", forbidden),
        patch.object(psycopg.Connection, "connect", forbidden),
    ):
        source = ProductionDefinitionSource(
            archive=InMemoryRegistrySnapshotRepository(), planning_only=True
        )
        dump(destination / "blocking-probes.json", blocking_probes(source))
        dump(
            destination / "registry-snapshot-ref.json",
            source.snapshot_ref(),
        )
    after = manifest()
    dump(destination / "source-after.json", after)
    dump(
        destination / "run.json",
        {
            "head": head,
            "candidateCommitted": False,
            "sourceStable": before == after,
            "commands": commands,
            "python": sys.executable,
            "artifacts": {
                path.name: digest(path) for path in destination.iterdir() if path.is_file()
            },
        },
    )
    print(json.dumps({"commands": commands, "sourceStable": before == after}, ensure_ascii=False))


if __name__ == "__main__":
    main()
