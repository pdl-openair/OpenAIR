from __future__ import annotations

from types import SimpleNamespace

from app.models.research_orchestration import AgentRef, SkillRef
from app.modules.research_execution_decision_service import _artifact_has_completion_authority
from app.modules.research_orchestration_resolver import (
    ResolutionFacts,
    ResearchOrchestrationResolver,
    parse_definition_spec,
)


def _criterion(artifact_type: str):
    return SimpleNamespace(
        required_artifact_type=artifact_type,
        judgment_requirement=SimpleNamespace(value="deterministic"),
        description=f"Produce {artifact_type}",
        evaluator_ref=None,
    )


def _instance(artifact_type: str):
    return SimpleNamespace(
        expected_artifact_types=[artifact_type],
        acceptance_criteria=[_criterion(artifact_type)],
        input_refs=[],
        objective="Complete the selected bounded research responsibility.",
    )


def _semantic_definition() -> dict:
    definition = {
        "definitionId": "ope:gap_analysis",
        "definitionVersion": 1,
        "stage": "problem_modeling",
        "riskPolicy": {"riskClass": "low"},
    }
    definition.update(
        parse_definition_spec(
            "ope",
            "specs/gap-analysis.md",
            "# Gap analysis\n\nNo structured contracts.\n",
        )
    )
    return definition


def _strict_definition(artifact_type: str) -> dict:
    return {
        "definitionId": "sdr:variable_definition",
        "definitionVersion": 1,
        "stage": "solution_design",
        "riskPolicy": {"riskClass": "low"},
        "inputContract": {
            "status": "known",
            "requiredInputRefs": {"status": "known", "values": []},
        },
        "outputContract": {
            "status": "known",
            "artifactTypes": {"status": "known", "values": [artifact_type]},
        },
        "acceptanceContract": {
            "status": "known",
            "criterionRefs": {"status": "known", "values": ["criterion:strict"]},
        },
    }


def _general_agent() -> dict:
    return {
        "agentKind": "general_research",
        "candidateScope": "all_enabled_substages",
        "inputContract": {"required": ["user_goal"]},
        "outputContract": {
            "artifactKind": "general_research_reasoning",
            "artifactContract": "general_research_reasoning_artifact_v1",
        },
        "budgetPolicy": {
            "defaultMode": "draft_only",
            "allowedModes": ["draft_only", "critique_only"],
            "maxToolCalls": 0,
        },
        "notFor": [
            "External retrieval, code execution, filesystem access, or evidence collection without an authorized Skill."
        ],
        "judgmentSupport": {"domainProfessional": False, "humanAuthority": False},
    }


def _facts(candidate_id: str, *, provider: bool) -> ResolutionFacts:
    return ResolutionFacts(
        runtimeReadiness={candidate_id: True},
        providerReadiness={"provider-a": True} if provider else {},
        authorizedCandidateRefs={candidate_id},
        availableInputRefs=set(),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )


def _runtime(kind: str, *, external: bool, adapter: str) -> dict:
    return {
        "kind": kind,
        "requires_external_action": external,
        "adapter_id": adapter,
        "evidence_contract_version": (
            "external-action-evidence/v1" if external else "none"
        ),
    }


def test_reasoning_runtime_contract_allows_general_fallback_completion() -> None:
    resolver = ResearchOrchestrationResolver(None)  # private direct evaluator does not access archive
    candidate_id = "agent:general_research_agent:1"
    analysis = resolver._evaluate_direct(
        ref=AgentRef(agentId="general_research_agent", definitionVersion=1),
        bindings=[{"bindingId": "bind:general", "bindingVersion": 1}],
        supported_role="primary",
        is_fallback=True,
        definition=_semantic_definition(),
        candidate=_general_agent(),
        instance=_instance("ope.gap_analysis.planned_output"),
        facts=_facts(candidate_id, provider=True),
        provider_id="provider-a",
        responsibility_runtime_contracts=(
            _runtime("llm_reasoning", external=False, adapter="huiyan_chat_completions"),
        ),
    )

    evaluation = analysis["evaluation"]
    assert evaluation.eligible
    assert evaluation.preference.completion_coverage == "completion_capable"
    assert "registry-policy.v1:executor-capability" in (
        evaluation.preference.semantic_fit_evidence.positive_rule_refs
    )


def test_external_runtime_contract_keeps_general_fallback_progress_only() -> None:
    resolver = ResearchOrchestrationResolver(None)
    candidate_id = "agent:general_research_agent:1"
    analysis = resolver._evaluate_direct(
        ref=AgentRef(agentId="general_research_agent", definitionVersion=1),
        bindings=[{"bindingId": "bind:general", "bindingVersion": 1}],
        supported_role="primary",
        is_fallback=True,
        definition=_semantic_definition(),
        candidate=_general_agent(),
        instance=_instance("opentrajectory.source_acquisition.planned_output"),
        facts=_facts(candidate_id, provider=True),
        provider_id="provider-a",
        responsibility_runtime_contracts=(
            _runtime(
                "external_adapter",
                external=True,
                adapter="python_adapter:academic_source_retrieval_skill",
            ),
        ),
    )

    evaluation = analysis["evaluation"]
    assert evaluation.eligible
    assert evaluation.preference.completion_coverage == "progress_capable"
    assert "registry-policy.v1:executor-capability" not in (
        evaluation.preference.semantic_fit_evidence.positive_rule_refs
    )


def test_unknown_or_unavailable_responsibility_runtime_keeps_general_fallback_progress_only() -> None:
    resolver = ResearchOrchestrationResolver(None)
    candidate_id = "agent:general_research_agent:1"
    analysis = resolver._evaluate_direct(
        ref=AgentRef(agentId="general_research_agent", definitionVersion=1),
        bindings=[{"bindingId": "bind:general", "bindingVersion": 1}],
        supported_role="primary",
        is_fallback=True,
        definition=_semantic_definition(),
        candidate=_general_agent(),
        instance=_instance("ope.code_implementation.planned_output"),
        facts=_facts(candidate_id, provider=True),
        provider_id="provider-a",
        responsibility_runtime_contracts=None,
    )

    evaluation = analysis["evaluation"]
    assert evaluation.eligible
    assert evaluation.preference.completion_coverage == "progress_capable"
    assert "registry-policy.v1:executor-capability" not in (
        evaluation.preference.semantic_fit_evidence.positive_rule_refs
    )


def test_external_skill_runtime_can_complete_same_semantic_instance() -> None:
    resolver = ResearchOrchestrationResolver(None)
    candidate_id = "skill:academic_source_retrieval_skill:1"
    runtime_contract = _runtime(
        "external_adapter",
        external=True,
        adapter="python_adapter:academic_source_retrieval_skill",
    )
    skill = {
        "inputContract": {"status": "unknown", "acceptedInputRefs": []},
        "outputContract": {"status": "unknown", "artifactTypes": []},
        "judgmentSupport": {"deterministic": False},
        "sourceContract": {"runtime_contract": runtime_contract},
    }
    analysis = resolver._evaluate_direct(
        ref=SkillRef(skillId="academic_source_retrieval_skill", definitionVersion=1),
        bindings=[{"bindingId": "bind:source-acquisition", "bindingVersion": 1}],
        supported_role="tool",
        is_fallback=False,
        definition=_semantic_definition(),
        candidate=skill,
        instance=_instance("opentrajectory.source_acquisition.planned_output"),
        facts=_facts(candidate_id, provider=False),
        provider_id=None,
        responsibility_runtime_contracts=(runtime_contract,),
    )

    evaluation = analysis["evaluation"]
    assert evaluation.eligible
    assert evaluation.preference.completion_coverage == "completion_capable"
    assert "registry-policy.v1:executor-capability" in (
        evaluation.preference.semantic_fit_evidence.positive_rule_refs
    )


def test_strict_definition_does_not_let_fallback_bypass_canonical_artifact_type() -> None:
    resolver = ResearchOrchestrationResolver(None)
    candidate_id = "agent:general_research_agent:1"
    analysis = resolver._evaluate_direct(
        ref=AgentRef(agentId="general_research_agent", definitionVersion=1),
        bindings=[{"bindingId": "bind:strict-general", "bindingVersion": 1}],
        supported_role="primary",
        is_fallback=True,
        definition=_strict_definition("variable_table"),
        candidate=_general_agent(),
        instance=_instance("variable_table"),
        facts=_facts(candidate_id, provider=True),
        provider_id="provider-a",
        responsibility_runtime_contracts=(
            _runtime("llm_reasoning", external=False, adapter="huiyan_chat_completions"),
        ),
    )

    evaluation = analysis["evaluation"]
    assert evaluation.eligible
    assert evaluation.preference.semantic_fit_evidence.output_contract_match == "none"
    assert evaluation.preference.completion_coverage == "progress_capable"


def test_planned_output_relabel_requires_resolver_backed_completion_provenance() -> None:
    artifact = {
        "artifactType": "ope.gap_analysis.planned_output",
        "validatorResult": {"artifactContract": "general_research_reasoning_artifact_v1"},
    }
    work = {"assignmentId": "assignment-1"}
    executor = {
        "executorType": "agent",
        "executorId": "general_research_agent",
        "definitionVersion": 1,
    }
    binding_refs = [{"bindingId": "bind:general", "bindingVersion": 1}]
    assignment = {
        "assignmentId": "assignment-1",
        "completionCoverage": "completion_capable",
        "executorRef": executor,
        "sourceBindingRefs": binding_refs,
    }
    decision = {
        "registrySnapshotRef": {"contentHash": "snapshot-hash"},
        "candidateEvaluations": [
            {
                "candidateEvaluationId": "eval:general",
                "candidateRef": {
                    "agentId": "general_research_agent",
                    "definitionVersion": 1,
                },
                "sourceBindingRefs": binding_refs,
                "preference": {
                    "completionCoverage": "completion_capable",
                    "semanticFitEvidence": {
                        "positiveRuleRefs": [
                            "registry-policy.v1:definition-binding",
                            "registry-policy.v1:executor-capability",
                        ]
                    },
                },
            }
        ],
    }

    assert not _artifact_has_completion_authority(artifact, work, assignment, decision)
    artifact["validatorResult"]["producerCompletionAuthority"] = {
        "candidateEvaluationId": "eval:general",
        "registrySnapshotContentHash": "snapshot-hash",
        "executorRef": executor,
        "sourceBindingRefs": binding_refs,
    }
    assert _artifact_has_completion_authority(artifact, work, assignment, decision)


def test_planned_output_authority_accepts_capable_skill_provenance() -> None:
    artifact = {
        "artifactType": "opentrajectory.source_acquisition.planned_output",
        "validatorResult": {
            "artifactContract": "source_acquisition_result_v1",
            "producerCompletionAuthority": {
                "candidateEvaluationId": "eval:source-skill",
                "registrySnapshotContentHash": "snapshot-hash",
                "executorRef": {
                    "executorType": "skill",
                    "executorId": "academic_source_retrieval_skill",
                    "definitionVersion": 1,
                },
                "sourceBindingRefs": [
                    {"bindingId": "bind:source-skill", "bindingVersion": 1}
                ],
            },
        },
    }
    work = {"assignmentId": "assignment-source"}
    assignment = {
        "assignmentId": "assignment-source",
        "completionCoverage": "completion_capable",
        "executorRef": {
            "executorType": "skill",
            "executorId": "academic_source_retrieval_skill",
            "definitionVersion": 1,
        },
        "sourceBindingRefs": [{"bindingId": "bind:source-skill", "bindingVersion": 1}],
    }
    decision = {
        "registrySnapshotRef": {"contentHash": "snapshot-hash"},
        "candidateEvaluations": [
            {
                "candidateEvaluationId": "eval:source-skill",
                "candidateRef": {
                    "skillId": "academic_source_retrieval_skill",
                    "definitionVersion": 1,
                },
                "sourceBindingRefs": [
                    {"bindingId": "bind:source-skill", "bindingVersion": 1}
                ],
                "preference": {
                    "completionCoverage": "completion_capable",
                    "semanticFitEvidence": {
                        "positiveRuleRefs": ["registry-policy.v1:executor-capability"]
                    },
                },
            }
        ],
    }

    assert _artifact_has_completion_authority(artifact, work, assignment, decision)
