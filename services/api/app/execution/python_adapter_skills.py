"""Shared allowlist for in-process Python Skill adapters.

This table is the single source of truth for Skill Lab, Workbench execute_skill,
and durable runtime support. Keys are accepted public aliases; values resolve to
(canonical_skill_id, repo-relative skill_path).
"""
from __future__ import annotations

PYTHON_ADAPTER_SKILLS: dict[str, tuple[str, str]] = {
    "reproduction-execution-input-validation": (
        "reproduction_execution_input_validation",
        "skills/cs_research/reproduction-execution-input-validation",
    ),
    "reproduction_execution_input_validation": (
        "reproduction_execution_input_validation",
        "skills/cs_research/reproduction-execution-input-validation",
    ),
    "reproduction-record-assembly": (
        "reproduction_record_assembly",
        "skills/cs_research/reproduction-record-assembly",
    ),
    "reproduction_record_assembly": (
        "reproduction_record_assembly",
        "skills/cs_research/reproduction-record-assembly",
    ),
    "actual-metric-extraction": (
        "actual_metric_extraction",
        "skills/cs_research/actual-metric-extraction",
    ),
    "actual_metric_extraction": (
        "actual_metric_extraction",
        "skills/cs_research/actual-metric-extraction",
    ),
    "resource-identity-acquisition": (
        "resource_identity_acquisition",
        "skills/cs_research/resource-identity-acquisition",
    ),
    "resource_identity_acquisition": (
        "resource_identity_acquisition",
        "skills/cs_research/resource-identity-acquisition",
    ),
    "reference-metric-comparison": (
        "reference_metric_comparison",
        "skills/cs_research/reference-metric-comparison",
    ),
    "reference_metric_comparison": (
        "reference_metric_comparison",
        "skills/cs_research/reference-metric-comparison",
    ),
    "repository-revision-acquisition": (
        "repository_revision_acquisition",
        "skills/cs_research/repository-revision-acquisition",
    ),
    "repository_revision_acquisition": (
        "repository_revision_acquisition",
        "skills/cs_research/repository-revision-acquisition",
    ),
    "cs-paper-source-resolution": (
        "cs_paper_source_resolution",
        "skills/cs_research/cs-paper-source-resolution",
    ),
    "cs_paper_source_resolution": (
        "cs_paper_source_resolution",
        "skills/cs_research/cs-paper-source-resolution",
    ),
    "exploratory-run": (
        "exploratory_run",
        "skills/cs_research/exploratory-run",
    ),
    "exploratory_run": (
        "exploratory_run",
        "skills/cs_research/exploratory-run",
    ),
    "environment-setup": (
        "environment_setup",
        "skills/cs_research/environment-setup",
    ),
    "environment_setup": (
        "environment_setup",
        "skills/cs_research/environment-setup",
    ),
    "opentrajectory-source-acquisition": (
        "opentrajectory_source_acquisition",
        "skills/opentrajectory/opentrajectory-source-acquisition",
    ),
    "opentrajectory_source_acquisition": (
        "opentrajectory_source_acquisition",
        "skills/opentrajectory/opentrajectory-source-acquisition",
    ),
    "academic-source-retrieval-skill": (
        "academic_source_retrieval_skill",
        "skills/opentrajectory/academic-source-retrieval-skill",
    ),
    "academic_source_retrieval_skill": (
        "academic_source_retrieval_skill",
        "skills/opentrajectory/academic-source-retrieval-skill",
    ),
    "candidate-dedup-and-provenance": (
        "candidate_dedup_and_provenance",
        "skills/opentrajectory/candidate-dedup-and-provenance",
    ),
    "candidate_dedup_and_provenance": (
        "candidate_dedup_and_provenance",
        "skills/opentrajectory/candidate-dedup-and-provenance",
    ),
    "source-access-readiness-resolver-skill": (
        "source_access_readiness_resolver",
        "skills/opentrajectory/source-access-readiness-resolver-skill",
    ),
    "source_access_readiness_resolver": (
        "source_access_readiness_resolver",
        "skills/opentrajectory/source-access-readiness-resolver-skill",
    ),
    "title-abstract-screening-skill": (
        "title_abstract_screening",
        "skills/opentrajectory/title-abstract-screening-skill",
    ),
    "title_abstract_screening": (
        "title_abstract_screening",
        "skills/opentrajectory/title-abstract-screening-skill",
    ),
    "candidate-source-scoring": (
        "candidate_source_scoring",
        "skills/opentrajectory/candidate-source-scoring",
    ),
    "candidate_source_scoring": (
        "candidate_source_scoring",
        "skills/opentrajectory/candidate-source-scoring",
    ),
    "candidate-tiering": (
        "candidate_tiering",
        "skills/opentrajectory/candidate-tiering",
    ),
    "candidate_tiering": (
        "candidate_tiering",
        "skills/opentrajectory/candidate-tiering",
    ),
    "topk-source-selection": (
        "topk_source_selection",
        "skills/opentrajectory/topk-source-selection",
    ),
    "topk_source_selection": (
        "topk_source_selection",
        "skills/opentrajectory/topk-source-selection",
    ),
    "deepxiv-progressive-reading-skill": (
        "deepxiv_progressive_reading",
        "skills/opentrajectory/deepxiv-progressive-reading-skill",
    ),
    "deepxiv_progressive_reading": (
        "deepxiv_progressive_reading",
        "skills/opentrajectory/deepxiv-progressive-reading-skill",
    ),
    "pvd-candidate-scoring-context-assembly": (
        "pvd_candidate_scoring_context_assembly",
        "skills/pvd/pvd-candidate-scoring-context-assembly",
    ),
    "pvd_candidate_scoring_context_assembly": (
        "pvd_candidate_scoring_context_assembly",
        "skills/pvd/pvd-candidate-scoring-context-assembly",
    ),
    "pvd-literature-evidence-acquisition-closure": (
        "pvd_literature_evidence_acquisition_closure",
        "skills/pvd/pvd-literature-evidence-acquisition-closure",
    ),
    "pvd_literature_evidence_acquisition_closure": (
        "pvd_literature_evidence_acquisition_closure",
        "skills/pvd/pvd-literature-evidence-acquisition-closure",
    ),
    "pvd-evidence-applicability-comparability-analysis": (
        "pvd_evidence_applicability_comparability_analysis",
        "skills/pvd/pvd-evidence-applicability-comparability-analysis",
    ),
    "pvd_evidence_applicability_comparability_analysis": (
        "pvd_evidence_applicability_comparability_analysis",
        "skills/pvd/pvd-evidence-applicability-comparability-analysis",
    ),
    "pvd-recommendation-mode-resolution": (
        "pvd_recommendation_mode_resolution",
        "skills/pvd/pvd-recommendation-mode-resolution",
    ),
    "pvd_recommendation_mode_resolution": (
        "pvd_recommendation_mode_resolution",
        "skills/pvd/pvd-recommendation-mode-resolution",
    ),
    "pvd_process_factor_candidate_direction_analysis": (
        "pvd_process_factor_candidate_direction_analysis",
        "skills/pvd/pvd-process-factor-candidate-direction-analysis",
    ),
    "pvd_bayesian_optimization_candidate_generation": (
        "pvd_bayesian_optimization_candidate_generation",
        "skills/pvd/pvd-bayesian-optimization-candidate-generation",
    ),
    "materials-magnet-design-mvp": (
        "materials_magnet_design_mvp",
        "skills/materials/materials-magnet-design-mvp",
    ),
    "materials_magnet_design_mvp": (
        "materials_magnet_design_mvp",
        "skills/materials/materials-magnet-design-mvp",
    ),
    "materials-mp-constraint-search": (
        "materials_mp_constraint_search",
        "skills/materials/materials-mp-constraint-search",
    ),
    "materials_mp_constraint_search": (
        "materials_mp_constraint_search",
        "skills/materials/materials-mp-constraint-search",
    ),
    "materials-crystal-design-structure-crossover-py": (
        "crystal_design_structure_crossover_py",
        "skills/materials/crystal-design-structure-crossover-py",
    ),
    "crystal_design_structure_crossover_py": (
        "crystal_design_structure_crossover_py",
        "skills/materials/crystal-design-structure-crossover-py",
    ),
    "materials-crystal-design-structure-crossover": (
        "crystal_design_structure_crossover_py",
        "skills/materials/crystal-design-structure-crossover-py",
    ),
    "crystal_design_structure_crossover": (
        "crystal_design_structure_crossover_py",
        "skills/materials/crystal-design-structure-crossover-py",
    ),
    "materials-crystal-design-formula-crossover-py": (
        "crystal_design_formula_crossover_py",
        "skills/materials/crystal-design-formula-crossover-py",
    ),
    "crystal_design_formula_crossover_py": (
        "crystal_design_formula_crossover_py",
        "skills/materials/crystal-design-formula-crossover-py",
    ),
    "materials-crystal-design-formula-crossover": (
        "crystal_design_formula_crossover_py",
        "skills/materials/crystal-design-formula-crossover-py",
    ),
    "crystal_design_formula_crossover": (
        "crystal_design_formula_crossover_py",
        "skills/materials/crystal-design-formula-crossover-py",
    ),
    "official-reference-source-acquisition": (
        "official_reference_source_acquisition",
        "skills/cs_research/official-reference-source-acquisition",
    ),
    "official_reference_source_acquisition": (
        "official_reference_source_acquisition",
        "skills/cs_research/official-reference-source-acquisition",
    ),
    "reference-metric-fact-assembly": (
        "reference_metric_fact_assembly",
        "skills/cs_research/reference-metric-fact-assembly",
    ),
    "reference_metric_fact_assembly": (
        "reference_metric_fact_assembly",
        "skills/cs_research/reference-metric-fact-assembly",
    ),
    "execution-plan-fact-assembly": (
        "execution_plan_fact_assembly",
        "skills/cs_research/execution-plan-fact-assembly",
    ),
    "execution_plan_fact_assembly": (
        "execution_plan_fact_assembly",
        "skills/cs_research/execution-plan-fact-assembly",
    ),
    "reproduction-execution-input-closure": (
        "reproduction_execution_input_closure",
        "skills/cs_research/reproduction-execution-input-closure",
    ),
    "reproduction_execution_input_closure": (
        "reproduction_execution_input_closure",
        "skills/cs_research/reproduction-execution-input-closure",
    ),
    "reproduction-comparison-closure": (
        "reproduction_comparison_closure",
        "skills/cs_research/reproduction-comparison-closure",
    ),
    "reproduction_comparison_closure": (
        "reproduction_comparison_closure",
        "skills/cs_research/reproduction-comparison-closure",
    ),
    "reproduction-conclusion-closure": (
        "reproduction_conclusion_closure",
        "skills/cs_research/reproduction-conclusion-closure",
    ),
    "reproduction_conclusion_closure": (
        "reproduction_conclusion_closure",
        "skills/cs_research/reproduction-conclusion-closure",
    ),
}


def _normalized_skill_id(skill_id: str) -> str:
    return (skill_id or "").strip().lower().replace("-", "_")


def resolve_python_adapter_skill(skill_id: str) -> tuple[str, str] | None:
    """Resolve an accepted Skill id/alias to its canonical adapter target."""
    direct = PYTHON_ADAPTER_SKILLS.get((skill_id or "").strip())
    if direct is not None:
        return direct
    normalized = _normalized_skill_id(skill_id)
    for alias, target in PYTHON_ADAPTER_SKILLS.items():
        if _normalized_skill_id(alias) == normalized:
            return target
    return None


# Formal callable registrations for accepted Python Adapter Skills.
PYTHON_ADAPTER_ENTRYPOINTS: dict[str, tuple[str, str, str]] = {
    "python_adapter:reproduction_record_assembly": (
        "adapter.reproduction_record_assembly_adapter", "run_reproduction_record_assembly", "payload_v1"
    ),
    "python_adapter:actual_metric_extraction": (
        "adapter.actual_metric_extraction_adapter", "run_actual_metric_extraction", "payload_v1"
    ),
    "python_adapter:resource_identity_acquisition": (
        "adapter.resource_identity_adapter", "run_resource_identity_acquisition", "payload_v1"
    ),
    "python_adapter:reference_metric_comparison": (
        "adapter.reference_metric_comparison_adapter", "run_reference_metric_comparison", "payload_v1"
    ),
    "python_adapter:repository_revision_acquisition": (
        "adapter.repository_revision_adapter", "run_repository_revision_acquisition", "payload_v1"
    ),
    "python_adapter:cs_paper_source_resolution": (
        "adapter.paper_source_resolution_adapter", "run_cs_paper_source_resolution", "payload_v1"
    ),
    "python_adapter:exploratory_run": (
        "adapter.exploratory_run_adapter", "run_exploratory_run", "payload_v1"
    ),
    "python_adapter:environment_setup": (
        "adapter.environment_setup_adapter", "run_environment_setup", "payload_v1"
    ),
    "python_adapter:opentrajectory_source_acquisition": (
        "adapter.source_acquisition_adapter", "run_source_acquisition", "payload_v1"
    ),
    "python_adapter:academic_source_retrieval_skill": (
        "adapter.retrieval_adapter", "run_retrieval", "academic_retrieval_v1"
    ),
    "python_adapter:candidate_dedup_and_provenance": (
        "adapter.dedup_adapter", "deduplicate_candidates", "payload_v1"
    ),
    "python_adapter:source_access_readiness_resolver": (
        "adapter.access_readiness_adapter", "resolve_access_readiness", "payload_v1"
    ),
    "python_adapter:deepxiv_progressive_reading": (
        "adapter.progressive_reading_adapter", "progressive_read_candidates", "payload_v1"
    ),
    "python_adapter:pvd_candidate_scoring_context_assembly": (
        "adapter.context_assembly",
        "run_pvd_candidate_scoring_context_assembly",
        "payload_v1",
    ),
    "python_adapter:pvd_literature_evidence_acquisition_closure": (
        "adapter.literature_evidence_closure",
        "run_literature_evidence_closure",
        "payload_v1",
    ),
    "python_adapter:pvd_evidence_applicability_comparability_analysis": (
        "adapter.evidence_applicability_comparability",
        "run_evidence_applicability_comparability",
        "payload_v1",
    ),
    "python_adapter:pvd_recommendation_mode_resolution": (
        "adapter.mode_resolution",
        "run_pvd_recommendation_mode_resolution",
        "payload_v1",
    ),
    "python_adapter:pvd_process_factor_candidate_direction_analysis": (
        "adapter.pvd_factor_analysis_adapter",
        "run_pvd_process_factor_candidate_direction_analysis",
        "payload_v1",
    ),
    "python_adapter:pvd_bayesian_optimization_candidate_generation": (
        "adapter.pvd_bo_candidate_adapter",
        "run_pvd_bayesian_candidate_generation",
        "payload_v1",
    ),
    "python_adapter:official_reference_source_acquisition": (
        "adapter.official_reference_source_acquisition_adapter",
        "run_official_reference_source_acquisition",
        "payload_v1",
    ),
    "python_adapter:reference_metric_fact_assembly": (
        "adapter.reference_metric_fact_assembly_adapter",
        "run_reference_metric_fact_assembly",
        "payload_v1",
    ),
    "python_adapter:execution_plan_fact_assembly": (
        "adapter.execution_plan_fact_assembly_adapter",
        "run_execution_plan_fact_assembly",
        "payload_v1",
    ),
    "python_adapter:reproduction_execution_input_validation": (
        "adapter.reproduction_execution_input_closure_adapter",
        "run_reproduction_execution_input_closure",
        "payload_v1",
    ),
    "python_adapter:reproduction_execution_input_closure": (
        "adapter.reproduction_execution_input_closure_adapter",
        "run_reproduction_execution_input_closure",
        "payload_v1",
    ),
    "python_adapter:reproduction_comparison_closure": (
        "adapter.reproduction_comparison_closure_adapter",
        "run_reproduction_comparison_closure",
        "payload_v1",
    ),
    "python_adapter:reproduction_conclusion_closure": (
        "adapter.reproduction_conclusion_closure_adapter",
        "run_reproduction_conclusion_closure",
        "payload_v1",
    ),
}


def resolve_python_adapter_entrypoint(adapter_id: str) -> tuple[str, str, str] | None:
    """Resolve a callable from the formal Registry/Skill runtime adapter identity."""
    return PYTHON_ADAPTER_ENTRYPOINTS.get((adapter_id or "").strip())
