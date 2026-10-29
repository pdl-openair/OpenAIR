from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('pvd_record_finalize_test', ROOT/'scripts/finalize.py')
assert spec and spec.loader
mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)


def _load(name):
    return json.loads((ROOT/'examples'/name).read_text(encoding='utf-8'))


def test_model_grouping_finalizes_to_exact_research_record_contract():
    request=_load('pvd_two_sample.request.json')
    model=_load('pvd_two_sample.model.json')
    result=mod.finalize(request, model)
    assert result.status == 'succeeded'
    assert result.output is not None, result.errors
    records=result.output['evidence_records']
    assert {row.get('sample_id') for row in records} == {'Sample A', 'Sample B'}
    evidence_ids={row['evidence_id'] for row in request['evidence_packet']['evidence_items']}
    emitted_refs={ref for record in records for ref in record['provenance_refs']}
    assert emitted_refs == evidence_ids
    assert result.output['source_provenance'][0]['source_id'] == 'arxiv:2608.54321'


def test_singleton_degradation_preserves_every_evidence_node():
    request=_load('pvd_two_sample.request.json')
    result=mod.materialize_evidence_singletons(request)
    assert result.output is not None, result.errors
    evidence_ids={row['evidence_id'] for row in request['evidence_packet']['evidence_items']}
    emitted_refs={ref for record in result.output['evidence_records'] for ref in record['provenance_refs']}
    assert evidence_ids == emitted_refs
    assert len(result.output['evidence_records']) == len(evidence_ids)


def test_provider_projection_hides_full_canonical_payload_and_keeps_compact_407b_views():
    import yaml
    provider_module_path = ROOT.parents[2] / "services/api/app/execution/provider_visible_skill_input.py"
    spec = importlib.util.spec_from_file_location("pvd_record_provider_projection", provider_module_path)
    assert spec and spec.loader
    provider_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider_mod)

    metadata = yaml.safe_load((ROOT / "skill.yaml").read_text(encoding="utf-8"))
    parameters = {
        "evidence_packet": {"evidence_items": [{"evidence_id": "ev:1", "claim": "full claim"}]},
        "source_material": {"paper_packets": [{"candidate_id": "paper-1", "sections": [{"content": "full text"}]}]},
        "evidence_index": {"evidence_items": [{"evidence_id": "ev:1", "candidate_id": "paper-1", "evidence_type": "experimental_condition", "section_name": "Methods", "matched_target_ids": ["target_001"]}]},
        "atomic_grounding_catalog": [{"grounded_ref": "ground_0001", "text": "full claim", "evidence_id": "ev:1"}],
        "source_index": {"paper_packets": [{"candidate_id": "paper-1", "title": "P", "doi": None, "arxiv_id": None}]},
    }
    projected = provider_mod.project_provider_parameters(metadata, parameters)
    assert "evidence_packet" not in projected
    assert "source_material" not in projected
    assert projected["evidence_index"] == parameters["evidence_index"]
    assert projected["atomic_grounding_catalog"] == parameters["atomic_grounding_catalog"]
    assert projected["source_index"] == parameters["source_index"]
