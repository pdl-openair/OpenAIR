"""Schema validation for candidate-dedup-and-provenance Skill.

Validates:
1. Input/output schema definitions are valid JSON Schema Draft-07
2. Example request validates against input schema
3. Example response validates against output schema
4. Dedup report statistics are consistent
"""

import json
import pathlib
import sys
from jsonschema import Draft7Validator

# Fix Windows console encoding
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_json(rel: str) -> dict:
    """Load JSON file relative to skill root."""
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def main() -> int:
    """Run validation checks."""
    checks = []
    errors_found = False

    try:
        # Load schemas and examples
        input_schema = load_json("input.schema.json")
        output_schema = load_json("output.schema.json")
        example_request = load_json("examples/request.json")
        example_response = load_json("examples/response.json")

        # 1. Validate schema definitions
        Draft7Validator.check_schema(input_schema)
        Draft7Validator.check_schema(output_schema)
        checks.append("✓ Schema definitions are valid Draft-07 JSON Schema")

        # 2. Validate example request
        errors = list(Draft7Validator(input_schema).iter_errors(example_request))
        if errors:
            print("✗ Example request validation errors:")
            for e in errors:
                print(f"  - {e.message} at {'.'.join(str(p) for p in e.path)}")
            errors_found = True
        else:
            checks.append("✓ Example request validates against input.schema.json")

        # 3. Validate example response
        errors = list(Draft7Validator(output_schema).iter_errors(example_response))
        if errors:
            print("✗ Example response validation errors:")
            for e in errors:
                print(f"  - {e.message} at {'.'.join(str(p) for p in e.path)}")
            errors_found = True
        else:
            checks.append("✓ Example response validates against output.schema.json")

        # 4. Validate dedup report consistency
        report = example_response.get("dedup_report", {})
        input_count = report.get("input_count", 0)
        output_count = report.get("output_count", 0)
        duplicates_removed = report.get("duplicates_removed", 0)

        if input_count != output_count + duplicates_removed:
            print(f"✗ Dedup report inconsistent: input_count ({input_count}) != output_count ({output_count}) + duplicates_removed ({duplicates_removed})")
            errors_found = True
        else:
            checks.append("✓ Dedup report statistics are consistent")

        # 5. Validate matched_source_count
        deduped_candidates = example_response.get("deduped_candidates", [])
        for i, candidate in enumerate(deduped_candidates):
            provenance = candidate.get("provenance", [])
            unique_sources = len(set(p.get("source_name") for p in provenance))
            matched_source_count = candidate.get("matched_source_count", 0)

            if unique_sources != matched_source_count:
                print(f"✗ Candidate {i}: matched_source_count ({matched_source_count}) != unique sources in provenance ({unique_sources})")
                errors_found = True

        if not errors_found:
            checks.append("✓ All candidates have correct matched_source_count")

        # 6. Runtime regression: DOI canonicalization + provenance fallback
        sys.path.insert(0, str(ROOT))
        from adapter.dedup_adapter import deduplicate_candidates

        runtime_resp = deduplicate_candidates({
            "metadata_candidates": [
                {
                    "candidate_id": "doi-prefixed",
                    "dedupe_key": "doi:10.1000/example",
                    "title": "Example A",
                    "normalized_title": "example a",
                    "source_name": "openalex",
                    "matched_query": "example",
                    "provenance": [{"source_name": "openalex", "matched_query": "example"}],
                    "citation_count": 10,
                },
                {
                    "candidate_id": "doi-raw",
                    "dedupe_key": "10.1000/example",
                    "title": "Example A duplicate",
                    "normalized_title": "example a duplicate",
                    "source_name": "semantic_scholar",
                    "matched_query": "example",
                    "provenance": [],
                    "citation_count": 5,
                },
            ]
        })
        runtime_rows = runtime_resp.get("deduped_candidates", [])
        runtime_report = runtime_resp.get("dedup_report", {})
        if len(runtime_rows) != 1 or runtime_rows[0].get("dedupe_key") != "doi:10.1000/example":
            print("✗ DOI prefixed/raw keys did not collapse to one canonical key")
            errors_found = True
        elif runtime_rows[0].get("matched_source_count") != 2:
            print("✗ Canonical DOI merge did not preserve matched_source_count=2")
            errors_found = True
        elif "degraded_provenance_recovered" not in runtime_report.get("risk_flags", []):
            print("✗ Missing provenance fallback did not surface degradation flag")
            errors_found = True
        else:
            checks.append("✓ DOI canonicalization and provenance fallback are deterministic")

        # 7. Runtime regression: stronger DOI metadata overrides a weaker legacy arXiv key
        priority_resp = deduplicate_candidates({
            "metadata_candidates": [
                {
                    "candidate_id": "legacy-arxiv-key",
                    "dedupe_key": "arxiv:2304.98765",
                    "doi": "10.1149/example",
                    "arxiv_id": "2304.98765v2",
                    "external_ids": {"doi": "10.1149/example", "arxiv_id": "2304.98765v2"},
                    "title": "Silicon Example",
                    "normalized_title": "silicon example",
                    "source_name": "arxiv",
                    "matched_query": "silicon example",
                    "provenance": [{"source_name": "arxiv", "matched_query": "silicon example"}],
                    "citation_count": 8,
                    "year": 2024,
                    "source_url": "https://arxiv.org/pdf/2304.98765.pdf",
                },
                {
                    "candidate_id": "doi-key",
                    "dedupe_key": "doi:10.1149/example",
                    "doi": "10.1149/example",
                    "external_ids": {"doi": "10.1149/example"},
                    "title": "Silicon Example",
                    "normalized_title": "silicon example",
                    "source_name": "openalex",
                    "matched_query": "silicon example",
                    "provenance": [{"source_name": "openalex", "matched_query": "silicon example"}],
                    "citation_count": 8,
                    "year": 2024,
                    "source_url": "https://doi.org/10.1149/example",
                },
            ]
        })
        priority_rows = priority_resp.get("deduped_candidates", [])
        if len(priority_rows) != 1 or priority_rows[0].get("dedupe_key") != "doi:10.1149/example":
            print("✗ Strong DOI metadata did not override weaker legacy arXiv key")
            errors_found = True
        elif priority_rows[0].get("matched_source_count") != 2:
            print("✗ DOI-priority merge did not preserve matched_source_count=2")
            errors_found = True
        elif "arxiv.org/pdf" not in str(priority_rows[0].get("source_url") or ""):
            print("✗ DOI-priority merge did not preserve arXiv PDF representative preference")
            errors_found = True
        else:
            checks.append("✓ Strong identifiers override weaker legacy dedupe keys")

        # Print summary
        print("\nValidation Results:")
        for check in checks:
            print(f"  {check}")

        if errors_found:
            print("\n❌ Validation failed")
            return 1
        else:
            print("\n✅ All validations passed")
            return 0

    except Exception as e:
        print(f"✗ Validation error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
