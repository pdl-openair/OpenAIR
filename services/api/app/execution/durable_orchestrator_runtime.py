"""Frozen execution boundary shared by the durable worker and provider adapters."""

from __future__ import annotations

import copy
from hashlib import sha256
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from app.modules.repo_paths import repo_root


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_:-]+$")
_LOGICAL_PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_MODEL_PROFILES = frozenset({"terra", "sol"})


class FrozenExecutionRequestError(ValueError):
    """Raised when a durable run and its persisted plan cannot be executed safely."""


class ArtifactContractValidationError(ValueError):
    """A frozen Artifact body or its declarative constraints are invalid."""

    def __init__(
        self,
        error_code: str,
        field_paths: tuple[str, ...] = (),
        messages: tuple[str, ...] = (),
    ) -> None:
        self.error_code = error_code
        self.field_paths = field_paths or ("/",)
        # Human-readable "path: message" rows for repair feedback (empty for
        # non-schema error codes). Used by the execution-artifact repair loop.
        self.messages = messages
        super().__init__(f"artifact_contract_validation_failed:{error_code}")


@dataclass(frozen=True)
class FrozenArtifactContract:
    contract_id: str
    semantic_version: str
    schema_digest: str
    schema: Mapping[str, Any]
    source_ref: str

    def __post_init__(self) -> None:
        contract_id = _required_artifact_contract(self.contract_id)
        schema = _frozen_mapping(self.schema, "artifactSchema")
        semantic_version = _required_semantic_version(self.semantic_version)
        if _text(schema.get("contractId")) != contract_id:
            raise FrozenExecutionRequestError("artifact schema contractId must match the frozen contract")
        if _text(schema.get("semanticVersion")) != semantic_version:
            raise FrozenExecutionRequestError("artifact schema semanticVersion must match the frozen contract")
        digest = _schema_digest(schema)
        if not re.fullmatch(r"[0-9a-f]{64}", _text(self.schema_digest)) or self.schema_digest != digest:
            raise FrozenExecutionRequestError("artifact schema digest mismatch")
        object.__setattr__(self, "contract_id", contract_id)
        object.__setattr__(self, "semantic_version", semantic_version)
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "source_ref", _required_schema_ref(self.source_ref))

    def snapshot(self) -> dict[str, Any]:
        return {
            "artifactContract": self.contract_id,
            "artifactSemanticVersion": self.semantic_version,
            "artifactSchemaDigest": self.schema_digest,
            "artifactSchema": _thaw(self.schema),
            "artifactSchemaRef": self.source_ref,
        }


def freeze_artifact_contract_body(contract_id: str, schema_body: Mapping[str, Any], *, source_ref: str) -> FrozenArtifactContract:
    """Validate and freeze one complete, JSON-only contract body at plan time."""
    contract_id = _required_artifact_contract(contract_id)
    if not isinstance(schema_body, Mapping):
        raise FrozenExecutionRequestError("artifact schema must be a JSON object")
    schema = copy.deepcopy(_thaw(schema_body))
    if _text(schema.get("contractId")) != contract_id:
        raise FrozenExecutionRequestError("artifact schema contractId does not match the registered contract")
    semantic_version = _required_semantic_version(schema.get("semanticVersion"))
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise FrozenExecutionRequestError("artifact schema is not a valid Draft 2020-12 schema") from exc
    _validate_reference_declaration(schema)
    frozen = _frozen_mapping(schema, "artifactSchema")
    return FrozenArtifactContract(
        contract_id=contract_id,
        semantic_version=semantic_version,
        schema_digest=_schema_digest(frozen),
        schema=frozen,
        source_ref=source_ref,
    )


def load_frozen_artifact_contract(contract_id: str, schema_ref: str) -> FrozenArtifactContract:
    """Load only a repository-local JSON schema and immediately freeze it."""
    reference = _required_schema_ref(schema_ref)
    root = repo_root().resolve()
    candidate = (root / reference).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FrozenExecutionRequestError("artifact schema reference escapes the repository") from exc
    if candidate.suffix.lower() != ".json" or not candidate.is_file():
        raise FrozenExecutionRequestError("artifact schema reference must name an existing JSON file")
    try:
        with candidate.open("r", encoding="utf-8") as stream:
            schema = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrozenExecutionRequestError("artifact schema could not be parsed as JSON") from exc
    return freeze_artifact_contract_body(contract_id, schema, source_ref=reference)


def frozen_artifact_contract_from_snapshot(value: Any) -> FrozenArtifactContract:
    if not isinstance(value, Mapping):
        raise FrozenExecutionRequestError("frozen artifact contract snapshot must be a mapping")
    contract = _required_artifact_contract(value.get("artifactContract"))
    frozen = freeze_artifact_contract_body(
        contract,
        _frozen_mapping(value.get("artifactSchema"), "artifactSchema"),
        source_ref=_required_schema_ref(value.get("artifactSchemaRef")),
    )
    if _required_semantic_version(value.get("artifactSemanticVersion")) != frozen.semantic_version:
        raise FrozenExecutionRequestError("frozen artifact semanticVersion mismatch")
    if _text(value.get("artifactSchemaDigest")) != frozen.schema_digest:
        raise FrozenExecutionRequestError("frozen artifact schema digest mismatch")
    return frozen


def validate_frozen_artifact_content(contract: FrozenArtifactContract, content: str | bytes) -> dict[str, Any]:
    """Validate a provider body using only its persisted schema snapshot."""
    if not isinstance(content, (str, bytes)):
        raise ArtifactContractValidationError("artifact_content_invalid_json", ("/",))
    try:
        decoded = content.decode("utf-8") if isinstance(content, bytes) else content
        body = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ArtifactContractValidationError("artifact_content_invalid_json", ("/",)) from None
    if not isinstance(body, dict):
        raise ArtifactContractValidationError("artifact_content_root_type", ("/",))
    schema = _thaw(contract.schema)
    errors = sorted(
        (error for error in Draft202012Validator(schema).iter_errors(body)
         if error.validator == "required"),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        paths = tuple(dict.fromkeys(_error_pointer(error.absolute_path) for error in errors[:20]))
        messages = tuple(
            f"{_error_pointer(error.absolute_path)}: {error.message}"
            for error in errors[:20]
        )
        raise ArtifactContractValidationError("artifact_schema_invalid", paths, messages)
    return body


def build_frozen_orchestration_execution_request(
    run: Mapping[str, Any], plan: Mapping[str, Any],
) -> "FrozenOrchestrationExecutionRequest":
    """Construct an execution request without routing or inspecting mutable task state."""
    snapshot = run.get("input_snapshot")
    context = plan.get("context") if isinstance(plan, Mapping) else None
    if not isinstance(snapshot, Mapping) or not isinstance(context, Mapping):
        raise FrozenExecutionRequestError("Persisted run and plan must contain mappings")

    identifiers = {
        "runId": _required_identifier(run.get("run_id"), "runId"),
        "taskId": _required_identifier(run.get("task_id"), "taskId"),
        "ownerId": _required_identifier(run.get("owner_id"), "ownerId"),
        "workspaceId": _required_identifier(run.get("workspace_id"), "workspaceId"),
    }
    for field, value in identifiers.items():
        if _text(context.get(field)) != value:
            raise FrozenExecutionRequestError(f"Persisted plan context does not match {field}")

    provider_id = _required_logical_provider(plan.get("selectedProvider"))
    selected_agent_id = _required_identifier(plan.get("selectedAgentId"), "selectedAgentId")
    skill_ids = _skill_ids(plan.get("selectedSkillIds"))
    return FrozenOrchestrationExecutionRequest(
        **identifiers,
        orchestrationPlan=plan,
        frozenInputSnapshot=snapshot,
        selectedAgentId=selected_agent_id,
        selectedSkillIds=skill_ids,
        logicalProviderId=provider_id,
        modelProfile=_required_model_profile(plan.get("modelProfile")),
        artifactContract=_required_artifact_contract(plan.get("artifactContract")),
        artifactContractSchemaVersion=_required_schema_version(plan.get("artifactContractSchemaVersion")),
        artifactRequiredFields=_artifact_required_fields(plan.get("artifactRequiredFields")),
        artifactSemanticVersion=_required_semantic_version(plan.get("artifactSemanticVersion")),
        artifactSchemaDigest=_text(plan.get("artifactSchemaDigest")),
        artifactSchema=_frozen_mapping(plan.get("artifactSchema"), "artifactSchema"),
        artifactSchemaRef=_required_schema_ref(plan.get("artifactSchemaRef")),
    )


@dataclass(frozen=True)
class FrozenOrchestrationExecutionRequest:
    """An immutable, already-routed execution request for exactly one durable run."""

    runId: str
    taskId: str
    ownerId: str
    workspaceId: str
    orchestrationPlan: Mapping[str, Any]
    frozenInputSnapshot: Mapping[str, Any]
    selectedAgentId: str
    selectedSkillIds: tuple[str, ...]
    logicalProviderId: str
    modelProfile: str
    artifactContract: str
    artifactContractSchemaVersion: int
    artifactRequiredFields: tuple[str, ...]
    artifactSemanticVersion: str
    artifactSchemaDigest: str
    artifactSchema: Mapping[str, Any]
    artifactSchemaRef: str
    # Research dispatch has no server-side continuation for provider skill requests.
    # Keep the durable orchestration default for the existing multi-agent path.
    allowSkillRequests: bool = True

    def __post_init__(self) -> None:
        for field in ("runId", "taskId", "ownerId", "workspaceId", "selectedAgentId"):
            object.__setattr__(self, field, _required_identifier(getattr(self, field), field))
        object.__setattr__(self, "logicalProviderId", _required_logical_provider(self.logicalProviderId))
        object.__setattr__(self, "modelProfile", _required_model_profile(self.modelProfile))
        object.__setattr__(self, "selectedSkillIds", _skill_ids(self.selectedSkillIds))
        object.__setattr__(self, "artifactContract", _required_artifact_contract(self.artifactContract))
        object.__setattr__(self, "artifactContractSchemaVersion", _required_schema_version(self.artifactContractSchemaVersion))
        object.__setattr__(self, "artifactRequiredFields", _artifact_required_fields(self.artifactRequiredFields))
        frozen_contract = FrozenArtifactContract(
            contract_id=self.artifactContract,
            semantic_version=self.artifactSemanticVersion,
            schema_digest=self.artifactSchemaDigest,
            schema=_frozen_mapping(self.artifactSchema, "artifactSchema"),
            source_ref=self.artifactSchemaRef,
        )
        object.__setattr__(self, "artifactSemanticVersion", frozen_contract.semantic_version)
        object.__setattr__(self, "artifactSchemaDigest", frozen_contract.schema_digest)
        object.__setattr__(self, "artifactSchema", frozen_contract.schema)
        object.__setattr__(self, "artifactSchemaRef", frozen_contract.source_ref)
        plan = _frozen_mapping(self.orchestrationPlan, "orchestrationPlan")
        snapshot = _frozen_mapping(self.frozenInputSnapshot, "frozenInputSnapshot")
        _assert_plan_matches_request(plan, self)
        object.__setattr__(self, "orchestrationPlan", plan)
        object.__setattr__(self, "frozenInputSnapshot", snapshot)

    def transport_payload(self) -> dict[str, Any]:
        """Return only the persisted plan and API-authoritative snapshot for one call."""
        return {
            "context": {
                "runId": self.runId,
                "taskId": self.taskId,
                "ownerId": self.ownerId,
                "workspaceId": self.workspaceId,
            },
            "orchestrationPlan": _thaw(self.orchestrationPlan),
            "frozenInputSnapshot": _thaw(self.frozenInputSnapshot),
            "expectedArtifactContract": {
                "artifactContract": self.artifactContract,
                "schemaVersion": self.artifactContractSchemaVersion,
                "requiredFields": list(self.artifactRequiredFields),
                "semanticVersion": self.artifactSemanticVersion,
                "schemaDigest": self.artifactSchemaDigest,
                "schema": _thaw(self.artifactSchema),
                "schemaRef": self.artifactSchemaRef,
            },
        }


def _assert_plan_matches_request(plan: Mapping[str, Any], request: FrozenOrchestrationExecutionRequest) -> None:
    if _text(plan.get("selectedAgentId")) != request.selectedAgentId:
        raise FrozenExecutionRequestError("Execution request agent must match the persisted plan")
    if _skill_ids(plan.get("selectedSkillIds")) != request.selectedSkillIds:
        raise FrozenExecutionRequestError("Execution request skills must match the persisted plan")
    if _text(plan.get("selectedProvider")) != request.logicalProviderId:
        raise FrozenExecutionRequestError("Execution request provider must match the persisted plan")
    policy = plan.get("providerPolicy")
    allowed = policy.get("allowedProviders") if isinstance(policy, Mapping) else None
    if not isinstance(allowed, (list, tuple)) or request.logicalProviderId not in allowed:
        raise FrozenExecutionRequestError("Execution request provider must be allowed by the persisted plan")
    if _required_artifact_contract(plan.get("artifactContract")) != request.artifactContract:
        raise FrozenExecutionRequestError("Execution request artifact contract must match the persisted plan")
    if _required_schema_version(plan.get("artifactContractSchemaVersion")) != request.artifactContractSchemaVersion:
        raise FrozenExecutionRequestError("Execution request artifact contract schema version must match the persisted plan")
    if _artifact_required_fields(plan.get("artifactRequiredFields")) != request.artifactRequiredFields:
        raise FrozenExecutionRequestError("Execution request artifact required fields must match the persisted plan")
    plan_contract = frozen_artifact_contract_from_snapshot(plan)
    request_contract = FrozenArtifactContract(
        contract_id=request.artifactContract,
        semantic_version=request.artifactSemanticVersion,
        schema_digest=request.artifactSchemaDigest,
        schema=request.artifactSchema,
        source_ref=request.artifactSchemaRef,
    )
    if plan_contract != request_contract:
        raise FrozenExecutionRequestError("Execution request frozen artifact schema must match the persisted plan")


def _frozen_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FrozenExecutionRequestError(f"{field} must be a mapping")
    return _freeze(copy.deepcopy(_thaw(value)))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list) or isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _required_identifier(value: Any, field: str) -> str:
    text = _text(value)
    if not _IDENTIFIER_PATTERN.fullmatch(text):
        raise FrozenExecutionRequestError(f"{field} must be a non-empty identifier")
    return text


def _required_logical_provider(value: Any) -> str:
    provider_id = _text(value)
    if not _LOGICAL_PROVIDER_PATTERN.fullmatch(provider_id):
        raise FrozenExecutionRequestError("logicalProviderId must be a logical provider ID")
    return provider_id


def _required_model_profile(value: Any) -> str:
    profile = _text(value)
    if profile not in _MODEL_PROFILES:
        raise FrozenExecutionRequestError("modelProfile must be terra or sol")
    return profile


def _required_artifact_contract(value: Any) -> str:
    contract = _text(value)
    if not _IDENTIFIER_PATTERN.fullmatch(contract):
        raise FrozenExecutionRequestError("artifactContract must be a non-empty identifier")
    return contract


def _required_schema_version(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise FrozenExecutionRequestError("artifactContractSchemaVersion must be a positive integer")
    return value


def _artifact_required_fields(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise FrozenExecutionRequestError("artifactRequiredFields must be a list of identifiers")
    fields = tuple(_required_identifier(item, "artifactRequiredFields") for item in value)
    if not fields or len(set(fields)) != len(fields):
        raise FrozenExecutionRequestError("artifactRequiredFields must be non-empty and unique")
    return fields


def _skill_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise FrozenExecutionRequestError("selectedSkillIds must be a list of identifiers")
    skill_ids = tuple(_required_identifier(item, "selectedSkillIds") for item in value)
    if len(set(skill_ids)) != len(skill_ids):
        raise FrozenExecutionRequestError("selectedSkillIds must not contain duplicates")
    return skill_ids


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_semantic_version(value: Any) -> str:
    version = _text(value)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise FrozenExecutionRequestError("artifact semanticVersion must be a semantic version")
    return version


def _required_schema_ref(value: Any) -> str:
    reference = _text(value).replace("\\", "/")
    path = Path(reference)
    if not reference or path.is_absolute() or ".." in path.parts:
        raise FrozenExecutionRequestError("artifact schema reference must be a safe repo-relative path")
    return reference


def _schema_digest(schema: Mapping[str, Any]) -> str:
    return sha256(json.dumps(_thaw(schema), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def _error_pointer(path: Any) -> str:
    values = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(values) if values else "/"


def _validate_reference_declaration(schema: Mapping[str, Any]) -> None:
    declaration = schema.get("x-openair-referenceConstraints")
    if declaration is None:
        return
    if not isinstance(declaration, Mapping):
        raise FrozenExecutionRequestError("reference constraints declaration must be an object")
    declaration = _thaw(declaration)
    meta = _load_reference_constraints_meta_schema()
    errors = list(Draft202012Validator(meta).iter_errors(_thaw(declaration)))
    if errors:
        raise FrozenExecutionRequestError("reference constraints declaration violates v1 meta-schema")
    collections = declaration.get("identifierCollections")
    rules = declaration.get("rules")
    if not isinstance(collections, list) or not isinstance(rules, list):
        raise FrozenExecutionRequestError("reference constraints declaration is incomplete")
    collection_pointers: dict[str, list[tuple[str, ...]]] = {}
    for collection in collections:
        if not isinstance(collection, Mapping):
            raise FrozenExecutionRequestError("reference constraint collection is invalid")
        collection_id = _text(collection.get("id"))
        if not collection_id or collection_id in collection_pointers:
            raise FrozenExecutionRequestError("reference constraint collection IDs must be unique")
        pointers = [_pointer_segments(pointer) for pointer in collection.get("identifierPointers") or []]
        for pointer in pointers:
            _validate_schema_pointer(schema, pointer)
        collection_pointers[collection_id] = pointers
    seen_rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise FrozenExecutionRequestError("reference constraint rule is invalid")
        rule_id = _text(rule.get("id"))
        kind = _text(rule.get("kind"))
        error_code = _text(rule.get("errorCode"))
        if not rule_id or rule_id in seen_rule_ids or not error_code:
            raise FrozenExecutionRequestError("reference constraint rule ID or errorCode is invalid")
        seen_rule_ids.add(rule_id)
        if kind == "exists":
            pointer = _pointer_segments(rule.get("sourceReferencePointer"))
            _validate_schema_pointer(schema, pointer)
            _require_collection(collection_pointers, rule.get("targetCollectionId"))
        elif kind == "reciprocal":
            source_collection = _require_collection(collection_pointers, rule.get("sourceCollectionId"))
            target_collection = _require_collection(collection_pointers, rule.get("targetCollectionId"))
            source_pointer = _pointer_segments(rule.get("sourceReferencePointer"))
            target_pointer = _pointer_segments(rule.get("targetReferencePointer"))
            _validate_schema_pointer(schema, source_pointer)
            _validate_schema_pointer(schema, target_pointer)
            _require_unambiguous_relation_pointer(source_collection, source_pointer)
            _require_unambiguous_relation_pointer(target_collection, target_pointer)
        elif kind == "consistent":
            _require_collection(collection_pointers, rule.get("leftCollectionId"))
            _require_collection(collection_pointers, rule.get("rightCollectionId"))
        else:
            raise FrozenExecutionRequestError("reference constraint rule kind is unknown")


def _load_reference_constraints_meta_schema() -> dict[str, Any]:
    path = repo_root() / "schemas" / "agents" / "artifact-reference-constraints-v1.schema.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrozenExecutionRequestError("reference constraints v1 meta-schema is unavailable") from exc
    if not isinstance(value, dict):
        raise FrozenExecutionRequestError("reference constraints v1 meta-schema is invalid")
    return value


def _pointer_segments(value: Any) -> tuple[str, ...]:
    pointer = _text(value)
    if not pointer.startswith("/"):
        raise FrozenExecutionRequestError("reference constraint pointer is invalid")
    segments = tuple(token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/"))
    if not segments or any(not segment for segment in segments):
        raise FrozenExecutionRequestError("reference constraint pointer is invalid")
    return segments


def _resolve_local_schema(schema: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
    current = value
    seen: set[str] = set()
    while isinstance(current, Mapping) and isinstance(current.get("$ref"), str):
        ref = current["$ref"]
        if not ref.startswith("#/") or ref in seen:
            raise FrozenExecutionRequestError("reference constraint pointer uses an unsupported schema reference")
        seen.add(ref)
        target: Any = schema
        for token in ref[2:].split("/"):
            if not isinstance(target, Mapping) or token not in target:
                raise FrozenExecutionRequestError("reference constraint pointer has an invalid schema reference")
            target = target[token]
        current = target
    if not isinstance(current, Mapping):
        raise FrozenExecutionRequestError("reference constraint pointer has an invalid schema target")
    return current


def _validate_schema_pointer(schema: Mapping[str, Any], pointer: tuple[str, ...]) -> None:
    current: Mapping[str, Any] = schema
    for token in pointer:
        current = _resolve_local_schema(schema, current)
        kind = current.get("type")
        if token == "*":
            if kind != "array" or not isinstance(current.get("items"), Mapping):
                raise FrozenExecutionRequestError("reference constraint has an ambiguous wildcard")
            current = current["items"]
            continue
        if kind != "object" or not isinstance(current.get("properties"), Mapping) or token not in current["properties"]:
            raise FrozenExecutionRequestError("reference constraint pointer does not resolve in the artifact schema")
        current = current["properties"][token]


def _require_collection(collections: Mapping[str, Any], value: Any) -> list[tuple[str, ...]]:
    collection_id = _text(value)
    if collection_id not in collections:
        raise FrozenExecutionRequestError("reference constraint references an unknown identifier collection")
    return collections[collection_id]


def _require_unambiguous_relation_pointer(collection: list[tuple[str, ...]], pointer: tuple[str, ...]) -> None:
    matches = [candidate for candidate in collection if _pattern_is_prefix(candidate[:-1], pointer)]
    if len(matches) != 1:
        raise FrozenExecutionRequestError("reference constraint relation pointer is ambiguous")


def _pattern_is_prefix(prefix: tuple[str, ...], value: tuple[str, ...]) -> bool:
    return len(prefix) <= len(value) and all(left == right for left, right in zip(prefix, value))
