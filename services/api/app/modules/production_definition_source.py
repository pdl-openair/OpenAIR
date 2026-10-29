"""Production DefinitionSource backed by the canonical Registry snapshot archive."""

from __future__ import annotations

from app.modules.research_orchestration_resolver import build_planning_registry_snapshot

from typing import Any, Mapping

from app.models.external_action import ExternalActionKind
from app.models.research_orchestration import RepeatableDefinitionReentryAuthority
from app.models.workflow_definition import parse_frozen_workflow_definition
from app.modules.registry_snapshot_repository import RegistrySnapshotArchive
from app.modules.catalog_execution_binding import CatalogExecutionBinding
from app.modules.research_design_service import ResolvedDefinition
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository
from app.modules.research_orchestration_resolver import build_registry_snapshot
from app.modules.registry_snapshot_repository import RegistrySnapshotRepository


class ProductionDefinitionSource:
    """Freeze one Registry snapshot and resolve Definitions only from it."""

    def __init__(
        self,
        *,
        archive: RegistrySnapshotArchive | None = None,
        snapshot_ref: Mapping[str, Any] | None = None,
        planning_only: bool = False,
    ) -> None:
        if archive is None:
            repository = ResearchOrchestrationRepository()
            archive = RegistrySnapshotRepository(repository._connect)
        if snapshot_ref is None:
            snapshot = archive.put(
                build_planning_registry_snapshot() if planning_only else build_registry_snapshot()
            )
        else:
            content_hash = str(snapshot_ref.get("contentHash") or "").strip()
            schema_version = snapshot_ref.get("schemaVersion")
            if not content_hash or isinstance(schema_version, bool) or not isinstance(schema_version, int):
                raise ValueError("registry snapshot ref is invalid")
            snapshot = archive.get(content_hash)
            if snapshot.schema_version != schema_version or snapshot.content_hash != content_hash:
                raise ValueError("registry snapshot ref does not match the archived snapshot")
        self._snapshot = snapshot
        self._definitions_by_ref: dict[tuple[str, int], dict[str, Any]] = {
            (str(row["definitionId"]), int(row["definitionVersion"])): row
            for row in snapshot.envelope["definitions"]["substages"]
        }
        self._definitions: dict[str, dict[str, Any]] = {}
        for (definition_id, definition_version), row in self._definitions_by_ref.items():
            current = self._definitions.get(definition_id)
            if current is None or definition_version > int(current["definitionVersion"]):
                self._definitions[definition_id] = row
        self._packs: dict[str, dict[str, Any]] = {
            str(row["pack"]): dict(row.get("source") or {})
            for row in snapshot.envelope["definitions"].get("packs", ())
        }
        self._agents: dict[tuple[str, int], dict[str, Any]] = {
            (str(row["agentId"]), int(row["definitionVersion"])): row
            for row in snapshot.envelope["definitions"]["agents"]
        }
        self._agent_bindings = tuple(snapshot.envelope["bindings"]["agents"])

    def snapshot_ref(self) -> dict[str, Any]:
        return {
            "schemaVersion": self._snapshot.schema_version,
            "contentHash": self._snapshot.content_hash,
        }

    def resolve(
        self,
        definition_id: str,
        definition_version: int | None = None,
    ) -> ResolvedDefinition | None:
        if definition_version is not None:
            return self.resolve_version(definition_id, definition_version)
        row = (
            self._definitions.get(definition_id)
        )
        return self._resolved_definition(row)

    def resolve_version(
        self,
        definition_id: str,
        definition_version: int,
    ) -> ResolvedDefinition | None:
        """Resolve one exact frozen identity without silently selecting latest."""
        by_ref = getattr(self, "_definitions_by_ref", None)
        if by_ref is not None:
            row = by_ref.get((definition_id, definition_version))
        else:
            candidate = self._definitions.get(definition_id)
            row = (
                candidate
                if candidate is not None
                and int(candidate["definitionVersion"]) == definition_version
                else None
            )
        return self._resolved_definition(row)

    def _resolved_definition(
        self,
        row: Mapping[str, Any] | None,
    ) -> ResolvedDefinition | None:
        if row is None:
            return None
        contract, _complete = self._definition_contract_state(row)
        required_actions, action_contract_known = self._external_action_contract_state(row)
        return ResolvedDefinition(
            definition_id=str(row["definitionId"]),
            definition_version=int(row["definitionVersion"]),
            stage_key=str(row["stage"]),
            substage_key=str(row["substageKey"]),
            required_input_refs=contract["requiredInputRefs"],
            artifact_types=contract["artifactTypes"],
            criterion_refs=contract["evaluatorRefs"],
            evaluator_refs=contract["evaluatorRefs"],
            quality_rule_refs=contract["qualityRuleRefs"],
            judgment_requirement=contract["judgmentRequirement"],
            deterministic_validator_refs=contract["deterministicValidatorRefs"],
            required_external_action_kinds=required_actions,
            external_action_contract_known=action_contract_known,
            repeatable_reentry_authority=self._reentry_authority(row),
        )

    @staticmethod
    def _reentry_authority(
        row: Mapping[str, Any],
    ) -> RepeatableDefinitionReentryAuthority | None:
        """Freeze the Definition-owned re-entry authority into the instance.

        The registry snapshot already validates the executionReentry shape
        (policyVersion, triggerDefinitionIds); an absent or non-repeatable
        policy carries no authority and stays ``None`` (fail-closed).
        """
        policy = row.get("instancePolicy")
        if not isinstance(policy, Mapping) or policy.get("mode") != "repeatable":
            return None
        reentry = policy.get("executionReentry")
        if not isinstance(reentry, Mapping):
            return None
        return RepeatableDefinitionReentryAuthority.model_validate(
            {
                "policyVersion": reentry.get("policyVersion"),
                "triggerDefinitionIds": reentry.get("triggerDefinitionIds"),
            }
        )

    def system_recovery_target(self, reason_code: str) -> Mapping[str, Any] | None:
        """Return only an authority embedded in this source's frozen snapshot."""
        rows = self._snapshot.envelope["staticPolicies"].get("systemRecoveryAuthorities", ())
        matches = [row for row in rows if row.get("reasonCode") == reason_code]
        if len(matches) != 1:
            return None
        target = dict(matches[0])
        definition_id = str(target.get("definitionId") or "")
        if definition_id not in self._definitions:
            return None
        return target

    def _planner_catalog_inventory(
        self,
        *,
        workflow_refs: set[tuple[str, int]] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Expose enabled Definitions plus exact frozen Workflow members.

        Workflow membership is a planning authority. Retaining a frozen member does
        not change its Registry enablement or grant any execution capability.
        """
        included_refs = workflow_refs or set()
        rows: list[dict[str, Any]] = []
        selected = {
            (str(row["definitionId"]), int(row["definitionVersion"])): row
            for row in self._definitions.values()
        }
        definitions_by_ref = getattr(self, "_definitions_by_ref", selected)
        for definition_ref in included_refs:
            historical = definitions_by_ref.get(definition_ref)
            if historical is not None:
                selected[definition_ref] = historical
        for row in selected.values():
            if (
                row.get("enabled") is not True
                and (str(row["definitionId"]), int(row["definitionVersion"])) not in included_refs
            ):
                continue
            contract, contract_complete = self._definition_contract_state(row)
            rows.append({
                "definitionId": str(row["definitionId"]),
                "definitionVersion": int(row["definitionVersion"]),
                "pack": str(row.get("pack") or ""),
                "stage": str(row["stage"]),
                "label": str(row["label"]),
                "description": str(row.get("description") or ""),
                "planningApplicability": self._planning_applicability(row),
                "instancePolicy": dict(row.get("instancePolicy") or {"mode": "repeatable"}),
                "contractComplete": contract_complete,
                **contract,
            })
        return tuple(rows)

    def planner_catalog_for_research_design(
        self,
        workflow_admission: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], ...]:
        """Return frozen Workflow members plus enabled general research work.

        Freeform planning retains its existing enabled Definition inventory. With a
        Workflow, its exact Definition identities and versions determine membership;
        execution composition and enablement do not remove frozen research duties.
        """
        if workflow_admission is None:
            return self._planner_catalog_inventory()
        workflow_refs = self._workflow_definition_refs(workflow_admission)
        catalog = self._planner_catalog_inventory(workflow_refs=workflow_refs)
        return tuple(
            row
            for row in catalog
            if row["planningApplicability"]["scopeKind"] == "general"
            or (str(row["definitionId"]), int(row["definitionVersion"])) in workflow_refs
        )

    def definition_is_eligible_for_workflow_admission(
        self,
        definition_id: str,
        workflow_admission: Mapping[str, Any] | None,
    ) -> bool:
        row = self._definitions.get(definition_id)
        if row is None:
            return False
        if workflow_admission is None:
            return row.get("enabled") is True
        if (
            str(row["definitionId"]),
            int(row["definitionVersion"]),
        ) in self._workflow_definition_refs(workflow_admission):
            return True
        return (
            row.get("enabled") is True
            and self._planning_applicability(row)["scopeKind"] == "general"
        )

    def _planning_applicability(self, row: Mapping[str, Any]) -> dict[str, str]:
        pack = str(row.get("pack") or "").strip()
        source = self._packs.get(pack)
        if not pack or not isinstance(source, Mapping):
            raise ValueError(f"Definition pack planning scope is unavailable: {pack or '<missing>'}")
        scope_kind = str(source.get("planning_scope") or "").strip()
        if scope_kind not in {"general", "specialized"}:
            # Historical frozen Registry snapshots predate the formal planning-scope
            # contract. Preserve that absence as an explicit unknown instead of
            # inventing domain semantics from pack IDs, labels, or execution bindings.
            scope_kind = "legacy_unspecified"
        return {
            "scopeKind": scope_kind,
            "scopeLabel": str(source.get("label") or pack).strip(),
            "scopeDescription": str(source.get("description") or "").strip(),
            "whenToUse": str(source.get("when_to_use") or "").strip(),
        }

    @staticmethod
    def _workflow_definition_refs(
        workflow_admission: Mapping[str, Any],
    ) -> set[tuple[str, int]]:
        snapshot = workflow_admission.get("workflowDefinitionSnapshot")
        try:
            frozen = parse_frozen_workflow_definition(snapshot)
        except (TypeError, ValueError) as exc:
            raise ValueError("Workflow admission snapshot is invalid") from exc
        refs = {
            (node.definition_id, node.definition_version)
            for node in frozen.definition.nodes
        }
        supporting_work = getattr(frozen.definition, "supporting_work", ())
        refs.update(
            (row.definition_id, row.definition_version)
            for row in supporting_work
        )
        return refs

    def planner_catalog_for_catalog_binding(
        self,
        binding: CatalogExecutionBinding,
    ) -> tuple[dict[str, Any], ...]:
        """Keep a user-selected Catalog capability out of ResearchDesign semantics.

        The durable Catalog pin remains an execution constraint and is enforced by
        ExecutionDecision. It must not remove scientifically required Definitions
        while Planner is forming the ResearchDesign.
        """
        del binding
        return self.planner_catalog_for_research_design(None)

    @staticmethod
    def _external_action_contract_state(
        row: Mapping[str, Any],
    ) -> tuple[tuple[ExternalActionKind, ...], bool]:
        contract = row.get("externalActionContract")
        if not isinstance(contract, Mapping) or contract.get("status") != "known":
            return (), False
        values = contract.get("requiredActionKinds")
        if not isinstance(values, (list, tuple)):
            return (), False
        try:
            actions = tuple(ExternalActionKind(value) for value in values)
        except (TypeError, ValueError):
            return (), False
        if len(set(actions)) != len(actions):
            return (), False
        return actions, True

    def _definition_contract_state(
        self,
        row: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Return known frozen contract fields without inventing missing truth.

        ``contractComplete`` is deliberately separate from planning visibility.
        Known Definition constraints are preserved and later enforced; unknown
        fields stay empty so the compiler can create only an instance-level
        planning envelope rather than fabricating a canonical Definition contract.
        """
        values: dict[str, Any] = {}
        complete = True
        for contract_key, values_key in (
            ("inputContract", "requiredInputRefs"),
            ("outputContract", "artifactTypes"),
        ):
            contract = row.get(contract_key) or {}
            declared = contract.get(values_key) or {}
            known = contract.get("status") == "known" and declared.get("status") == "known"
            field_values = tuple(
                str(value).strip()
                for value in declared.get("values") or ()
                if str(value).strip()
            ) if known else ()
            if not known or not field_values:
                complete = False
            values[values_key] = field_values

        acceptance = row.get("acceptanceContract") or {}
        if "evaluatorRefs" in acceptance:
            def acceptance_list(key: str) -> tuple[tuple[str, ...], bool]:
                declared = acceptance.get(key) or {}
                known = acceptance.get("status") == "known" and declared.get("status") == "known"
                return tuple(
                    str(value).strip()
                    for value in declared.get("values") or ()
                    if str(value).strip()
                ) if known else (), known

            quality_rules, quality_known = acceptance_list("qualityRuleRefs")
            evaluators, evaluators_known = acceptance_list("evaluatorRefs")
            validators, validators_known = acceptance_list("deterministicValidatorRefs")
            judgment = acceptance.get("judgmentRequirement") or {}
            judgment_known = acceptance.get("status") == "known" and judgment.get("status") == "known"
            judgment_value = str(judgment.get("value") or "").strip() if judgment_known else ""
            acceptance_complete = quality_known and judgment_known and evaluators_known and validators_known
            if judgment_value in {"domain_professional", "research_synthesis", "human_authority"} and not evaluators:
                acceptance_complete = False
            if judgment_value == "deterministic" and not validators:
                acceptance_complete = False
            complete = complete and acceptance_complete
            values.update({
                "qualityRuleRefs": quality_rules,
                "judgmentRequirement": judgment_value or None,
                "evaluatorRefs": evaluators,
                "criterionRefs": evaluators,
                "deterministicValidatorRefs": validators,
            })
        else:
            declared = acceptance.get("criterionRefs") or {}
            known = acceptance.get("status") == "known" and declared.get("status") == "known"
            evaluators = tuple(
                str(value).strip()
                for value in declared.get("values") or ()
                if str(value).strip()
            ) if known else ()
            if not known or not evaluators:
                complete = False
            values.update({
                "qualityRuleRefs": (),
                "judgmentRequirement": None,
                "evaluatorRefs": evaluators,
                "criterionRefs": evaluators,
                "deterministicValidatorRefs": (),
            })

        # ResearchDesign must be based on the frozen Definition contract, not
        # on the currently available specialist Agent list. Candidate selection
        # and the general-agent fallback belong to the execution-decision phase.
        return values, complete


__all__ = ["ProductionDefinitionSource"]
