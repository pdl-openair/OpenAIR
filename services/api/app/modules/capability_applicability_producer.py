"""Capability-owned applicability evaluation before capability selection.

The frozen SkillBinding chooses an evaluator.  This module only resolves and
invokes that callable, admits its typed result, and fails closed.  It does not
interpret capability-specific scientific facts.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

from app.modules.binding_input_runtime_projection import (
    project_binding_input_runtime_facts,
)
from app.modules.conditional_capability_binding import (
    CapabilityApplicabilityResult,
    CapabilityApplicabilityStatus,
    validate_conditional_policy,
)
from app.modules.repo_paths import repo_root


ApplicabilityEvaluator = Callable[[Mapping[str, Any]], Any]
ApplicabilityEvaluatorResolver = Callable[[str], ApplicabilityEvaluator | None]

RESEARCH_TASK_FACT_REF = "runtime:research-task:v1"
EXECUTION_INSTANCE_FACT_REF = "runtime:execution-instance:v1"
SCIENTIFIC_STATE_FACT_REF = "runtime:scientific-state:v1"


def resolve_capability_applicability_evaluator(
    evaluator_ref: str,
    *,
    skill_path: str | None = None,
) -> ApplicabilityEvaluator | None:
    """Resolve one frozen Python entrypoint without capability identity rules.

    Production Skill packages are not installed into the API interpreter.  A
    frozen Registry ``skillPath`` therefore supplies the only additional import
    root.  The loaded module must originate inside that exact package.
    """

    parts = evaluator_ref.split(":", 2)
    if len(parts) != 3 or parts[0] != "python" or not parts[1] or not parts[2]:
        return None
    import_roots: list[Path] = []
    skill_root: Path | None = None
    if skill_path is not None:
        skill_root = (repo_root() / skill_path).resolve()
        repository_root = repo_root().resolve()
        if repository_root not in skill_root.parents or not skill_root.is_dir():
            return None
        import_roots = [root for root in (skill_root / "src", skill_root) if root.is_dir()]
    inserted: list[str] = []
    try:
        for root in reversed(import_roots):
            value = str(root)
            if value not in sys.path:
                sys.path.insert(0, value)
                inserted.append(value)
        evaluator = getattr(import_module(parts[1]), parts[2])
    except (AttributeError, ImportError, ValueError):
        return None
    finally:
        for value in inserted:
            if value in sys.path:
                sys.path.remove(value)
    if skill_root is not None:
        module = sys.modules.get(parts[1])
        module_file = None if module is None else getattr(module, "__file__", None)
        if module_file is None:
            return None
        resolved_module = Path(module_file).resolve()
        if resolved_module != skill_root and skill_root not in resolved_module.parents:
            return None
    return evaluator if callable(evaluator) else None


def _failed_result(
    *, capability_ref: str, evaluator_ref: str, evaluated_fact_refs: set[str]
) -> CapabilityApplicabilityResult:
    return CapabilityApplicabilityResult(
        capabilityRef=capability_ref,
        evaluatorRef=evaluator_ref,
        status=CapabilityApplicabilityStatus.EVALUATION_FAILED,
        evaluatedRuntimeFactRefs=sorted(evaluated_fact_refs),
    )


def produce_capability_applicability_results(
    *,
    snapshot: Mapping[str, Any],
    definition_identity: tuple[str, int],
    authoritative_runtime_facts: Mapping[str, Any],
    evaluator_resolver: ApplicabilityEvaluatorResolver | None = None,
) -> dict[str, CapabilityApplicabilityResult]:
    """Evaluate conditional bindings once, before the existing Resolver runs."""

    results: dict[str, CapabilityApplicabilityResult] = {}
    skill_definitions = {
        (row.get("skillId"), row.get("definitionVersion")): row
        for row in snapshot.get("definitions", {}).get("skills", [])
        if isinstance(row, Mapping)
    }
    for binding in snapshot.get("bindings", {}).get("skills", []):
        if (
            (binding.get("definitionId"), binding.get("definitionVersion"))
            != definition_identity
            or binding.get("enabled") is not True
            or binding.get("conditionalPolicy") is None
        ):
            continue
        capability_ref = (
            f"skill:{binding.get('skillId')}:{binding.get('skillDefinitionVersion')}"
        )
        raw_policy = binding.get("conditionalPolicy")
        try:
            policy = validate_conditional_policy(raw_policy)
        except ValueError:
            # Invalid policy is already rejected at snapshot admission.  Keep
            # producer behavior fail-closed for defensive direct callers.
            continue
        evaluator_ref = policy["evaluatorRef"]
        required_refs = set(policy["requiredRuntimeFactRefs"])
        research_task = authoritative_runtime_facts.get(RESEARCH_TASK_FACT_REF)
        projected_binding_inputs = project_binding_input_runtime_facts(
            binding=binding,
            snapshot=snapshot,
            research_task=(
                research_task if isinstance(research_task, Mapping) else None
            ),
            required_refs=required_refs,
        )
        effective_runtime_facts = {
            **authoritative_runtime_facts,
            **projected_binding_inputs,
        }
        available_required_refs = required_refs & set(effective_runtime_facts)
        if available_required_refs != required_refs:
            results[capability_ref] = _failed_result(
                capability_ref=capability_ref,
                evaluator_ref=evaluator_ref,
                evaluated_fact_refs=available_required_refs,
            )
            continue
        try:
            if evaluator_resolver is None:
                definition = skill_definitions.get(
                    (binding.get("skillId"), binding.get("skillDefinitionVersion"))
                )
                skill_path = (
                    definition.get("skillPath")
                    if isinstance(definition, Mapping)
                    else None
                )
                evaluator = resolve_capability_applicability_evaluator(
                    evaluator_ref,
                    skill_path=(str(skill_path) if skill_path else None),
                )
            else:
                evaluator = evaluator_resolver(evaluator_ref)
        except Exception:
            evaluator = None
        if evaluator is None:
            results[capability_ref] = _failed_result(
                capability_ref=capability_ref,
                evaluator_ref=evaluator_ref,
                evaluated_fact_refs=set(),
            )
            continue
        request = {
            "capabilityRef": capability_ref,
            "evaluatorRef": evaluator_ref,
            "runtimeFacts": {
                ref: effective_runtime_facts[ref] for ref in sorted(required_refs)
            },
        }
        try:
            raw_result = evaluator(request)
            result = (
                raw_result
                if isinstance(raw_result, CapabilityApplicabilityResult)
                else CapabilityApplicabilityResult.model_validate(raw_result)
            )
            if (
                result.capability_ref != capability_ref
                or result.evaluator_ref != evaluator_ref
                or set(result.evaluated_runtime_fact_refs) != required_refs
            ):
                raise ValueError("capability applicability provenance mismatch")
        except Exception:
            result = _failed_result(
                capability_ref=capability_ref,
                evaluator_ref=evaluator_ref,
                evaluated_fact_refs=set(),
            )
        results[capability_ref] = result
    return results


__all__ = [
    "ApplicabilityEvaluator",
    "ApplicabilityEvaluatorResolver",
    "EXECUTION_INSTANCE_FACT_REF",
    "RESEARCH_TASK_FACT_REF",
    "SCIENTIFIC_STATE_FACT_REF",
    "produce_capability_applicability_results",
    "resolve_capability_applicability_evaluator",
]
