"""Computer-science domain profile for generic research-lead packages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import register_domain_profile



@dataclass(frozen=True)
class CSDomainProfile:
    profile_id: str = "cs_v0.3"
    task_domains: tuple[str, ...] = ("computer_science", "cs_research")

    def normalize(self, source: dict[str, Any], generic_package: dict[str, Any]) -> dict[str, Any]:
        """Return only domain_normalized.fields."""

        proposal = generic_package.get("proposal") or {}
        return {
            "research_area": "offline_policy_evaluation",
            "method_family": "reinforcement_learning_evaluation",
            "evaluation_objects": [
                "policy evaluation methods",
                "training-stage policy snapshots",
                "behavior policy data windows",
            ],
            "domain_validation_targets": proposal.get("validation_target", []),
            "domain_metrics": proposal.get("metrics", []),
            "domain_controls": proposal.get("control_or_ablation_plan", []),
            "domain_failure_taxonomy": proposal.get("failure_or_error_taxonomy", []),
        }

    def proposal_from_recorded_run(self, source: dict[str, Any], artifact_refs: list[dict[str, str]]) -> dict[str, Any]:
        """Map a CS recorded run into generic proposal fields without claiming evidence."""

        task = source.get("task") or {}
        return {
            "problem_formulation": (
                task.get("user_goal")
                or "Study reliability changes in policy evaluation methods during training."
            ),
            "input_output_setting": (
                "Input is a set of trained or partially trained policies, behavior data windows, "
                "environment rollouts, and method configurations; output is a method-selection "
                "decision rule with estimated policy values and uncertainty diagnostics."
            ),
            "failure_mode": (
                "Evaluation methods can become unreliable when target and behavior policies are far apart, "
                "training stage changes distribution coverage, or finite samples amplify estimator variance."
            ),
            "technical_intervention": (
                "Construct a training-dynamics evaluation grid that varies policy distance, data window, "
                "sample size, and method family, then learn observable switching rules for selecting an "
                "evaluation method under each regime."
            ),
            "expected_effect": (
                "Reduce method-selection error and identify when a lower-variance or lower-bias estimator "
                "should be trusted during training."
            ),
            "mechanism_hypothesis": (
                "Observable signals from policy distance, data coverage, and sample size mediate estimator "
                "bias-variance behavior, so method ranking should change predictably across training regimes."
            ),
            "baseline": [
                "Direct method selection with one fixed estimator across all training stages",
                "Importance sampling family",
                "Weighted importance sampling family",
                "Model-based direct method family",
                "Doubly robust family",
                "Fitted evaluation family",
            ],
            "validation_target": [
                "Four-environment training grid with discrete and continuous action settings",
                "Target policy training progress crossed with evaluation method family",
                "Behavior-data time window crossed with evaluation method family",
                "Data-volume sensitivity grid crossed with evaluation method family",
            ],
            "metrics": [
                "absolute value-estimation error against rollout reference",
                "relative ranking error between evaluation methods",
                "estimator variance across random seeds",
                "confidence interval coverage",
                "method-selection accuracy under predefined regimes",
                "compute cost and wall time",
            ],
            "control_or_ablation_plan": [
                "Compare fixed-method selection against regime-conditioned selection using the same data budget",
                "Remove policy-distance features from the switching rule and measure method-selection accuracy",
                "Remove data-coverage features from the switching rule and measure ranking error",
                "Compare each estimator family under identical policy snapshots, behavior windows, and seeds",
            ],
            "reproducibility_protocol": {
                "repo": "",
                "commit": "",
                "config": "planned estimator, environment, and grid configuration described in recorded artifacts",
                "command": "",
                "seed": "planned multi-seed design",
                "model_version": "",
                "data_split": "planned policy snapshots and behavior-data windows",
                "logs": "",
            },
            "failure_or_error_taxonomy": [
                "policy support mismatch",
                "high variance from sparse importance weights",
                "model approximation error",
                "low data coverage for target behavior",
                "method ranking instability",
                "underpowered regime comparison",
            ],
            "compute_or_cost_boundary": (
                "Planned run grid records environment count, estimator families, seeds, wall time, "
                "and compute budget from the task plan."
            ),
            "statistical_robustness_plan": (
                "Use repeated seeds, confidence intervals, estimator variance reporting, and predefined "
                "decision thresholds for method-switching claims."
            ),
            "falsification_criteria": (
                "Reject or revise the switching-rule hypothesis if observable regime features do not predict "
                "method ranking better than fixed selection or if improvements vanish under seed variation."
            ),
            "next_experiment_matrix": [
                "environment x policy snapshot x behavior window x data volume x method family x metric x decision rule",
            ],
            "evidence_refs": [],
            "artifact_refs": artifact_refs,
            "gap_records": [],
            "claimed_result_status": "planned",
        }

    def required_generic_fields(self) -> tuple[str, ...]:
        """Return generic required fields only."""

        return (
            "problem_formulation",
            "input_output_setting",
            "failure_mode",
            "technical_intervention",
            "baseline",
            "validation_target",
            "metrics",
            "control_or_ablation_plan",
            "reproducibility_protocol",
            "failure_or_error_taxonomy",
            "compute_or_cost_boundary",
            "statistical_robustness_plan",
            "falsification_criteria",
            "next_experiment_matrix",
        )

    def task_domain_for_recorded_run(self, source: dict[str, Any]) -> str:
        return "computer_science"

    def task_subdomain_for_recorded_run(self, source: dict[str, Any]) -> str:
        return "offline_policy_evaluation"


def build_cs_research_lead_package_from_recorded_run(recorded_run_path: str | Path) -> dict[str, Any]:
    """Build a CS research-lead package with an explicit CS profile."""

    from ..builder import build_research_lead_package_from_recorded_run

    return build_research_lead_package_from_recorded_run(recorded_run_path, domain_profile=CSDomainProfile())


register_domain_profile(CSDomainProfile())
