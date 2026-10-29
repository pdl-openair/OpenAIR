"""Static code-quality gates for candidate-source-scoring production files."""

from __future__ import annotations

import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapter" / "scoring_adapter.py"
INPUT_SCHEMA = ROOT / "input.schema.json"
OUTPUT_SCHEMA = ROOT / "output.schema.json"

FORBIDDEN_PRODUCTION_TOKENS = (
    "deduped_candidates",
    "research_evidence_contract",
    '"evidence_contract"',
    '"contract"',
    "screening_to_scoring_bridge",
    "map_screened_to_scoring_input",
    "compatibility",
    "fallback",
    "shim",
)
FORBIDDEN_IMPORT_ROOTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "subprocess",
    "sqlalchemy",
    "boto3",
    "openai",
}
FORBIDDEN_OUTPUT_AUTHORITY = {
    "tier",
    "pool",
    "topk_selection",
    "selected_for_p03",
}


def main() -> int:
    adapter_text = ADAPTER.read_text(encoding="utf-8")
    schemas_text = INPUT_SCHEMA.read_text(encoding="utf-8") + "\n" + OUTPUT_SCHEMA.read_text(encoding="utf-8")
    production_text = (adapter_text + "\n" + schemas_text).lower()

    for token in FORBIDDEN_PRODUCTION_TOKENS:
        assert token.lower() not in production_text, f"forbidden production token: {token}"

    tree = ast.parse(adapter_text)
    import_roots: set[str] = set()
    function_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_names.append(node.name.lower())

    assert not (import_roots & FORBIDDEN_IMPORT_ROOTS), "adapter imports external/runtime integration dependencies"
    assert not any(any(word in name for word in ("bridge", "mapper", "compat", "fallback")) for name in function_names)

    input_schema = json.loads(INPUT_SCHEMA.read_text(encoding="utf-8"))
    assert input_schema["required"] == ["screened_candidates", "research_context"]
    assert set(input_schema["properties"]) == {"screened_candidates", "research_context"}

    output_schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    score_item = output_schema["properties"]["candidate_scores"]["items"]
    assert not (FORBIDDEN_OUTPUT_AUTHORITY & set(score_item["properties"]))
    assert not (FORBIDDEN_OUTPUT_AUTHORITY & set(output_schema["properties"]))

    adapter_lines = adapter_text.count("\n") + 1
    assert adapter_lines < 400, f"adapter unexpectedly large: {adapter_lines} lines"

    print("CODE_VALIDATION = PASS")
    print("NO_BRIDGE_MAPPER_COMPAT_FALLBACK = PASS")
    print("NO_EXTERNAL_RUNTIME_DEPENDENCY = PASS")
    print("SINGLE_PUBLIC_INPUT_CONTRACT = PASS")
    print("NO_TIERING_TOPK_AUTHORITY = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
