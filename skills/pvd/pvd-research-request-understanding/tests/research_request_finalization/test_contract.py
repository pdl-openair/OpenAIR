from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[2]
PROVIDER_SCHEMA = json.loads((ROOT / "provider.schema.json").read_text(encoding="utf-8"))
OUTPUT_SCHEMA = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
PROVIDER_VALIDATOR = validator_for(PROVIDER_SCHEMA)(PROVIDER_SCHEMA)
OUTPUT_VALIDATOR = validator_for(OUTPUT_SCHEMA)(OUTPUT_SCHEMA)

_spec = importlib.util.spec_from_file_location(
    "pvd_research_request_finalize", ROOT / "scripts" / "finalize.py"
)
assert _spec is not None and _spec.loader is not None
_finalize_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _finalize_module
_spec.loader.exec_module(_finalize_module)
finalize = _finalize_module.finalize


def _parameter(
    *,
    key: str,
    label: str,
    quote: str,
    value_type: str = "continuous",
    scope: str = "active",
    unit: str | None = None,
    unit_identity: str | None = None,
    unit_dimension: str | None = None,
    allowed_values: list[object] | None = None,
    condition: str | None = None,
    observations: list[dict[str, object]] | None = None,
    constraints: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "scope": scope,
        "value_type": value_type,
        "unit": unit,
        "unit_identity": unit_identity,
        "unit_dimension": unit_dimension,
        "allowed_values": list(allowed_values or []),
        "condition": condition,
        "rationale": "当前研究范围中的显式参数",
        "quote": quote,
        "observations": list(observations or []),
        "constraints": list(constraints or []),
    }


def _draft(*, parameters: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "research_facts": [],
        "target_metrics": [],
        "materials": [],
        "system_facts": [],
        "parameter_mentions": parameters,
        "existing_experiments": [],
        "unknowns": [],
        "initial_experiment_set_count": None,
    }
    value.update(overrides)
    return value


def _run(query: str, draft: dict[str, object]) -> dict[str, object]:
    PROVIDER_VALIDATOR.validate(draft)
    result = finalize({"query": query}, draft)
    assert result.status == "succeeded", result.errors
    assert result.output is not None
    OUTPUT_VALIDATOR.validate(result.output)
    return result.output


def test_compact_provider_contract_is_below_407b_limit_and_schemas_are_valid() -> None:
    validator_for(PROVIDER_SCHEMA).check_schema(PROVIDER_SCHEMA)
    validator_for(OUTPUT_SCHEMA).check_schema(OUTPUT_SCHEMA)


def test_subset_scope_conditional_admission_and_missing_system_fields() -> None:
    query = (
        "用Ti靶材在Si基底做磁控溅射，只研究功率和N2流量。"
        "功率100 W，N2流量20 sccm。基底温度先判断是否值得后续纳入。"
        "目标是提高致密度。做3组。"
    )
    output = _run(
        query,
        _draft(
            parameters=[
                _parameter(
                    key="power",
                    label="功率",
                    quote="功率",
                    unit="W",
                    unit_identity="power:W",
                    unit_dimension="power",
                    observations=[{"value": 100, "unit": "W", "quote": "功率100 W"}],
                ),
                _parameter(
                    key="n2_flow",
                    label="N2流量",
                    quote="N2流量",
                    unit="sccm",
                    unit_identity="flow:sccm",
                    unit_dimension="flow",
                    observations=[{"value": 20, "unit": "sccm", "quote": "N2流量20 sccm"}],
                ),
                _parameter(
                    key="substrate_temperature",
                    label="基底温度",
                    quote="基底温度先判断是否值得后续纳入",
                    scope="conditional",
                    unit="°C",
                    unit_identity="temperature:C",
                    unit_dimension="temperature",
                    condition="先判断是否值得后续纳入",
                ),
            ],
            research_facts=[
                {"kind": "objective", "text": "提高致密度", "quote": "提高致密度"},
                {"kind": "process_method", "text": "磁控溅射", "quote": "磁控溅射"},
            ],
            target_metrics=[{"name": "致密度", "direction": "maximize", "quote": "提高致密度"}],
            materials=[
                {"role": "target", "value": "Ti靶材", "quote": "Ti靶材"},
                {"role": "substrate", "value": "Si基底", "quote": "Si基底"},
            ],
            initial_experiment_set_count=3,
        ),
    )

    scope = output["recommendation_parameter_set"]["parameters"]
    assert [row["parameter_key"] for row in scope] == ["power", "n2_flow"]
    assert {row["parameter_key"] for row in scope}.isdisjoint({"ar_flow", "deposition_time", "substrate_temperature"})

    judgments = output["structured_context"]["parameter_admission_judgments"]
    assert len(judgments) == 1
    assert judgments[0]["judgment_id"] == "pvd:parameter_admission:substrate_temperature"
    assert judgments[0]["status"] == "pending_evidence"
    assert judgments[0]["user_intent"] == "evaluate_for_admission"
    assert judgments[0]["provenance"]["raw_expression"] == "基底温度先判断是否值得后续纳入"

    system = output["structured_context"]["experimental_system_context"]
    assert system["target_material"] == "Ti靶材"
    assert system["substrate"] == "Si基底"
    assert system["target_substrate_distance_mm"] is None
    assert system["field_metadata"]["target_substrate_distance_mm"]["status"] == "missing"
    assert system["power_supply_temperature"] is None
    assert system["field_metadata"]["power_supply_temperature"]["status"] == "missing"
    equipment = output["structured_context"]["equipment"]
    assert equipment["substrate_temperature"] == []
    assert equipment["gas_and_pressure"]["chamber_pressure"] == []
    assert output["structured_context"]["hard_constraints"] == []
    assert output["run_preferences"] == {
        "initial_experiment_set_count": 3,
        "source": "user_query",
        "requested_count": 3,
        "capped_to_maximum": False,
    }
    assert output["structured_context"]["research_context"]["process_methods"][0]["statement"] == "磁控溅射"


def test_dynamic_non_core_parameter_types_survive_without_continuous_fallback() -> None:
    query = "研究脉冲次数、靶材类型A或B，以及是否开启偏压。脉冲次数10次，靶材类型A，偏压开启。"
    output = _run(
        query,
        _draft(
            parameters=[
                _parameter(
                    key="pulse_count",
                    label="脉冲次数",
                    quote="脉冲次数",
                    value_type="integer",
                    unit="次",
                    unit_identity="count:cycle",
                    unit_dimension="count",
                    observations=[{"value": 10, "unit": "次", "quote": "脉冲次数10次"}],
                ),
                _parameter(
                    key="target_type",
                    label="靶材类型",
                    quote="靶材类型A或B",
                    value_type="categorical",
                    allowed_values=["A", "B"],
                    observations=[{"value": "A", "unit": None, "quote": "靶材类型A"}],
                ),
                _parameter(
                    key="bias_enabled",
                    label="是否开启偏压",
                    quote="是否开启偏压",
                    value_type="boolean",
                    observations=[{"value": True, "unit": None, "quote": "偏压开启"}],
                ),
            ]
        ),
    )
    scope = {row["parameter_key"]: row for row in output["recommendation_parameter_set"]["parameters"]}
    assert scope["pulse_count"]["value_type"] == "integer"
    assert scope["target_type"]["value_type"] == "categorical"
    assert scope["target_type"]["allowed_values"] == ["A", "B"]
    assert scope["bias_enabled"]["value_type"] == "boolean"
    assert all(scope[key]["allowed_values"] == [] for key in ("pulse_count", "bias_enabled"))


def test_same_call_existing_experiment_preserves_executed_measured_vs_planned_boundary() -> None:
    query = (
        "研究功率。已有真实实验功率100 W、氩气流量30 sccm，测得电阻率2.1。"
        "另计划功率120 W，计划测电阻率。"
    )
    output = _run(
        query,
        _draft(
            parameters=[
                _parameter(
                    key="power",
                    label="功率",
                    quote="研究功率",
                    unit="W",
                    unit_identity="power:W",
                    unit_dimension="power",
                )
            ],
            existing_experiments=[
                {
                    "execution_identity": "completed_physical_execution",
                    "quote": "已有真实实验功率100 W、氩气流量30 sccm，测得电阻率2.1",
                    "parameters": [
                        {"key": "power", "value_type": "continuous", "value": 100, "unit": "W", "quote": "功率100 W"},
                        # A real execution condition remains history even when it is
                        # outside the active recommendation parameter set.
                        {"key": "ar_flow", "value_type": "continuous", "value": 30, "unit": "sccm", "quote": "氩气流量30 sccm"},
                    ],
                    "measurements": [{"metric": "电阻率", "value": 2.1, "unit": None, "quote": "测得电阻率2.1"}],
                },
                {
                    "execution_identity": "planned_or_future",
                    "quote": "另计划功率120 W，计划测电阻率",
                    "parameters": [
                        {"key": "power", "value_type": "continuous", "value": 120, "unit": "W", "quote": "功率120 W"}
                    ],
                    "measurements": [{"metric": "电阻率", "value": "计划测量", "unit": None, "quote": "计划测电阻率"}],
                },
            ],
        ),
    )
    rounds = output["structured_context"]["rounds"]
    assert [row["status"] for row in rounds] == ["measured", "planned"]
    assert [
        p["parameter_key"] for p in rounds[0]["actual_execution"]
    ] == ["power", "ar_flow"]
    assert rounds[0]["actual_execution"][1]["unit_identity"] is None
    assert [
        p["parameter_key"]
        for p in output["recommendation_parameter_set"]["parameters"]
    ] == ["power"]
    assert rounds[0]["measurements"][0]["metric"] == "电阻率"
    assert rounds[1]["planned_execution"][0]["parameter_key"] == "power"
    assert rounds[1]["actual_execution"] == []
    assert rounds[1]["measurements"] == []
    warnings = output["structured_context"]["quality"]["warnings"]
    assert "existing_parameter_dropped:1:2" not in warnings
    assert "planned_measurements_ignored:2" in warnings


def test_identical_physical_experiment_statements_keep_distinct_stable_source_records() -> None:
    statement = "已有真实实验功率100 W，测得电阻率2.1。"
    query = "研究功率。" + statement + statement
    experiment = {
        "execution_identity": "completed_physical_execution",
        "quote": statement,
        "parameters": [
            {
                "key": "power",
                "value_type": "continuous",
                "value": 100,
                "unit": "W",
                "quote": "功率100 W",
            }
        ],
        "measurements": [
            {
                "metric": "电阻率",
                "value": 2.1,
                "unit": None,
                "quote": "测得电阻率2.1",
            }
        ],
    }
    draft = _draft(
        parameters=[
            _parameter(
                key="power",
                label="功率",
                quote="研究功率",
                unit="W",
                unit_identity="power:W",
                unit_dimension="power",
            )
        ],
        existing_experiments=[experiment, experiment],
    )

    first = _run(query, draft)
    replay = _run(query, draft)
    rounds = first["structured_context"]["rounds"]

    assert len(rounds) == 2
    assert rounds[0]["source_record_ref"] != rounds[1]["source_record_ref"]
    assert all(
        row["source_record_ref"].startswith("pvd-query-source-record:sha256:")
        for row in rounds
    )
    assert [row["source_record_ref"] for row in rounds] == [
        row["source_record_ref"]
        for row in replay["structured_context"]["rounds"]
    ]




def test_run_preference_count_is_mechanically_capped_to_product_maximum() -> None:
    query = "研究功率，先做12组。"
    output = _run(
        query,
        _draft(
            parameters=[_parameter(key="power", label="功率", quote="研究功率", unit="W")],
            initial_experiment_set_count=12,
        ),
    )
    assert output["run_preferences"] == {
        "initial_experiment_set_count": 10,
        "source": "user_query",
        "requested_count": 12,
        "capped_to_maximum": True,
    }



def test_skill_contract_requires_stable_parameter_identity_across_history() -> None:
    instructions = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert (
        "四个核心参数稳定身份固定为 `power`、`n2_flow`、`ar_flow`、"
        "`deposition_time`"
    ) in instructions
    assert "`existing_experiments.parameters`" in instructions
    assert "`ar_flow_rate` 等同义 key" in instructions
    assert "不根据关键词、regex、字符串 alias 或表格列位置猜测" in instructions


def test_parameter_identity_preserves_physical_referent_across_contracts() -> None:
    instructions = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "`parameter_key` 必须同时保留用户明确的物理对象和属性" in instructions
    assert "单位、量纲相同或 Schema 中存在另一个字段均不构成同一身份" in instructions
    assert "不得将一个物理对象的属性改写为另一个对象的属性" in instructions

    provider = json.loads((ROOT / "provider.schema.json").read_text(encoding="utf-8"))
    provider_key = provider["$defs"]["pk"]
    assert provider_key["description"] == (
        "Exact physical object and property; units do not imply identity."
    )
    for definition, field in (("f", "field"), ("p", "key"), ("pv", "key")):
        assert provider["$defs"][definition]["properties"][field] == {
            "$ref": "#/$defs/pk"
        }

    output = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
    expected = (
        "Stable scientific identity of the exact physical quantity: preserve the "
        "user-stated physical object and property across scope, constraints, planned "
        "execution, actual execution, and history. Equal units or dimensions and other "
        "schema field names do not make different physical quantities interchangeable."
    )
    for definition in (
        "typedParameterValue",
        "recommendationParameterSpec",
        "parameterAdmissionJudgment",
    ):
        assert (
            output["$defs"][definition]["properties"]["parameter_key"]["description"]
            == expected
        )
