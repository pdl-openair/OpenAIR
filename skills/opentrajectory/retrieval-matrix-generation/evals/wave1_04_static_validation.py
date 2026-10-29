import json
import pathlib
import re

from jsonschema import Draft7Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def assert_valid(data, schema, label):
    errors = list(Draft7Validator(schema).iter_errors(data))
    if errors:
        first = errors[0]
        raise AssertionError(f"{label}: {list(first.path)} {first.message}")


def main():
    input_schema = load_json(ROOT / "input.schema.json")
    model_output_schema = load_json(ROOT / "model_output.schema.json")
    output_schema = load_json(ROOT / "output.schema.json")
    Draft7Validator.check_schema(input_schema)
    Draft7Validator.check_schema(model_output_schema)
    Draft7Validator.check_schema(output_schema)

    assert_valid(load_json(ROOT / "examples/request.json"), input_schema, "example request")
    assert_valid(load_json(ROOT / "examples/response.json"), output_schema, "example response")

    replay = load_json(
        ROOT
        / "tests/replay/p0_effect_eval_from_skill1_outputs/replay_cases_raw.json"
    )
    for case in replay["cases"]:
        assert_valid(case["input"], input_schema, f"existing replay {case['case_id']}")

    eval_set = load_json(ROOT / "evals/wave1_04_cases.json")
    for case in eval_set["boundary_cases"]:
        assert_valid(case["input"], input_schema, f"boundary {case['case_id']}")

    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    guide = (ROOT / "references/intent-guide.md").read_text(encoding="utf-8")
    combined = skill + "\n" + guide

    forbidden_defaults = re.compile(
        r"fluorinated solvent|high-concentration electrolyte|"
        r"localized high-concentration|SEI stabilization|CEI stabilization|dendrite suppression",
        re.I,
    )
    holdout_specific_terms = [
        "single-cell RNA sequencing",
        "scRNA-seq",
        "single-cell transcriptomics",
        "cell type annotation",
        "cell identity classification",
        "graph neural network",
        "GNN",
        "XGBoost",
        "molecular property prediction",
    ]
    combined_casefold = combined.casefold()
    anti_eval_leak = all(
        term.casefold() not in combined_casefold for term in holdout_specific_terms
    )

    checks = {
        "no battery-specific default list": not forbidden_defaults.search(combined),
        "exactly eight variants stated": "恰好 8" in combined,
        "no at-least-eight contradiction": "至少 8" not in combined,
        "inactive intent may be omitted from semantic draft": "没有支持该 intent 的事实时省略该 strategy" in skill,
        "formal empty slot is code owned": "确定性组装器会生成空槽位" in skill,
        "risk flags are not exclusions": "不得转成 query term、NOT 或 `exclude_terms`" in skill,
        "explicit exclusion authority": "用户明确表达的排除项" in guide,
        "source-scope kept out of topical query": "不要把 `English`、年份窗口、`peer-reviewed`" in skill,
        "explicit comparison preserved": "明确 baseline" in skill,
        "AND NOT syntax stated": "AND NOT (...)" in combined,
        "facets may not add facts": "不要引入新事实" in skill,
        "no holdout-specific example leakage": anti_eval_leak,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise AssertionError("instruction preflight failed: " + ", ".join(failed))

    print("STATIC_PREFLIGHT=PASS")
    print(f"EXISTING_REPLAY_INPUTS={len(replay['cases'])}/{len(replay['cases'])}")
    print(f"BOUNDARY_INPUTS={len(eval_set['boundary_cases'])}/{len(eval_set['boundary_cases'])}")
    print(f"INSTRUCTION_CHECKS={len(checks)}/{len(checks)}")
    print(f"SKILL_BYTES={len((ROOT / 'SKILL.md').read_bytes())}")
    print("NOTE=Static preflight only; this is not a live-provider effect score and does not prove 97+.")


if __name__ == "__main__":
    main()
