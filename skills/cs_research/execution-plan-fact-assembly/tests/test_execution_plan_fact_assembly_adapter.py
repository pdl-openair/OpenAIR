from pathlib import Path
import sys

SKILL_DIR = Path(__file__).parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
from adapter.execution_plan_fact_assembly_adapter import run_execution_plan_fact_assembly as run  # noqa: E402

MODULE = SKILL_DIR / "adapter" / "execution_plan_fact_assembly_adapter.py"

DOC_ID="refdoc:"+"a"*64
REF_ID="refmetric:"+"b"*64
CONTENT="""Install torch==2.4.0 and transformers==4.44.0. Verify imports torch and transformers.\nRun python evaluate.py --model demo/model --output results.json from the repository root.\nThe model is demo/model at revision 0123456789012345678901234567890123456789.\nResults are written to results.json as JSON field score.\n"""

def source():
    return {"schema_version":"openair-reference-source-package/v1","state":"succeeded","source_kind":"official_repository","repository":"https://example.invalid/repo.git","requested_revision":"main","resolved_commit_sha":"1"*40,"workspace_relative_path":"code/repo","documents":[{"document_id":DOC_ID,"path":"README.md","sha256":"2"*64,"line_count":4,"content":CONTENT}]}

def refs():
    return [{"reference_id":REF_ID,"metric_name":"score","reference_value":0.9,"value_type":"number","unit":None,"experiment_conditions":["eval"],"source":{},"comparison_rule_status":"grounded","comparison_rule":{"kind":"exact","basis":{}}}]

def fact(value,excerpt): return {"value":value,"source_document_id":DOC_ID,"source_excerpt":excerpt}

def interpretation():
    return {
      "resources":[{"usage":"evaluation_model","execution_usage":"model","resource_kind":"huggingface_snapshot","repo_id":"demo/model","repo_type":"model","revision":"0123456789012345678901234567890123456789","filename":None,"expected_commit_sha":None,"expected_sha256":None,"delivery":{"kind":"command_argument","argument_index":1},"source_document_id":DOC_ID,"source_excerpt":"The model is demo/model at revision 0123456789012345678901234567890123456789."}],
      "environment":{"python_version":None,"requirements":[fact("torch==2.4.0","Install torch==2.4.0 and transformers==4.44.0."),fact("transformers==4.44.0","Install torch==2.4.0 and transformers==4.44.0.")],"verify_imports":[fact("torch","Verify imports torch and transformers."),fact("transformers","Verify imports torch and transformers.")],"find_links":None},
      "execution":{"entrypoint":fact("evaluate.py","Run python evaluate.py --model demo/model --output results.json from the repository root."),"arguments":fact(["--model","demo/model","--output","results.json"],"Run python evaluate.py --model demo/model --output results.json from the repository root."),"working_directory":fact(".","from the repository root"),"timeout_seconds":None,"environment_variables":None},
      "metric_extraction_jobs":[{"reference_ids":[REF_ID],"result_source":{"kind":"workspace_file","relative_path":"results.json","format":"json","table":None,"source_document_id":DOC_ID,"source_excerpt":"Results are written to results.json as JSON field score."},"selectors":[{"reference_id":REF_ID,"selector":{"json_pointer":"/score"}}]}],
      "unresolved":[]}

def test_grounded_plan_separates_source_system_and_runtime_pending():
    out=run({"source_package":source(),"interpretation":interpretation(),"reference_metrics":refs()})
    assert out["state"]=="succeeded" and out["plan_status"]=="complete"
    assert out["resource_acquisition_requests"][0]["repo_id"]=="demo/model"
    assert out["argv"][2]=="{{openair-resource:model}}"
    assert out["working_directory"]=="code/repo"
    assert out["actual_metric_requests"]==[{"metric_name":"score","value_type":"number","unit":None,"selector":{"json_pointer":"/score"}}]
    assert "workspace_result_expected_sha256" in out["execution_plan_runtime_pending"]
    assert any(p.get("origin")=="official_source" for p in out["execution_plan_provenance"])
    assert any(p.get("origin")=="system_selection" and p.get("target")=="python_version" for p in out["execution_plan_provenance"])

def test_unknown_required_fact_stays_unknown_and_incomplete():
    data=interpretation(); data["environment"]["requirements"]=None
    out=run({"source_package":source(),"interpretation":data,"reference_metrics":refs()})
    assert out["plan_status"]=="incomplete" and out["requirements"] is None
    assert {"target":"requirements","reason":"official_source_unspecified"} in out["execution_plan_unresolved"]


def test_model_cannot_invent_metric_identity():
    data=interpretation(); data["metric_extraction_jobs"][0]["reference_ids"]=["refmetric:"+"c"*64]
    out=run({"source_package":source(),"interpretation":data,"reference_metrics":refs()})
    assert out["actual_metric_requests"]==[] and out["plan_status"]=="incomplete"
    assert any("unknown_reference_id" in x["reason"] for x in out["execution_plan_unresolved"])

def test_output_contains_no_case_specific_rule():
    text=MODULE.read_text(encoding="utf-8").lower()
    assert "simcse" not in text and "senteval" not in text


def test_complete_plan_matches_existing_downstream_input_contracts_after_runtime_materialization():
    import json
    from jsonschema import Draft7Validator

    research_root = SKILL_DIR.parent
    out = run({"source_package": source(), "interpretation": interpretation(), "reference_metrics": refs()})
    assert out["plan_status"] == "complete"

    environment_schema = json.loads(
        (research_root / "environment-setup" / "input.schema.json").read_text(encoding="utf-8")
    )
    environment_payload = {
        key: out[key]
        for key in (
            "python_version",
            "requirements",
            "verify_imports",
            "dependency_install_timeout_seconds",
            "find_links",
        )
    }
    Draft7Validator(environment_schema).validate(environment_payload)

    resource_schema = json.loads(
        (research_root / "resource-identity-acquisition" / "input.schema.json").read_text(encoding="utf-8")
    )
    Draft7Validator(resource_schema).validate({"resources": out["resource_acquisition_requests"]})

    run_schema = json.loads(
        (research_root / "exploratory-run" / "input.schema.json").read_text(encoding="utf-8")
    )
    run_payload = {
        key: out[key]
        for key in (
            "executable",
            "argv",
            "working_directory",
            "timeout_seconds",
            "environment_variables",
        )
    }
    Draft7Validator(run_schema).validate(run_payload)

    extraction_schema = json.loads(
        (research_root / "actual-metric-extraction" / "input.schema.json").read_text(encoding="utf-8")
    )
    job = out["metric_extraction_jobs"][0]
    result_source = dict(job["result_source_plan"])
    assert result_source["kind"] == "workspace_file"
    result_source["expected_sha256"] = "0" * 64
    Draft7Validator(extraction_schema).validate(
        {
            "execution_record_relative_path": "execution/example/execution-record.json",
            "result_source": result_source,
            "metrics": job["metrics"],
        }
    )
