"""Project closed, current responsibilities without judging scientific success."""

from collections.abc import Mapping
from typing import Any

from app.modules.research_blocker_semantics import research_gap_continues


def _wire(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return value.model_dump(mode="json", by_alias=True)


def _current_state_matches(task, design, state) -> bool:
    expected_ref = {"researchDesignId": design["researchDesignId"], "version": design["version"]}
    if task.get("activeResearchDesignRef") != expected_ref or design.get("status") != "confirmed":
        return False
    states = [
        row
        for row in task.get("scientificStates", [])
        if row.get("researchDesignVersion") == design["version"]
    ]
    latest = max(states, key=lambda row: row["scientificStateVersion"], default=None)
    fields = (
        "scientificStateVersion",
        "researchDesignVersion",
        "scientificFacts",
        "executionState",
        "sourceRefs",
    )
    return latest is not None and all(latest.get(field) == state.get(field) for field in fields)


def _unsettled_execution(task, design_version) -> bool:
    if task.get("status") in {"cancelled", "failed"}:
        return True
    if any(
        row.get("status") not in {"succeeded", "failed"} for row in task.get("capabilityCalls", [])
    ):
        return True
    if any(
        row.get("status") not in {"completed", "failed", "blocked", "cancelled"}
        for row in task.get("workUnits", [])
    ):
        return True
    return any(
        row.get("researchDesignVersion") == design_version and row.get("status") != "closed"
        for row in task.get("executionDecisions", [])
    )


def _instance_ref(row):
    return row["substageInstanceId"], row["instanceRevision"]


def _closed_scope(task, design, state):
    runtimes = {_instance_ref(row): row for row in task.get("substageRuntimeStates", [])}
    completion = {
        _instance_ref(row): row
        for row in state.get("executionState", {}).get("substageCompletion", [])
    }
    decisions = []
    non_execution = []
    for instance in design.get("instances", []):
        ref = _instance_ref(instance)
        runtime = runtimes.get(ref)
        row = completion.get(ref)
        if runtime is None or row is None or row.get("runtimeStatus") != runtime.get("status"):
            return None
        if runtime["status"] not in {"completed", "skipped"}:
            return None
        if runtime["status"] == "skipped" and not research_gap_continues(
            runtime.get("statusReasonCode")
        ):
            return None
        matches = [
            decision
            for decision in task.get("executionDecisions", [])
            if decision.get("researchDesignVersion") == design["version"]
            and (decision["substageInstanceId"], decision["substageInstanceRevision"]) == ref
        ]
        latest = max(matches, key=lambda decision: decision["decisionSequence"], default=None)
        if latest is None and runtime["status"] == "skipped":
            reason = runtime.get("statusReasonCode")
            if row.get("runtimeStatusReasonCode") != reason:
                return None
            non_execution.append(
                {
                    "substageInstanceId": ref[0],
                    "instanceRevision": ref[1],
                    "reasonCode": reason,
                }
            )
            continue
        if latest is None or latest.get("status") != "closed" or latest.get("closedAt") is None:
            return None
        decisions.append(latest["executionDecisionId"])
    expected_refs = {_instance_ref(row) for row in design.get("instances", [])}
    if not expected_refs or set(completion) != expected_refs:
        return None
    return decisions, non_execution


def _accepted_material_refs(task, design_version, state):
    work = {row["workUnitId"]: row for row in task.get("workUnits", [])}
    calls = {row["capabilityCallId"]: row for row in task.get("capabilityCalls", [])}
    expected = state.get("executionState", {}).get("validatedArtifactRefs", [])
    accepted = set()
    for artifact in task.get("artifacts", []):
        unit = work.get(artifact.get("workUnitId"))
        call = calls.get(artifact.get("sourceCapabilityCallId"))
        if unit is None or call is None:
            continue
        valid = (
            artifact.get("artifactState") == "usable"
            and unit.get("status") == "completed"
            and unit.get("sourceMode") == "live"
            and unit.get("researchDesignVersion") == design_version
            and call.get("status") == "succeeded"
            and call.get("workUnitId") == unit["workUnitId"]
            and artifact["artifactId"] in call.get("outputArtifactRefs", [])
        )
        if valid:
            accepted.add(artifact["artifactId"])
    if not expected or len(set(expected)) != len(expected) or not set(expected) <= accepted:
        return None
    return sorted(expected)


def derive_round_end(task: Any, design: Any, state: Any) -> dict[str, Any] | None:
    """A receipt for this scope, never evidence that the original goal was met.

    This leaf has no submission, persistence or recovery authority. The existing
    completion and final-integrity consumers recompute it from canonical facts.
    Scientific unknowns and unmet criteria are deliberately not inspected.
    """
    task, design, state = _wire(task), _wire(design), _wire(state)
    if not _current_state_matches(task, design, state) or _unsettled_execution(
        task, design["version"]
    ):
        return None
    decisions = _closed_scope(task, design, state)
    artifacts = _accepted_material_refs(task, design["version"], state)
    if decisions is None or artifacts is None:
        return None
    decisions, non_execution = decisions
    if not decisions:
        return None
    return {
        **({"nonExecutionRefs": non_execution} if non_execution else {}),
        "reason": "confirmed_scope_settled",
        "instanceRefs": [
            {"substageInstanceId": row[0], "instanceRevision": row[1]}
            for row in sorted(_instance_ref(instance) for instance in design["instances"])
        ],
        "sourceDecisionRefs": sorted(set(decisions)),
        "sourceArtifactRefs": artifacts,
    }


def validate_round_end_identity(task: Any, decision: Any, design: Any, state: Any) -> None:
    """Validate even historical receipts without requiring history to be current."""
    decision = _wire(decision)
    receipt = decision.get("roundEnd")
    if receipt is None:
        return
    task, design, state = _wire(task), _wire(design), _wire(state)
    expected_instances = {_instance_ref(row) for row in design.get("instances", [])}
    if {_instance_ref(row) for row in receipt["instanceRefs"]} != expected_instances:
        raise ValueError("round end confirmed instance scope mismatch")
    decisions = {row["executionDecisionId"]: row for row in task.get("executionDecisions", [])}
    covered = []
    for ref in receipt["sourceDecisionRefs"]:
        owner = decisions.get(ref)
        if (
            owner is None
            or owner["researchDesignVersion"] != design["version"]
            or owner["status"] != "closed"
            or owner.get("closedAt") is None
        ):
            raise ValueError("round end source decision identity mismatch")
        covered.append((owner["substageInstanceId"], owner["substageInstanceRevision"]))
    completion = {
        _instance_ref(row): row
        for row in state.get("executionState", {}).get("substageCompletion", [])
    }
    for ref in receipt.get("nonExecutionRefs", []):
        identity = _instance_ref(ref)
        source_row = completion.get(identity)
        if (
            source_row is None
            or source_row.get("runtimeStatus") != "skipped"
            or source_row.get("runtimeStatusReasonCode") != ref.get("reasonCode")
            or not research_gap_continues(ref.get("reasonCode"))
            or any(
                row["researchDesignVersion"] == design["version"]
                and (row["substageInstanceId"], row["substageInstanceRevision"]) == identity
                for row in task.get("executionDecisions", [])
            )
        ):
            raise ValueError("round end non-execution source identity mismatch")
        covered.append(identity)
    if len(covered) != len(expected_instances) or set(covered) != expected_instances:
        raise ValueError("round end source decisions do not cover the confirmed scope")
    artifacts = _accepted_material_refs(task, design["version"], state)
    if artifacts is None or receipt["sourceArtifactRefs"] != artifacts:
        raise ValueError("round end source artifact identity mismatch")
