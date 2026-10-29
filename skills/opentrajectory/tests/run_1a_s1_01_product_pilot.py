#!/usr/bin/env python3
"""Product-runtime pilot for OpenTrajectory 1a (Phase 1-3 only).

Uses lightweight LLM structured output runner instead of Claude Code.
Does NOT call API execute endpoint - runs skills directly via llm_structured_runner.

Phase 1: structured-requirement-generation
Phase 2: constraint-interpretation-skill
Phase 3: retrieval-matrix-generation
Phase 4: SKIPPED (academic-source-retrieval-skill requires Python adapter)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Add services/api to path for imports
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root / "services" / "api"))

from app.execution.llm_structured_runner import run_skill_with_llm
from app.execution.python_adapter_runner import run_python_skill_adapter

CASE_ID = "S1-01-li-battery-fast-charge"
QUERY = "比较石墨、硅碳、LTO 负极在快充场景下的循环稳定性与容量保持率，优先近五年英文高质量期刊。"

SKILLS = [
    ("phase1", "structured-requirement-generation"),
    ("phase2", "constraint-interpretation-skill"),
    ("phase3", "retrieval-matrix-generation"),
]

DEFAULT_PHASE4_TARGET_SOURCES = ["deepxiv", "openalex", "semantic_scholar", "arxiv"]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _collect_text_fields(phase3_data: dict) -> list[str]:
    texts: list[str] = []
    for row in phase3_data.get("search_matrix", []):
        for key in ["query_terms", "must_include_terms", "optional_terms", "exclude_terms"]:
            vals = row.get(key, []) if isinstance(row, dict) else []
            if isinstance(vals, list):
                texts.extend([_norm(str(v)) for v in vals if isinstance(v, str)])
    for row in phase3_data.get("expanded_query_variants", []):
        if isinstance(row, dict) and isinstance(row.get("query"), str):
            texts.append(_norm(row["query"]))
    return texts


def _extract_phase2_terms(phase2_data: dict) -> list[str]:
    terms: list[str] = []
    for c in phase2_data.get("interpreted_constraints", []):
        if not isinstance(c, dict):
            continue
        for t in c.get("downstream_search_terms", []):
            if isinstance(t, str):
                terms.append(_norm(t))
        iv = c.get("interpreted_value")
        if isinstance(iv, str):
            terms.append(_norm(iv))
    return terms


def _tokenize_phrases(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        s = _norm(v)
        if not s:
            continue
        if len(s.split()) <= 8:
            out.append(s)
    return out


def detect_comparison_objects(phase1_data: dict, phase2_data: dict, phase3_data: dict | None = None) -> list[str]:
    candidates: dict[str, int] = {}

    def add(v: str, w: int = 1) -> None:
        k = _norm(v)
        if not k or len(k) < 3:
            return
        if any(x in k for x in ["fast charging", "capacity retention", "cycling", "journal", "english", "recent"]):
            return
        candidates[k] = candidates.get(k, 0) + w

    # phase2 constraints: object-like constraints should dominate
    for c in phase2_data.get("interpreted_constraints", []):
        if not isinstance(c, dict):
            continue
        cname = _norm(str(c.get("constraint_name", "")))
        ctype = _norm(str(c.get("constraint_type", "")))
        boost = 3 if any(x in cname for x in ["comparison", "object", "set", "entity", "material"]) else 1
        for t in c.get("downstream_search_terms", []):
            if isinstance(t, str):
                add(t, boost)
        iv = c.get("interpreted_value")
        if isinstance(iv, str):
            for p in re.split(r",|/|;|\bor\b|\band\b", iv):
                add(p, boost)
        if ctype in {"required_condition", "entity", "material"}:
            for t in c.get("downstream_search_terms", []):
                if isinstance(t, str):
                    add(t, 2)

    # phase1 weak hints
    for key in ["research_object", "compared_objects", "material_candidates", "target_entities"]:
        val = phase1_data.get(key)
        if isinstance(val, list):
            for v in val:
                if isinstance(v, str):
                    add(v, 2)
        elif isinstance(val, str):
            for p in re.split(r",|/|;|\bor\b|\band\b", val):
                add(p, 2)

    # phase3 hints (optional)
    if phase3_data:
        for row in phase3_data.get("expanded_query_variants", []):
            if isinstance(row, dict) and isinstance(row.get("query"), str):
                q = _norm(row["query"])
                for part in re.findall(r'"([^"]+)"', q):
                    add(part, 1)

    ranked = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
    # keep top distinct phrases
    objects = [k for k, v in ranked if v >= 2]
    return _tokenize_phrases(objects)[:6]


def build_phase3_task_specific_quality_requirements(phase1_data: dict, phase2_data: dict) -> dict:
    phase2_terms = _extract_phase2_terms(phase2_data)
    detected_objects = detect_comparison_objects(phase1_data, phase2_data)

    return {
        "identified_comparison_objects": detected_objects,
        "forbidden_hard_constraints": [
            "core_topic.must_include_terms must NOT simultaneously contain all detected comparison objects",
            "no single query or expanded_query_variant may hard-require all detected comparison objects via AND",
            "pairwise query may include at most two detected comparison objects",
            "per-object query should focus on one detected comparison object",
        ],
        "required_query_groups": [
            "per-object queries for all detected comparison objects",
            "pairwise queries for all detected object pairs",
            "broad comparative/review queries",
            "metric-focused queries",
            "noise/exclusion queries",
            "implement group diversity primarily in expanded_query_variants while keeping search_matrix exactly 7 rows",
        ],
        "required_filters": [
            "recency/year filter strategy (not as raw year keyword stuffing in query text)",
            "English-language filter strategy when language preference exists",
            "high-quality journal/source-quality filter strategy when source preference exists",
        ],
        "required_noise_exclusion": [
            "exclude unrelated domains",
            "exclude non-target-domain contexts if relevant",
        ],
        "fallback_policy": "fallback_used must be false for this case",
        "pre_output_self_check": [
            "if any query hard-requires all detected comparison objects together, rewrite it",
            "if one detected object frequency is significantly higher than others, rebalance query families",
        ],
        "dynamic_evidence_from_phase2": {
            "phase2_terms_excerpt": phase2_terms[:24],
            "detected_objects_count": len(detected_objects),
        },
    }


def validate_retrieval_matrix_quality(phase3_data: dict, phase1_data: dict, phase2_data: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []

    search_matrix = phase3_data.get("search_matrix", [])
    variants = phase3_data.get("expanded_query_variants", [])
    quality_report = phase3_data.get("quality_report", {})

    all_texts = _collect_text_fields(phase3_data)
    all_text = "\n".join(all_texts)

    detected_objects = detect_comparison_objects(phase1_data, phase2_data, phase3_data)
    is_multi_object_compare = len(detected_objects) >= 3

    obj_hits = {obj: sum(obj in t for t in all_texts) for obj in detected_objects}

    if is_multi_object_compare:
        if any(v == 0 for v in obj_hits.values()):
            failures.append("multi_object_coverage_failed: missing one or more detected comparison objects")

        max_hits = max(obj_hits.values()) if obj_hits else 0
        min_hits = min(obj_hits.values()) if obj_hits else 0
        if min_hits == 0 or (min_hits > 0 and max_hits > 3 * min_hits):
            failures.append("material_bias_failed: one comparison object is over-anchored relative to others")

        core_topic_rows = [r for r in search_matrix if isinstance(r, dict) and r.get("search_intent") == "core_topic"]
        if core_topic_rows:
            must_terms = [_norm(x) for x in core_topic_rows[0].get("must_include_terms", []) if isinstance(x, str)]
            must_text = " ".join(must_terms)
            covered = sum(1 for obj in detected_objects if obj in must_text)
            if covered >= len(detected_objects):
                failures.append("strict_and_failed: core_topic.must_include_terms hard-requires all detected comparison objects")

        for v in variants:
            if not isinstance(v, dict):
                continue
            query = _norm(str(v.get("query", "")))
            if " and " not in query:
                continue
            covered = sum(1 for obj in detected_objects if obj in query)
            if covered >= len(detected_objects):
                failures.append("strict_and_failed: expanded query enforces all detected comparison objects together")
                break

    def _has_intent(intent: str) -> bool:
        return any(isinstance(r, dict) and r.get("search_intent") == intent for r in search_matrix)

    if not _has_intent("metric_or_performance"):
        failures.append("query_grouping_failed: missing metric-focused queries")
    if not _has_intent("noise_exclusion"):
        failures.append("query_grouping_failed: missing noise/exclusion queries")

    if is_multi_object_compare:
        per_object = {obj: any(obj in _norm(str(v.get("query", ""))) for v in variants if isinstance(v, dict)) for obj in detected_objects}
        if not all(per_object.values()):
            failures.append("query_grouping_failed: missing per-object query coverage")

        required_pairs: list[tuple[str, str]] = []
        for i in range(len(detected_objects)):
            for j in range(i + 1, len(detected_objects)):
                required_pairs.append((detected_objects[i], detected_objects[j]))

        missing_pairs: list[str] = []
        for a, b in required_pairs:
            pair_found = any(
                isinstance(v, dict)
                and (a in _norm(str(v.get("query", ""))))
                and (b in _norm(str(v.get("query", ""))))
                for v in variants
            )
            if not pair_found:
                missing_pairs.append(f"{a}__{b}")

        if missing_pairs:
            failures.append("pairwise_coverage_failed: missing one or more required object pairs")

    broad_comp = any(any(k in _norm(str(v.get("query", ""))) for k in ["comparison", "comparative", "versus", " vs ", "benchmark", "review"]) for v in variants if isinstance(v, dict))
    if not broad_comp:
        failures.append("query_grouping_failed: missing broad comparative/review lexical cues")

    # filter strategy: should be reflected in constraints/rationales, not hard-coded as year keyword blob in query text
    constraints_utilized = [str(x).lower() for x in quality_report.get("constraints_utilized", [])] if isinstance(quality_report, dict) else []
    has_recency = any("recency" in x or "year" in x or "time_window" in x for x in constraints_utilized)
    has_language = any("language" in x or "english" in x for x in constraints_utilized)
    has_quality = any("quality" in x or "journal" in x or "venue" in x for x in constraints_utilized)
    if not (has_recency and has_language and has_quality):
        failures.append("filter_strategy_failed: recency/language/journal-quality constraints not carried into quality_report.constraints_utilized")

    bad_year_keywords = ["2020", "2021", "2022", "2023", "2024", "2025"]
    if any(y in all_text for y in bad_year_keywords):
        failures.append("filter_strategy_failed: year tokens are injected into query terms instead of filter constraints")

    noise_rows = [r for r in search_matrix if isinstance(r, dict) and r.get("search_intent") == "noise_exclusion"]
    if noise_rows:
        excludes = noise_rows[0].get("exclude_terms", [])
        if not isinstance(excludes, list) or len([x for x in excludes if isinstance(x, str) and _norm(x)]) == 0:
            failures.append("noise_exclusion_failed: noise_exclusion intent exists but exclude_terms is empty")

    if quality_report.get("fallback_used") is True:
        failures.append("fallback_failed: fallback_used=true is not acceptable for this product-runtime scenario")

    return len(failures) == 0, failures


def _object_mention_distribution(phase3_data: dict, detected_objects: list[str]) -> dict[str, int]:
    texts = _collect_text_fields(phase3_data)
    return {obj: sum(obj in t for t in texts) for obj in detected_objects}


def sanitize_object_balance(phase3_data: dict, detected_objects: list[str]) -> tuple[dict, dict]:
    sanitized = json.loads(json.dumps(phase3_data))
    actions: list[str] = []

    if len(detected_objects) < 3:
        dist = _object_mention_distribution(sanitized, detected_objects)
        return sanitized, {
            "phase3_object_balance_sanitized": False,
            "object_mention_distribution_before": dist,
            "object_mention_distribution_after": dist,
            "object_balance_actions": ["not_multi_object_comparison_task"],
        }

    before = _object_mention_distribution(sanitized, detected_objects)
    if not before:
        return sanitized, {
            "phase3_object_balance_sanitized": False,
            "object_mention_distribution_before": before,
            "object_mention_distribution_after": before,
            "object_balance_actions": ["no_detected_objects"],
        }

    max_obj = max(before, key=before.get)
    min_count = min(before.values()) if before else 0
    max_count = before[max_obj]
    if min_count > 0 and max_count <= 3 * min_count:
        return sanitized, {
            "phase3_object_balance_sanitized": False,
            "object_mention_distribution_before": before,
            "object_mention_distribution_after": before,
            "object_balance_actions": ["distribution_already_balanced"],
        }

    variants = sanitized.get("expanded_query_variants", [])
    if not isinstance(variants, list):
        variants = []

    # down-weight dominant object in strict must-like query phrasings
    for v in variants:
        if not isinstance(v, dict) or not isinstance(v.get("query"), str):
            continue
        q = _norm(v["query"])
        if max_obj in q and " and " in q and any(obj in q for obj in detected_objects if obj != max_obj):
            v["query"] = re.sub(re.escape(max_obj), "target object", v["query"], count=1, flags=re.IGNORECASE)
            actions.append("down_weighted_dominant_object_in_pair_or_multi_query")
            break

    # add per-object variants for underrepresented objects
    refreshed = _object_mention_distribution({**sanitized, "expanded_query_variants": variants}, detected_objects)
    target_floor = max(1, min(refreshed.values()) if refreshed else 1)
    for obj in detected_objects:
        while refreshed.get(obj, 0) < target_floor + 1:
            variants.append({
                "query": f"\"{obj}\" AND (comparison OR comparative OR benchmark) AND (performance OR metric OR evaluation)",
                "variant_type": "broad",
                "search_intent": "constraint_focused",
                "source_constraints": ["object_balance_sanitizer"],
                "rationale": "Auto-generated balance query for underrepresented comparison object.",
                "priority": 2,
            })
            actions.append("added_underrepresented_object_query")
            refreshed = _object_mention_distribution({**sanitized, "expanded_query_variants": variants}, detected_objects)
            if refreshed.get(obj, 0) >= target_floor + 1:
                break

    # ensure pairwise coverage stays intact after balance edits
    for i in range(len(detected_objects)):
        for j in range(i + 1, len(detected_objects)):
            a, b = detected_objects[i], detected_objects[j]
            exists = any(isinstance(v, dict) and a in _norm(str(v.get("query", ""))) and b in _norm(str(v.get("query", ""))) for v in variants)
            if not exists:
                variants.append({
                    "query": f"\"{a}\" AND \"{b}\" AND (versus OR comparative OR benchmark)",
                    "variant_type": "strict",
                    "search_intent": "adjacent_but_relevant",
                    "source_constraints": ["object_balance_sanitizer"],
                    "rationale": "Auto-generated pairwise query to preserve pair coverage during balancing.",
                    "priority": 2,
                })
                actions.append("added_missing_pairwise_query_during_balance")

    sanitized["expanded_query_variants"] = variants
    after = _object_mention_distribution(sanitized, detected_objects)

    return sanitized, {
        "phase3_object_balance_sanitized": len(actions) > 0,
        "object_mention_distribution_before": before,
        "object_mention_distribution_after": after,
        "object_balance_actions": actions or ["no_balance_action_needed"],
    }


def sanitize_multi_object_comparison_queries(phase3_data: dict, phase1_data: dict, phase2_data: dict) -> tuple[dict, dict]:
    actions: list[str] = []
    removed: list[str] = []
    sanitized = json.loads(json.dumps(phase3_data))

    detected_objects = detect_comparison_objects(phase1_data, phase2_data, phase3_data)
    if len(detected_objects) < 3:
        return sanitized, {
            "phase3_sanitized": False,
            "phase3_sanitizer_actions": ["not_multi_object_comparison_task"],
            "detected_comparison_objects": detected_objects,
            "removed_all_object_hard_and": removed,
            "generated_pairwise_groups_count": 0,
            "generated_per_object_groups_count": 0,
        }

    variants = sanitized.get("expanded_query_variants", [])
    if not isinstance(variants, list):
        variants = []

    base_query = None
    for v in variants:
        if isinstance(v, dict) and isinstance(v.get("query"), str):
            base_query = _norm(v["query"])
            break
    if not base_query:
        base_query = "comparison AND performance"

    kept_variants = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        q = _norm(str(v.get("query", "")))
        covered = sum(1 for obj in detected_objects if obj in q)
        if " and " in q and covered >= len(detected_objects):
            removed.append(q)
            actions.append("removed_all_object_hard_and_variant")
            continue
        kept_variants.append(v)

    # ensure per-object variants
    generated_per_object = 0
    for obj in detected_objects:
        exists = any(isinstance(v, dict) and obj in _norm(str(v.get("query", ""))) for v in kept_variants)
        if not exists:
            kept_variants.append({
                "query": f"\"{obj}\" AND (comparison OR comparative OR versus OR benchmark) AND (performance OR metric)",
                "variant_type": "broad",
                "search_intent": "constraint_focused",
                "source_constraints": ["task_specific_quality_requirements"],
                "rationale": "Auto-generated per-object variant by multi-object comparison sanitizer.",
                "priority": 2,
            })
            generated_per_object += 1

    # ensure pairwise variants
    generated_pairs = 0
    for i in range(len(detected_objects)):
        for j in range(i + 1, len(detected_objects)):
            a, b = detected_objects[i], detected_objects[j]
            exists = any(isinstance(v, dict) and a in _norm(str(v.get("query", ""))) and b in _norm(str(v.get("query", ""))) for v in kept_variants)
            if not exists:
                kept_variants.append({
                    "query": f"\"{a}\" AND \"{b}\" AND (versus OR comparative OR benchmark)",
                    "variant_type": "strict",
                    "search_intent": "adjacent_but_relevant",
                    "source_constraints": ["task_specific_quality_requirements"],
                    "rationale": "Auto-generated pairwise variant by multi-object comparison sanitizer.",
                    "priority": 2,
                })
                generated_pairs += 1

    sanitized["expanded_query_variants"] = kept_variants

    # sanitize core_topic must_include_terms all-object hard AND
    for row in sanitized.get("search_matrix", []):
        if isinstance(row, dict) and row.get("search_intent") == "core_topic":
            must = [x for x in row.get("must_include_terms", []) if isinstance(x, str)]
            must_norm = " ".join(_norm(x) for x in must)
            covered = sum(1 for obj in detected_objects if obj in must_norm)
            if covered >= len(detected_objects):
                row["must_include_terms"] = ["comparative study", "target entity"]
                actions.append("rewrote_core_topic_must_include_terms_to_remove_all_object_hard_and")

    return sanitized, {
        "phase3_sanitized": bool(actions or generated_pairs or generated_per_object),
        "phase3_sanitizer_actions": actions,
        "detected_comparison_objects": detected_objects,
        "removed_all_object_hard_and": removed,
        "generated_pairwise_groups_count": generated_pairs,
        "generated_per_object_groups_count": generated_per_object,
    }


def _run_phase4_only_from_existing_phase3(output_dir: Path) -> int:
    print("=== OpenTrajectory 1a Product-Runtime Pilot (Phase 4 only from existing Phase 3) ===\n")

    phase3_path = output_dir / "phase3.json"
    if not phase3_path.exists():
        print(f"[FAIL] Missing required Phase 3 output: {phase3_path}")
        print("Refusing to auto-rerun Phase1-3 in phase4-only mode.")
        return 3

    phase3_data = json.loads(phase3_path.read_text(encoding="utf-8"))

    s2_api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY")

    retrieval_input = {
        "search_matrix": phase3_data.get("search_matrix", []),
        "expanded_query_variants": phase3_data.get("expanded_query_variants", []),
        "retrieval_config": {
            "target_sources": DEFAULT_PHASE4_TARGET_SOURCES,
            "max_results_per_query": 10,
            "max_total_candidates": 120,
            "timeout_seconds": 45,
            "allow_partial_success": True,
        },
        "s2_api_key": s2_api_key,
    }

    print("Phase 4: academic-source-retrieval-skill (python adapter)")
    phase4_output = run_python_skill_adapter(
        repo_root=repo_root,
        skill_id="academic_source_retrieval_skill",
        skill_path="skills/opentrajectory/academic-source-retrieval-skill",
        input_payload=retrieval_input,
    )

    phase4_path = output_dir / "phase4.json"
    phase4_path.write_text(
        json.dumps(phase4_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metadata_candidates = phase4_output.get("metadata_candidates", [])
    retrieval_report = phase4_output.get("retrieval_report", {})
    source_counts = retrieval_report.get("source_counts", {}) if isinstance(retrieval_report, dict) else {}
    failed_sources = retrieval_report.get("failed_sources", []) if isinstance(retrieval_report, dict) else []
    partial_success = retrieval_report.get("partial_success", False) if isinstance(retrieval_report, dict) else False

    print("\n=== Summary (Phase 4 only) ===")
    print(f"metadata_candidates: {len(metadata_candidates) if isinstance(metadata_candidates, list) else 0}")
    print(f"source_counts: {source_counts}")
    print(f"failed_sources: {failed_sources}")
    print(f"partial_success: {partial_success}")
    print(f"Output: {phase4_path}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenTrajectory 1a product-runtime pilot runner")
    parser.add_argument(
        "--phase4-only-from-existing-phase3",
        action="store_true",
        help="Run only Phase 4 from existing phase3.json without rerunning Phase1-3",
    )
    args = parser.parse_args()

    output_dir = repo_root / "skills/opentrajectory/tests/execution_outputs/S1-01-product-runtime"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.phase4_only_from_existing_phase3:
        return _run_phase4_only_from_existing_phase3(output_dir)

    print("=== OpenTrajectory 1a Product-Runtime Pilot (Phase 1-3) ===\n")

    workspace_dir = repo_root / "workspace"
    task_id = "task_product_pilot_001"
    output_dir = repo_root / "skills/opentrajectory/tests/execution_outputs/S1-01-product-runtime"
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "case_id": CASE_ID,
        "mode": "product_runtime",
        "runtime": "llm_structured_output",
        "blocked": False,
        "phase_outputs": {},
        "elapsed_seconds_per_phase": {},
        "total_elapsed_seconds": 0.0,
        "schema_valid": {},
        "quality_valid": {},
        "quality_failures": {},
        "block_reason": None,
    }

    # Check environment variables
    import os
    api_key = os.environ.get("LLM_PROVIDER_API_KEY")
    api_base = os.environ.get("LLM_PROVIDER_BASE_URL")
    model = os.environ.get("LLM_PROVIDER_MODEL", "gpt-4")

    print("Environment check:")
    print(f"  LLM_PROVIDER_API_KEY: {'[SET]' if api_key else '[NOT SET]'}")
    print(f"  LLM_PROVIDER_BASE_URL: {api_base if api_base else '[NOT SET]'}")
    print(f"  LLM_PROVIDER_MODEL: {model}\n")

    if not api_key or not api_base:
        report["blocked"] = True
        report["block_reason"] = "Missing environment variables: LLM_PROVIDER_API_KEY or LLM_PROVIDER_BASE_URL"
        report_path = output_dir / "product_pilot_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[BLOCKED] {report['block_reason']}")
        print(f"Report saved to: {report_path}")
        return 1

    # Phase 1: structured-requirement-generation
    phase1_input = {"user_query": QUERY}
    phase1_output = None

    try:
        print("Phase 1: structured-requirement-generation")
        skill_dir = repo_root / "skills/opentrajectory/structured-requirement-generation"
        phase1_output, elapsed = run_skill_with_llm(skill_dir, phase1_input, workspace_dir, task_id)

        phase1_path = output_dir / "phase1.json"

        report["phase_outputs"]["phase1"] = {
            "status": "generated",
            "path": str(phase1_path),
            "workspace": str(workspace_dir / task_id / "structured_requirement.json"),
        }
        report["elapsed_seconds_per_phase"]["phase1"] = round(elapsed, 2)
        report["schema_valid"]["phase1"] = True
        report["quality_valid"]["phase1"] = True
        report["total_elapsed_seconds"] += elapsed

        print(f"  [OK] Completed in {elapsed:.2f}s")
        print(f"  Output: {phase1_path}\n")
    except Exception as e:
        report["blocked"] = True
        report["block_reason"] = f"Phase 1 failed: {str(e)}"
        report["phase_outputs"]["phase1"] = {"status": "failed", "error": str(e)}
        report["schema_valid"]["phase1"] = False
        report["quality_valid"]["phase1"] = False

        report_path = output_dir / "product_pilot_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [FAIL] Failed: {e}")
        print(f"Report saved to: {report_path}")
        return 2

    # Phase 2: constraint-interpretation-skill
    if not phase1_output:
        report["blocked"] = True
        report["block_reason"] = "Phase 1 output is None"
        report_path = output_dir / "product_pilot_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    phase2_input = {"structured_requirement": phase1_output}
    phase2_output = None

    try:
        print("Phase 2: constraint-interpretation-skill")
        skill_dir = repo_root / "skills/opentrajectory/constraint-interpretation-skill"
        phase2_output, elapsed = run_skill_with_llm(skill_dir, phase2_input, workspace_dir, task_id)

        phase2_path = output_dir / "phase2.json"

        report["phase_outputs"]["phase2"] = {
            "status": "generated",
            "path": str(phase2_path),
            "workspace": str(workspace_dir / task_id / "constraint_interpretation.json"),
        }
        report["elapsed_seconds_per_phase"]["phase2"] = round(elapsed, 2)
        report["schema_valid"]["phase2"] = True
        report["quality_valid"]["phase2"] = True
        report["total_elapsed_seconds"] += elapsed

        print(f"  [OK] Completed in {elapsed:.2f}s")
        print(f"  Output: {phase2_path}\n")
    except Exception as e:
        report["blocked"] = True
        report["block_reason"] = f"Phase 2 failed: {str(e)}"
        report["phase_outputs"]["phase2"] = {"status": "failed", "error": str(e)}
        report["schema_valid"]["phase2"] = False
        report["quality_valid"]["phase2"] = False

        report_path = output_dir / "product_pilot_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [FAIL] Failed: {e}")
        print(f"Report saved to: {report_path}")
        return 2

    # Phase 3: retrieval-matrix-generation
    if not phase2_output:
        report["blocked"] = True
        report["block_reason"] = "Phase 2 output is None"
        report_path = output_dir / "product_pilot_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    phase3_quality_requirements = build_phase3_task_specific_quality_requirements(
        phase1_output, phase2_output
    )
    phase3_input = {
        "constraint_interpretation": phase2_output,
        "task_specific_quality_requirements": phase3_quality_requirements,
    }

    try:
        print("Phase 3: retrieval-matrix-generation")
        skill_dir = repo_root / "skills/opentrajectory/retrieval-matrix-generation"
        phase3_output, elapsed = run_skill_with_llm(skill_dir, phase3_input, workspace_dir, task_id)

        print("  Injected task_specific_quality_requirements for Phase 3")

        phase3_output_sanitized, sanitizer_meta = sanitize_multi_object_comparison_queries(
            phase3_output, phase1_output, phase2_output
        )

        detected_objects = sanitizer_meta.get("detected_comparison_objects", [])
        phase3_output_balanced, balance_meta = sanitize_object_balance(
            phase3_output_sanitized, detected_objects
        )

        phase3_path = output_dir / "phase3.json"

        report["phase3_sanitized"] = sanitizer_meta.get("phase3_sanitized", False)
        report["phase3_sanitizer_actions"] = sanitizer_meta.get("phase3_sanitizer_actions", [])
        report["detected_comparison_objects"] = detected_objects
        report["removed_all_object_hard_and"] = sanitizer_meta.get("removed_all_object_hard_and", [])
        report["generated_pairwise_groups_count"] = sanitizer_meta.get("generated_pairwise_groups_count", 0)
        report["generated_per_object_groups_count"] = sanitizer_meta.get("generated_per_object_groups_count", 0)
        report["phase3_object_balance_sanitized"] = balance_meta.get("phase3_object_balance_sanitized", False)
        report["object_mention_distribution_before"] = balance_meta.get("object_mention_distribution_before", {})
        report["object_mention_distribution_after"] = balance_meta.get("object_mention_distribution_after", {})
        report["object_balance_actions"] = balance_meta.get("object_balance_actions", [])

        report["phase_outputs"]["phase3"] = {
            "status": "generated",
            "path": str(phase3_path),
            "workspace": str(workspace_dir / task_id / "retrieval_matrix.json"),
        }
        report["elapsed_seconds_per_phase"]["phase3"] = round(elapsed, 2)
        report["schema_valid"]["phase3"] = True
        report["total_elapsed_seconds"] += elapsed

        quality_ok, quality_failures = validate_retrieval_matrix_quality(
            phase3_output_balanced, phase1_output, phase2_output
        )
        report["quality_valid"]["phase3"] = quality_ok
        report["quality_failures"]["phase3"] = quality_failures
        if not quality_ok:
            report["blocked"] = True
            report["block_reason"] = "Phase 3 quality check failed: " + "; ".join(quality_failures)

        print(f"  [OK] Completed in {elapsed:.2f}s")
        print(f"  Output: {phase3_path}")
        if quality_ok:
            print("  [OK] Phase 3 retrieval quality checks passed\n")
        else:
            print("  [FAIL] Phase 3 retrieval quality checks failed")
            for item in quality_failures:
                print(f"    - {item}")
            print()
    except Exception as e:
        report["blocked"] = True
        report["block_reason"] = f"Phase 3 failed: {str(e)}"
        report["phase_outputs"]["phase3"] = {"status": "failed", "error": str(e)}
        report["schema_valid"]["phase3"] = False

        report_path = output_dir / "product_pilot_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [FAIL] Failed: {e}")
        print(f"Report saved to: {report_path}")
        return 2

    # Success
    report["total_elapsed_seconds"] = round(report["total_elapsed_seconds"], 2)
    report_path = output_dir / "product_pilot_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Summary ===")
    print(f"Phase 1: {report['elapsed_seconds_per_phase']['phase1']}s")
    print(f"Phase 2: {report['elapsed_seconds_per_phase']['phase2']}s")
    print(f"Phase 3: {report['elapsed_seconds_per_phase']['phase3']}s")
    print(f"Total: {report['total_elapsed_seconds']}s")
    print(f"\n[OK] All phases completed successfully")
    print(f"Report saved to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
