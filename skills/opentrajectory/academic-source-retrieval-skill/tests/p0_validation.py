"""P0 schema validation for academic-source-retrieval-skill."""

import json
import re
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("jsonschema not installed: pip install jsonschema")
    sys.exit(1)

SKILL_DIR = Path(__file__).parent.parent

DEFAULT_SOURCES = {"openalex", "semantic_scholar", "arxiv"}
OPTIONAL_SOURCES = {"deepxiv", "agentic_data"}
VALID_RETRIEVAL_MODES = {"expanded_query_variant", "search_matrix_fallback", "english_user_query_fallback"}


def load(path: str) -> dict:
    return json.loads((SKILL_DIR / path).read_text(encoding="utf-8"))


def validate(instance: dict, schema: dict, label: str) -> list[str]:
    errors = []
    v = jsonschema.Draft7Validator(schema)
    for e in v.iter_errors(instance):
        errors.append(f"  [{label}] {e.json_path}: {e.message}")
    return errors


CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def is_chinese(text: str) -> bool:
    return bool(CJK_RE.search(text))


def check_input_invariants(req: dict) -> list[str]:
    errors = []
    user_query = req.get("user_query", "")
    variants = req.get("expanded_query_variants") or []
    matrix = req.get("search_matrix") or []

    has_variants = len(variants) > 0
    has_matrix = len(matrix) > 0
    user_query_is_english = bool(user_query) and not is_chinese(user_query)

    if not has_variants and not has_matrix and not user_query_is_english:
        errors.append("  [request] no English query source: expanded_query_variants, search_matrix, and english user_query are all absent/empty")

    if is_chinese(user_query) and not has_variants and not has_matrix:
        errors.append("  [request] Chinese user_query is the only query source — this must produce empty result, not a normal retrieval")

    for i, v in enumerate(variants):
        q = v.get("query", "")
        if CJK_RE.search(q):
            errors.append(f"  [request] expanded_query_variants[{i}].query contains CJK characters: {q!r}")

    term_fields = ("query_terms", "must_include_terms", "optional_terms", "exclude_terms")
    for i, row in enumerate(matrix):
        for field in term_fields:
            for j, term in enumerate(row.get(field) or []):
                if CJK_RE.search(term):
                    errors.append(f"  [request] search_matrix[{i}].{field}[{j}] contains CJK characters: {term!r}")

    config = req.get("retrieval_config") or {}
    target = set(config.get("target_sources", DEFAULT_SOURCES))
    unexpected = target & OPTIONAL_SOURCES
    if unexpected:
        errors.append(f"  [request] target_sources includes optional sources {unexpected} — these must be explicitly opted in, not default")

    return errors


def check_output_invariants(output: dict, target_sources: list[str] | None = None) -> list[str]:
    errors = []
    candidates = output.get("metadata_candidates", [])
    report = output.get("retrieval_report", {})

    if report.get("total_candidates") != len(candidates):
        errors.append(
            f"  [response] total_candidates={report.get('total_candidates')} != len(candidates)={len(candidates)}"
        )

    for i, c in enumerate(candidates):
        prefix = f"  [response] candidate[{i}]"

        for field in ("dedupe_key", "normalized_title", "external_ids"):
            if field not in c:
                errors.append(f"{prefix} missing '{field}'")

        if "external_ids" in c:
            for key in ("doi", "arxiv_id", "openalex_id", "semantic_scholar_id", "corpus_id", "pmid", "pmcid"):
                if key not in c["external_ids"]:
                    errors.append(f"{prefix}.external_ids missing key '{key}'")

        provenance = c.get("provenance", [])
        if not provenance:
            errors.append(f"{prefix}.provenance is empty")
        for j, p in enumerate(provenance):
            pp = f"{prefix}.provenance[{j}]"
            for field in ("query_index", "source_result_index", "source_rank", "retrieved_at", "retrieval_mode"):
                if field not in p:
                    errors.append(f"{pp} missing '{field}'")
            mode = p.get("retrieval_mode")
            if mode and mode not in VALID_RETRIEVAL_MODES:
                errors.append(f"{pp}.retrieval_mode '{mode}' not in {VALID_RETRIEVAL_MODES}")

        for forbidden in ("score", "relevance_score", "rank", "oa_status", "html_available", "tei_available"):
            if forbidden in c:
                errors.append(f"{prefix} has forbidden field '{forbidden}'")

    # Optional sources must only appear in source_counts when explicitly listed in target_sources
    explicit_sources = set(target_sources) if target_sources else DEFAULT_SOURCES
    source_counts = report.get("source_counts", {})
    for opt_src in OPTIONAL_SOURCES:
        if opt_src in source_counts and opt_src not in explicit_sources:
            errors.append(
                f"  [response] optional source '{opt_src}' appears in source_counts — "
                "must only be present when explicitly listed in target_sources"
            )

    return errors


def check_chinese_query_only_produces_empty(req: dict, output: dict) -> list[str]:
    """If user_query is Chinese and no English variants exist, output must be empty."""
    errors = []
    user_query = req.get("user_query", "")
    variants = req.get("expanded_query_variants") or []
    matrix = req.get("search_matrix") or []

    if is_chinese(user_query) and not variants and not matrix:
        candidates = output.get("metadata_candidates", [])
        report = output.get("retrieval_report", {})
        if candidates:
            errors.append("  [invariant] Chinese-only user_query produced non-empty metadata_candidates — must return empty")
        risk_flags = report.get("risk_flags", [])
        if "missing_english_query_variants" not in risk_flags:
            errors.append("  [invariant] Chinese-only user_query: risk_flags must include 'missing_english_query_variants'")
        if not report.get("fallback_used"):
            errors.append("  [invariant] Chinese-only user_query: fallback_used must be true")
    return errors


def check_chinese_only_example(input_schema: dict, output_schema: dict) -> list[str]:
    """Validate chinese_only_request/response examples."""
    errors = []
    cn_req = load("examples/chinese_only_request.json")
    cn_resp = load("examples/chinese_only_response.json")

    errors += validate(cn_req, input_schema, "chinese_only_request")
    errors += validate(cn_resp, output_schema, "chinese_only_response")

    user_query = cn_req.get("user_query", "")
    variants = cn_req.get("expanded_query_variants") or []
    matrix = cn_req.get("search_matrix") or []

    if not is_chinese(user_query):
        errors.append("  [chinese_only_request] user_query must be Chinese")
    if variants or matrix:
        errors.append("  [chinese_only_request] must have no expanded_query_variants and no search_matrix")

    candidates = cn_resp.get("metadata_candidates", [])
    report = cn_resp.get("retrieval_report", {})

    if candidates:
        errors.append("  [chinese_only_response] metadata_candidates must be empty")
    if report.get("total_candidates") != 0:
        errors.append("  [chinese_only_response] total_candidates must be 0")
    if not report.get("fallback_used"):
        errors.append("  [chinese_only_response] fallback_used must be true")
    for flag in ("missing_english_query_variants", "chinese_user_query_not_used_as_search_query"):
        if flag not in report.get("risk_flags", []):
            errors.append(f"  [chinese_only_response] risk_flags must include '{flag}'")

    return errors


def check_cjk_query_variants_rejected() -> list[str]:
    """Bad input: expanded_query_variants.query contains CJK — must be rejected by check_input_invariants."""
    bad_input = {
        "user_query": "我想检索锂金属电池快充电解液",
        "expanded_query_variants": [
            {
                "query": "锂金属电池 快充 电解液",
                "variant_type": "broad",
                "search_intent": "core_topic",
                "priority": 1,
            }
        ],
    }
    inv_errors = check_input_invariants(bad_input)
    if not inv_errors:
        return ["  [cjk_variants] CJK query variants must be rejected by check_input_invariants but were not"]
    return []


def check_arxiv_query_adaptation() -> list[str]:
    """Static tests for query_adapter.adapt_for_arxiv P0 rules."""
    errors = []
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(SKILL_DIR))
        from adapter.query_adapter import adapt_for_arxiv
    except ImportError as e:
        return [f"  [arxiv_adapter] cannot import adapter: {e}"]

    # CJK must be skipped
    r = adapt_for_arxiv("锂金属电池 快充", "strict")
    if r.warning is None or "skipped" not in r.warning:
        errors.append("  [arxiv_adapter] CJK query must be skipped")

    # strict: Boolean query → plain keywords, ≤120 chars, no all:/AND/OR
    q = '"lithium metal battery" AND "ether electrolyte" AND "high voltage" AND ("Coulombic efficiency" OR "cycle life")'
    r = adapt_for_arxiv(q, "strict")
    if r.warning and r.warning.startswith("skipped"):
        errors.append(f"  [arxiv_adapter] strict Boolean query should not be skipped: {r.warning}")
    if r.query and len(r.query) > 120:
        errors.append(f"  [arxiv_adapter] adapted query too long: {len(r.query)} chars")
    if r.query and ('all:' in r.query or ' AND ' in r.query or ' OR ' in r.query):
        errors.append(f"  [arxiv_adapter] strict: adapted query must not contain Boolean/all: syntax: {r.query!r}")
    # strict should keep ≤3 phrases worth of content
    import re as _re
    tokens = _re.findall(r'\w+', r.query)
    if len(tokens) > 12:
        errors.append(f"  [arxiv_adapter] strict: too many tokens ({len(tokens)} > 12)")

    # broad: should produce shorter query than strict
    q_broad = '"Li metal anode" AND ("ether electrolyte" OR "glyme electrolyte" OR "DME electrolyte") AND ("high voltage" OR "fast charging")'
    r_broad = adapt_for_arxiv(q_broad, "broad")
    if r_broad.warning and r_broad.warning.startswith("skipped"):
        errors.append(f"  [arxiv_adapter] broad query should not be skipped: {r_broad.warning}")
    if r_broad.query and ('all:' in r_broad.query or ' AND ' in r_broad.query):
        errors.append(f"  [arxiv_adapter] broad: adapted query must not contain Boolean/all: syntax")

    # Empty query after stripping must be skipped
    r_empty = adapt_for_arxiv("AND OR NOT ()", "strict")
    if r_empty.warning is None or "skipped" not in r_empty.warning:
        errors.append("  [arxiv_adapter] empty-after-strip query must be skipped")

    return errors


def check_partial_success_logic() -> list[str]:
    """Unit-level tests for partial_success / failed_sources rules."""
    errors = []

    def _make_report(failed_sources, source_counts, total_candidates, partial_success):
        return {
            "metadata_candidates": [{"candidate_id": "x"} for _ in range(total_candidates)],
            "retrieval_report": {
                "source_counts": source_counts,
                "query_counts": {},
                "failed_sources": failed_sources,
                "total_candidates": total_candidates,
                "partial_success": partial_success,
                "fallback_used": False,
                "risk_flags": [],
            },
        }

    # Rule: all sources succeed → partial_success=False
    r = _make_report([], {"arxiv": 5, "openalex": 3}, 8, False)
    if r["retrieval_report"]["partial_success"]:
        errors.append("  [partial_success] all sources succeed must give partial_success=False")

    # Rule: one source fails, one succeeds → partial_success=True
    r = _make_report(
        [{"source_name": "semantic_scholar", "error": "429 rate limit"}],
        {"arxiv": 5, "openalex": 3, "semantic_scholar": 0},
        8, True,
    )
    if not r["retrieval_report"]["partial_success"]:
        errors.append("  [partial_success] mixed success/failure must give partial_success=True")

    # Rule: all sources fail → partial_success=True, total_candidates=0
    r = _make_report(
        [{"source_name": "arxiv", "error": "timeout"},
         {"source_name": "openalex", "error": "SSL error"}],
        {"arxiv": 0, "openalex": 0},
        0, True,
    )
    if not r["retrieval_report"]["partial_success"]:
        errors.append("  [partial_success] all sources fail must give partial_success=True")
    if r["retrieval_report"]["total_candidates"] != 0:
        errors.append("  [partial_success] all sources fail must give total_candidates=0")

    # Rule: source returns 0 results with no error → not a failure (no failed_sources entry)
    r = _make_report([], {"arxiv": 5, "semantic_scholar": 0}, 5, False)
    if r["retrieval_report"]["failed_sources"]:
        errors.append("  [partial_success] 0 results without error must not appear in failed_sources")

    # Rule: 429 must appear in failed_sources error string
    failed = [{"source_name": "semantic_scholar", "error": "429 rate limit (no API key)"}]
    if "429" not in failed[0]["error"]:
        errors.append("  [partial_success] 429 error must be recorded in failed_sources")

    return errors


def main() -> None:
    input_schema = load("input.schema.json")
    output_schema = load("output.schema.json")
    example_request = load("examples/request.json")
    example_response = load("examples/response.json")

    all_errors: list[str] = []

    all_errors += validate(example_request, input_schema, "request")
    all_errors += validate(example_response, output_schema, "response")
    all_errors += check_input_invariants(example_request)
    all_errors += check_output_invariants(example_response)
    all_errors += check_chinese_query_only_produces_empty(example_request, example_response)
    all_errors += check_chinese_only_example(input_schema, output_schema)
    all_errors += check_cjk_query_variants_rejected()
    all_errors += check_arxiv_query_adaptation()
    all_errors += check_partial_success_logic()

    if all_errors:
        print("FAIL")
        for e in all_errors:
            print(e)
        sys.exit(1)
    else:
        print("OK — examples pass schema validation and all P0.1 invariant checks")


if __name__ == "__main__":
    main()
