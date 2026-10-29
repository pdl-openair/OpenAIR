"""OpenAI-compatible chat-completions adapter for one frozen v1.2 research dispatch.

Historically named ``*huiyan*`` because the first credential gateway was HUIYAN.
HUIYAN is only a token/base-URL source; this module speaks the shared OpenAI
chat-completions shape and is selected by registry ``adapter`` id
(``huiyan_chat_completions`` today). Provider identity comes from
``provider_id`` / runtime config, not from the module name.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.execution.academic_retrieval_query import build_academic_retrieval_payload
from app.execution.durable_orchestrator_runtime import (
    FrozenArtifactContract,
    FrozenOrchestrationExecutionRequest,
    validate_frozen_artifact_content,
)
from app.execution.durable_skill_runtime import normalize_external_action_evidence
from app.execution.openai_responses_provider import (
    HttpOpenAIResponsesTransport,
    OpenAIResponsesProviderAdapter,
    ProviderAttemptEvidenceUnavailableError,
    ProviderContractError,
    ProviderExecutionError,
    ProviderRejectedRequestError,
    ProviderTransportNotSubmittedError,
    ProviderTransportUncertainError,
)
from app.execution.research_dispatch_provider import (
    DispatchProviderKnownRejected,
    DispatchProviderPermanentPreflightError,
    DispatchProviderResultUnknown,
    DispatchSubmissionCompleted,
    DispatchSubmissionQueryResult,
    DispatchSubmissionRequest,
)
from app.execution.skill_dependency_runtime import (
    SkillDependencyContractError,
    execute_frozen_agent_skill,
)
from app.modules.artifact_write import artifact_write_service
from app.modules.provider_registry import (
    ProviderRegistry,
    ProviderSubmissionSafety,
    load_provider_registry,
    resolve_provider_runtime_config,
)
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
)

DISPATCH_MODEL_PROFILE = "terra"
_KEY_LITERATURE_AGENT_ID = "key_literature_positioning"
_KEY_LITERATURE_ARTIFACT_CONTRACT = "key_literature_positioning_artifact_v1"
_KEY_LITERATURE_RETRIEVAL_SKILL_ID = "academic_source_retrieval_skill"
_KEY_LITERATURE_RETRIEVAL_ADAPTER = "python_adapter:academic_source_retrieval_skill"
_SOURCE_IDENTITY_KEYS = frozenset({
    "source_id",
    "sourceid",
    "source_identifier",
    "sourceidentifier",
    "source_ref",
    "sourceref",
    "external_id",
    "externalid",
    "paper_id",
    "paperid",
    "openalex_id",
    "openalexid",
    "doi",
    "arxiv_id",
    "arxivid",
    "url",
})


@dataclass(frozen=True)
class _KeyLiteratureRetrievalEvidence:
    material: Mapping[str, Any]
    external_action_evidence: tuple[Mapping[str, str], ...]


def _requires_key_literature_retrieval(
    frozen_request: FrozenOrchestrationExecutionRequest,
) -> bool:
    return (
        frozen_request.selectedAgentId == _KEY_LITERATURE_AGENT_ID
        and frozen_request.artifactContract == _KEY_LITERATURE_ARTIFACT_CONTRACT
    )


def _require_key_literature_retrieval_authority(
    request: DispatchSubmissionRequest,
    frozen_request: FrozenOrchestrationExecutionRequest,
) -> None:
    if _KEY_LITERATURE_RETRIEVAL_SKILL_ID not in frozen_request.selectedSkillIds:
        raise DispatchProviderKnownRejected("key_literature_retrieval_skill_not_frozen")
    authorization = request.payload.get("authorizationSnapshot")
    confirmations = authorization.get("confirmationRefs") if isinstance(authorization, Mapping) else None
    if not isinstance(confirmations, (list, tuple)) or not any(str(ref or "").strip() for ref in confirmations):
        raise DispatchProviderKnownRejected("key_literature_retrieval_not_authorized")


def _key_literature_retrieval_query(
    frozen_request: FrozenOrchestrationExecutionRequest,
) -> str:
    snapshot = frozen_request.frozenInputSnapshot
    resolved = snapshot.get("resolvedInputMemory")
    if isinstance(resolved, Mapping):
        user_goal = str(resolved.get("user_goal") or "").strip()
        if user_goal:
            return user_goal
    instruction = str(snapshot.get("instruction") or "").strip()
    if instruction:
        return instruction
    raise DispatchProviderKnownRejected("key_literature_retrieval_query_missing")


def _key_literature_retrieval_storage_ref(content_sha256: str) -> str:
    return (
        "durable/external-action-evidence/"
        f"{_KEY_LITERATURE_RETRIEVAL_SKILL_ID}/{content_sha256}.json"
    )


def _decode_key_literature_retrieval_result(
    result: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any], tuple[Mapping[str, str], ...]]:
    state = str(result.get("state") or "").strip()
    if state == "unknown" or result.get("externalResultUnknown") is True:
        raise DispatchProviderResultUnknown("key_literature_retrieval_result_unknown")
    if state != "succeeded":
        detail = {
            key: value
            for key in ("failedStage", "firstMismatch")
            if isinstance((value := result.get(key)), str) and value
        }
        classification = str(result.get("failureClassification") or "").strip()
        if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", classification) is None:
            classification = "key_literature_retrieval_failed"
        raise DispatchProviderKnownRejected(
            classification,
            failure_detail=detail or None,
        )
    try:
        evidence = normalize_external_action_evidence(result.get("externalActionEvidence"))
    except ValueError as exc:
        raise DispatchProviderKnownRejected("key_literature_retrieval_evidence_invalid") from exc
    if len(evidence) != 1 or evidence[0]["adapter"] != _KEY_LITERATURE_RETRIEVAL_ADAPTER:
        raise DispatchProviderKnownRejected("key_literature_retrieval_evidence_invalid")
    payload = result.get("artifactPayload")
    presentation = payload.get("presentation") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or payload.get("contentType") != "application/json"
        or not isinstance(presentation, Mapping)
    ):
        raise DispatchProviderKnownRejected("key_literature_retrieval_payload_invalid")
    try:
        raw = base64.b64decode(str(payload.get("contentBase64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise DispatchProviderKnownRejected("key_literature_retrieval_payload_invalid") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if (
        not raw
        or str(presentation.get("contentSha256") or "").strip().lower() != digest
        or evidence[0]["contentSha256"] != digest
    ):
        raise DispatchProviderKnownRejected("key_literature_retrieval_evidence_invalid")
    try:
        material = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchProviderKnownRejected("key_literature_retrieval_payload_invalid") from exc
    candidates = material.get("metadata_candidates") if isinstance(material, dict) else None
    if not isinstance(candidates, list) or not candidates:
        raise DispatchProviderKnownRejected("key_literature_retrieval_empty")
    return raw, material, evidence


def _persist_and_rehydrate_key_literature_retrieval(
    request: DispatchSubmissionRequest,
    *,
    raw: bytes,
    evidence: tuple[Mapping[str, str], ...],
) -> dict[str, Any]:
    storage_ref = _key_literature_retrieval_storage_ref(evidence[0]["contentSha256"])
    try:
        stored = artifact_write_service.store_durable_bytes(
            request.task_id,
            storage_ref,
            raw,
            "application/json",
        )
        stored_type = str(getattr(stored, "storage_type", "") or "").strip()
        stored_ref = str(getattr(stored, "storage_ref", "") or "").strip()
        stored_scope = str(getattr(stored, "storage_scope", "") or "").strip() or None
        if not stored_type or not stored_ref:
            raise ValueError("durable retrieval evidence storage identity missing")
        durable_raw = artifact_write_service.read_durable_bytes(
            request.task_id,
            stored_type,
            stored_ref,
            stored_scope,
        )
    except Exception as exc:
        raise DispatchProviderResultUnknown("key_literature_retrieval_evidence_unavailable") from exc
    if durable_raw != raw:
        raise DispatchProviderResultUnknown("key_literature_retrieval_evidence_unavailable")
    try:
        material = json.loads(durable_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchProviderResultUnknown("key_literature_retrieval_evidence_unavailable") from exc
    if not isinstance(material, dict):
        raise DispatchProviderResultUnknown("key_literature_retrieval_evidence_unavailable")
    return material


def _normalized_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _normalized_source_identifier(value: Any, *, key: str = "") -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip()).strip(".,; ")
    if not raw:
        return ""
    lowered = raw.casefold()
    normalized_key = key.casefold().replace("-", "_")
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if lowered.startswith(prefix):
            return "doi:" + lowered[len(prefix):].strip()
    if normalized_key == "doi" or re.match(r"^10\.\d{4,9}/", lowered):
        return "doi:" + lowered.removeprefix("doi:").strip()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:"):
        if lowered.startswith(prefix):
            return "arxiv:" + re.sub(r"v\d+$", "", lowered[len(prefix):].strip())
    if normalized_key in {"arxiv_id", "arxivid"} or re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", lowered):
        return "arxiv:" + re.sub(r"v\d+$", "", lowered)
    for prefix in ("https://openalex.org/", "http://openalex.org/", "openalex:"):
        if lowered.startswith(prefix):
            return "openalex:" + lowered[len(prefix):].strip().rstrip("/")
    if normalized_key in {"openalex_id", "openalexid"} or re.fullmatch(r"w\d+", lowered):
        return "openalex:" + lowered.removeprefix("openalex:").strip()
    return lowered.rstrip("/")


def _stable_generic_source_identifier(value: Any) -> str:
    raw = str(value or "").strip()
    lowered = raw.casefold()
    if (
        lowered.startswith((
            "http://",
            "https://",
            "doi:",
            "arxiv:",
            "openalex:",
        ))
        or re.match(r"^10\.\d{4,9}/", lowered)
        or re.fullmatch(r"w\d+", lowered)
        or re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", lowered)
    ):
        return _normalized_source_identifier(raw, key="id")
    return ""


def _retrieved_candidate_identity_index(retrieval_material: Mapping[str, Any]) -> dict[str, set[str]]:
    candidates = retrieval_material.get("metadata_candidates")
    if not isinstance(candidates, list):
        return {}
    index: dict[str, set[str]] = {}
    for row in candidates:
        if not isinstance(row, Mapping):
            continue
        title = _normalized_title(row.get("title"))
        provenance = row.get("provenance")
        has_provenance = bool(provenance) or bool(str(row.get("source_name") or "").strip())
        if not title or not has_provenance:
            continue
        identities: set[str] = set()
        for key, value in row.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if normalized_key in _SOURCE_IDENTITY_KEYS and isinstance(value, (str, int)):
                identity = _normalized_source_identifier(value, key=normalized_key)
                if identity:
                    identities.add(identity)
            elif normalized_key == "id" and isinstance(value, (str, int)):
                identity = _stable_generic_source_identifier(value)
                if identity:
                    identities.add(identity)
        if isinstance(provenance, Mapping):
            for key, value in provenance.items():
                normalized_key = str(key).casefold().replace("-", "_")
                if normalized_key in _SOURCE_IDENTITY_KEYS and isinstance(value, (str, int)):
                    identity = _normalized_source_identifier(value, key=normalized_key)
                    if identity:
                        identities.add(identity)
                elif normalized_key == "id" and isinstance(value, (str, int)):
                    identity = _stable_generic_source_identifier(value)
                    if identity:
                        identities.add(identity)
        if identities:
            index.setdefault(title, set()).update(identities)
    return index


def _execute_key_literature_retrieval(
    request: DispatchSubmissionRequest,
    frozen_request: FrozenOrchestrationExecutionRequest,
    *,
    repository: ResearchOrchestrationRepository,
) -> _KeyLiteratureRetrievalEvidence:
    _require_key_literature_retrieval_authority(request, frozen_request)
    snapshot_ref = request.payload.get("registrySnapshotRef")
    content_hash = (
        str(snapshot_ref.get("contentHash") or "").strip()
        if isinstance(snapshot_ref, Mapping)
        else ""
    )
    if not content_hash:
        raise DispatchProviderKnownRejected("skill_dependency_snapshot_missing")
    snapshot = repository.load_registry_snapshot(content_hash)
    try:
        user_query = _key_literature_retrieval_query(frozen_request)
        retrieval_payload = build_academic_retrieval_payload(user_query)
        result = execute_frozen_agent_skill(
            snapshot=snapshot,
            target_skill_ref=(_KEY_LITERATURE_RETRIEVAL_SKILL_ID, 1),
            parameters=retrieval_payload,
            canonical_user_query=user_query,
            request=request,
            requesting_actor_id=frozen_request.selectedAgentId,
        )
    except SkillDependencyContractError as exc:
        raise DispatchProviderKnownRejected(exc.code) from exc
    raw, _material, evidence = _decode_key_literature_retrieval_result(result)
    durable_material = _persist_and_rehydrate_key_literature_retrieval(
        request,
        raw=raw,
        evidence=evidence,
    )
    if not _retrieved_candidate_identity_index(durable_material):
        raise DispatchProviderKnownRejected("key_literature_retrieval_identity_missing")
    return _KeyLiteratureRetrievalEvidence(
        material={
            "skillId": _KEY_LITERATURE_RETRIEVAL_SKILL_ID,
            "adapter": _KEY_LITERATURE_RETRIEVAL_ADAPTER,
            "outcome": evidence[0]["outcome"],
            "evidenceRef": evidence[0]["evidenceRef"],
            "contentSha256": evidence[0]["contentSha256"],
            "groundingRule": (
                "Use only metadata_candidates from retrievalResult. Every candidate_literature title "
                "and source_identifier must match the same retrieved source record; never invent, "
                "infer, or substitute a citation."
            ),
            "retrievalResult": durable_material,
        },
        external_action_evidence=evidence,
    )



def _seed_source_identifier(row: Mapping[str, Any]) -> str | None:
    """Return one identity copied only from the durable retrieval record."""
    preferred = (
        "doi",
        "arxiv_id",
        "openalex_id",
        "source_identifier",
        "source_id",
        "paper_id",
        "url",
        "id",
    )
    sources: list[Mapping[str, Any]] = [row]
    provenance = row.get("provenance")
    if isinstance(provenance, Mapping):
        sources.append(provenance)
    for source in sources:
        for key in preferred:
            value = source.get(key)
            if not isinstance(value, (str, int)):
                continue
            normalized = (
                _stable_generic_source_identifier(value)
                if key == "id"
                else _normalized_source_identifier(value, key=key)
            )
            if normalized:
                return normalized[:500]
    return None


def _key_literature_seed_content(
    frozen_request: FrozenOrchestrationExecutionRequest,
    retrieval: _KeyLiteratureRetrievalEvidence,
) -> str:
    """Build a truthful minimum-sufficient literature artifact from retrieval only.

    The hot path intentionally does not ask a second model to rewrite citations.
    Every title and source identifier is copied from durable retrieval evidence;
    scientific relevance/ranking remains explicitly provisional for downstream
    evidence extraction.
    """
    retrieval_result = retrieval.material.get("retrievalResult")
    if not isinstance(retrieval_result, Mapping):
        raise DispatchProviderKnownRejected("key_literature_retrieval_evidence_invalid")
    rows = retrieval_result.get("metadata_candidates")
    if not isinstance(rows, list) or not rows:
        raise DispatchProviderKnownRejected("key_literature_retrieval_empty")

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw_title = str(row.get("title") or "").strip()
        source_identifier = _seed_source_identifier(row)
        normalized_title = _normalized_title(raw_title)
        if not normalized_title or not source_identifier:
            continue
        dedupe_key = (normalized_title, source_identifier)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidate_id = f"retrieval_seed_{len(candidates) + 1:02d}"
        candidates.append({
            "candidate_id": candidate_id,
            "source_identifier": source_identifier,
            "title": raw_title[:500],
            "screening_status": "seed",
            "screening_rationale": (
                "Retrieved from a durable scholarly source and preserved as a provenance-grounded "
                "seed; scientific relevance and priority are not yet asserted."
            ),
            "role_hint": "other",
        })
        if len(candidates) >= 20:
            break
    if not candidates:
        raise DispatchProviderKnownRejected("key_literature_retrieval_identity_missing")

    query = _key_literature_retrieval_query(frozen_request).strip()
    target_question = query[:500]
    if len(target_question) < 10:
        target_question = f"Research question: {target_question}"[:500]
    keyword = query[:80] or "research"

    report = retrieval_result.get("retrieval_report")
    source_counts = report.get("source_counts") if isinstance(report, Mapping) else None
    collections = []
    if isinstance(source_counts, Mapping):
        collections = [
            str(name)[:120]
            for name in source_counts
            if str(name or "").strip()
        ][:12]
    if not collections:
        collections = sorted({
            str(row.get("source_name") or "").strip()[:120]
            for row in rows
            if isinstance(row, Mapping) and str(row.get("source_name") or "").strip()
        })[:12]

    body = {
        "search_boundary": {
            "authorization_status": "retrieval_authorized",
            "retrieval_outcome": "attempted",
            "target_question": target_question,
            "included_scope": "Scholarly metadata retrieved for the confirmed research question.",
            "excluded_scope": "Unverified scientific claims, relevance rankings, and unsupported citation substitutions.",
            "constraints": [],
        },
        "keyword_families": [{
            "family_id": "confirmed_query",
            "label": "Confirmed research query",
            "keywords": [keyword],
        }],
        "source_scope": {
            "source_kinds": ["other_scholarly"],
            "languages": ["unspecified"],
            "collections": collections,
        },
        "candidate_literature": candidates,
        "reading_priority": [],
        "coverage_limitations": [{
            "limitation_id": "retrieval_seed_only",
            "limitation_kind": "method",
            "statement": (
                "This artifact preserves provenance-grounded retrieval seeds only; scientific relevance, "
                "priority, and claims must be verified during downstream evidence extraction."
            ),
        }],
        "handoff": {
            "handoff_status": "ready_for_next_consumer",
            "next_consumer": "paper_evidence_extraction",
            "candidate_ids": [row["candidate_id"] for row in candidates],
            "instruction": (
                "Verify scientific relevance and extract evidence only from the provenance-grounded candidates; "
                "retain uncertainty and testing conditions instead of inferring unsupported claims."
            ),
            "requires_user_confirmation_before_external_execution": True,
        },
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


_logger = logging.getLogger("openair.research_dispatch.openai_compatible")
_OPENAI_COMPATIBLE_ADAPTER = "huiyan_chat_completions"  # registry adapter id; not a vendor lock-in


@dataclass(frozen=True)
class PreparedHuiyanResearchDispatch:
    provider_id: str
    capabilities: ProviderSubmissionSafety
    repository: ResearchOrchestrationRepository
    executor: OpenAIResponsesProviderAdapter
    adapter: str = _OPENAI_COMPATIBLE_ADAPTER

    def submit(self, request: DispatchSubmissionRequest) -> DispatchSubmissionCompleted:
        frozen_request = frozen_request_for_dispatch(
            request,
            provider_id=self.provider_id,
            repository=self.repository,
        )
        retrieval: _KeyLiteratureRetrievalEvidence | None = None
        if _requires_key_literature_retrieval(frozen_request):
            retrieval = _execute_key_literature_retrieval(
                request, frozen_request, repository=self.repository
            )
            result_content = _key_literature_seed_content(frozen_request, retrieval)
            _logger.info(
                "dispatch_provider key_literature_fastpath_ok taskId=%s workUnitId=%s "
                "capabilityCallId=%s provider=%s contentChars=%s",
                request.task_id,
                request.work_unit_id,
                request.capability_call_id,
                self.provider_id,
                len(result_content),
            )
            return DispatchSubmissionCompleted(
                title="Key literature retrieval seeds",
                mime_type="application/json",
                content=result_content,
                provider_id=self.provider_id,
                adapter=_KEY_LITERATURE_RETRIEVAL_ADAPTER,
                usage=None,
                external_action_evidence=retrieval.external_action_evidence,
                model=None,
            )
        _logger.info(
            "dispatch_provider execute_start taskId=%s workUnitId=%s capabilityCallId=%s "
            "attempt=%s provider=%s adapter=%s modelProfile=%s artifactContract=%s agentId=%s",
            request.task_id,
            request.work_unit_id,
            request.capability_call_id,
            request.attempt_number,
            self.provider_id,
            _OPENAI_COMPATIBLE_ADAPTER,
            frozen_request.modelProfile,
            frozen_request.artifactContract,
            frozen_request.selectedAgentId,
        )
        def _execute_once():
            try:
                return self.executor.execute(
                    frozen_request,
                    capability_call_id=request.capability_call_id,
                    attempt_number=request.attempt_number,
                )
            except ProviderTransportNotSubmittedError as exc:
                _logger.warning(
                    "dispatch_provider transport_not_submitted taskId=%s workUnitId=%s capabilityCallId=%s "
                    "provider=%s artifactContract=%s error=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                    frozen_request.artifactContract,
                    exc,
                )
                raise DispatchProviderKnownRejected("provider_transport_not_submitted") from exc
            except ProviderTransportUncertainError as exc:
                _logger.warning(
                    "dispatch_provider transport_uncertain taskId=%s workUnitId=%s capabilityCallId=%s "
                    "provider=%s artifactContract=%s error=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                    frozen_request.artifactContract,
                    exc,
                )
                raise DispatchProviderResultUnknown(
                    "provider_result_unknown",
                    remote_run_id=getattr(exc, "provider_execution_id", None),
                ) from exc
            except ProviderRejectedRequestError as exc:
                _logger.warning(
                    "dispatch_provider request_rejected taskId=%s workUnitId=%s capabilityCallId=%s "
                    "provider=%s artifactContract=%s error=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                    frozen_request.artifactContract,
                    exc,
                )
                raise DispatchProviderKnownRejected("provider_request_rejected") from exc
            except ProviderContractError as exc:
                code = str(getattr(exc, "safe_error_code", "") or "provider_response_contract_invalid")
                field_paths = tuple(getattr(exc, "safe_field_paths", ()) or ())
                usage = getattr(exc, "usage", None)
                _logger.warning(
                    "dispatch_provider contract_rejected taskId=%s workUnitId=%s capabilityCallId=%s "
                    "provider=%s artifactContract=%s error=%s fields=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                    frozen_request.artifactContract,
                    code,
                    list(field_paths)[:16],
                )
                raise DispatchProviderKnownRejected(
                    code,
                    usage=usage if isinstance(usage, Mapping) else None,
                ) from exc
            except ProviderAttemptEvidenceUnavailableError as exc:
                _logger.warning(
                    "dispatch_provider attempt_evidence_unavailable taskId=%s workUnitId=%s "
                    "capabilityCallId=%s provider=%s artifactContract=%s error=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                    frozen_request.artifactContract,
                    exc,
                )
                raise DispatchProviderKnownRejected("provider_attempt_evidence") from exc
            except ProviderExecutionError as exc:
                code = str(
                    getattr(exc, "safe_error_code", "")
                    or "provider_execution_invalid"
                )
                _logger.warning(
                    "dispatch_provider execution_invalid taskId=%s workUnitId=%s capabilityCallId=%s "
                    "provider=%s artifactContract=%s code=%s error=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                    frozen_request.artifactContract,
                    code,
                    exc,
                )
                raise DispatchProviderKnownRejected(code) from exc
            except Exception:
                # Provider adapters own transport ambiguity: timeouts/response-loss
                # must arrive as ProviderTransportUncertainError. An untyped
                # exception here is therefore an internal post-response/runtime
                # failure and must never become external_result_unknown.
                _logger.exception(
                    "dispatch_provider internal_execution_invalid taskId=%s workUnitId=%s "
                    "capabilityCallId=%s provider=%s artifactContract=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                    frozen_request.artifactContract,
                )
                raise DispatchProviderKnownRejected("provider_execution_invalid") from None

        result = _execute_once()
        if result.skillRequest is not None:
            _logger.warning(
                "dispatch_provider skill_request_unsupported taskId=%s workUnitId=%s "
                "capabilityCallId=%s provider=%s",
                request.task_id,
                request.work_unit_id,
                request.capability_call_id,
                self.provider_id,
            )
            raise DispatchProviderKnownRejected("provider_skill_request_unsupported")
        if result.title is None or result.mimeType is None or result.content is None:
            _logger.warning(
                "dispatch_provider incomplete_result taskId=%s workUnitId=%s capabilityCallId=%s "
                "provider=%s hasTitle=%s hasMime=%s hasContent=%s",
                request.task_id,
                request.work_unit_id,
                request.capability_call_id,
                self.provider_id,
                result.title is not None,
                result.mimeType is not None,
                result.content is not None,
            )
            raise DispatchProviderKnownRejected("provider_response_contract_invalid")
        result_content = result.content
        if frozen_request.artifactContract == "independent_result_review_v1":
            review = json.loads(result.content)
            if review.get("verdict") == "external_result_unknown":
                _logger.warning(
                    "dispatch_provider review_external_unknown taskId=%s workUnitId=%s "
                    "capabilityCallId=%s provider=%s",
                    request.task_id,
                    request.work_unit_id,
                    request.capability_call_id,
                    self.provider_id,
                )
                raise DispatchProviderResultUnknown("provider_review_external_result_unknown")
        _logger.info(
            "dispatch_provider execute_ok taskId=%s workUnitId=%s capabilityCallId=%s "
            "provider=%s artifactContract=%s contentChars=%s",
            request.task_id,
            request.work_unit_id,
            request.capability_call_id,
            self.provider_id,
            frozen_request.artifactContract,
            len(result_content),
        )
        return DispatchSubmissionCompleted(
            title=result.title,
            mime_type=result.mimeType,
            content=result_content,
            provider_id=self.provider_id,
            adapter=str(result.metadata["adapter"]),
            usage=dict(result.usage) if result.usage else None,
            external_action_evidence=(
                retrieval.external_action_evidence if retrieval is not None else ()
            ),
            model=str(getattr(frozen_request, "modelProfile", None) or DISPATCH_MODEL_PROFILE or "") or None,
        )

    def query_by_idempotency_key(self, _idempotency_key: str) -> DispatchSubmissionQueryResult:
        raise DispatchProviderResultUnknown("provider_submission_query_unsupported")


class HuiyanResearchDispatchProvider:
    def __init__(
        self,
        *,
        provider_id: str,
        repository: ResearchOrchestrationRepository | None = None,
        registry: ProviderRegistry | None = None,
        executor: OpenAIResponsesProviderAdapter | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._repository = repository or ResearchOrchestrationRepository()
        self._registry = registry or load_provider_registry()
        self._executor = executor or OpenAIResponsesProviderAdapter(
            transport=HttpOpenAIResponsesTransport(),
            registry=self._registry,
        )

    def preflight(self) -> PreparedHuiyanResearchDispatch:
        try:
            definition = self._registry.require(self._provider_id)
            if definition.adapter != _OPENAI_COMPATIBLE_ADAPTER:
                raise ValueError("unsupported adapter")
            resolve_provider_runtime_config(
                self._provider_id,
                model_profile=DISPATCH_MODEL_PROFILE,
                registry=self._registry,
            )
        except Exception as exc:
            raise DispatchProviderPermanentPreflightError("provider_configuration_unavailable") from exc
        return PreparedHuiyanResearchDispatch(
            provider_id=self._provider_id,
            capabilities=definition.submissionSafety,
            repository=self._repository,
            executor=self._executor,
        )


def frozen_request_for_dispatch(
    request: DispatchSubmissionRequest,
    *,
    provider_id: str,
    repository: ResearchOrchestrationRepository,
) -> FrozenOrchestrationExecutionRequest:
    task = repository.load(
        task_id=request.task_id,
        owner_id=request.owner_id,
        workspace_id=request.workspace_id,
    )
    work_unit = next(
        (row for row in task.work_units if row.work_unit_id == request.work_unit_id),
        None,
    )
    if work_unit is None or work_unit.model_dump(mode="json", by_alias=True) != dict(request.payload):
        raise DispatchProviderKnownRejected("dispatch_work_unit_snapshot_mismatch")
    decision = next(
        (
            row for row in task.execution_decisions
            if row.execution_decision_id == request.execution_decision_id
        ),
        None,
    )
    assignment = None if decision is None else next(
        (row for row in decision.assignments if row.assignment_id == work_unit.assignment_id),
        None,
    )
    if assignment is None or assignment.executor_ref != work_unit.executor_ref:
        raise DispatchProviderKnownRejected("dispatch_assignment_snapshot_mismatch")
    snapshot = repository.load_registry_snapshot(work_unit.registry_snapshot_ref.content_hash)
    agent = _one_agent(snapshot, work_unit.executor_ref.executor_id, work_unit.executor_ref.definition_version)
    if provider_id not in agent.get("allowedProviders", []):
        raise DispatchProviderKnownRejected("dispatch_provider_not_allowed")
    contract_container = agent.get("reviewContract") if assignment.role.value == "reviewer" else agent.get("outputContract")
    frozen = contract_container.get("frozenArtifactContract") if isinstance(contract_container, Mapping) else None
    if not isinstance(frozen, Mapping):
        raise DispatchProviderKnownRejected("frozen_artifact_contract_missing")
    artifact_kind = str((agent.get("outputContract") or {}).get("artifactKind") or "").strip()
    if (
        agent.get("agentKind") == "general_research"
        and decision.purpose.value == "completion"
        and assignment.completion_coverage.value == "completion_capable"
        and len(work_unit.expected_artifact_types) == 1
    ):
        artifact_kind = work_unit.expected_artifact_types[0]
    if assignment.role.value != "reviewer" and not artifact_kind:
        raise DispatchProviderKnownRejected("frozen_artifact_kind_missing")
    schema = frozen.get("artifactSchema")
    required_fields = schema.get("required") if isinstance(schema, Mapping) else None
    if not isinstance(required_fields, list) or not required_fields:
        raise DispatchProviderKnownRejected("frozen_artifact_contract_invalid")
    selected_skills = tuple(row.skill_id for row in work_unit.allowed_skill_refs)
    allow_skill_requests = _should_allow_skill_requests_for_durable_adapter(selected_skills)
    plan = {
        "selectedAgentId": work_unit.executor_ref.executor_id,
        "selectedSkillIds": list(selected_skills),
        "assignmentRole": assignment.role.value,
        **({"artifactKind": artifact_kind} if assignment.role.value != "reviewer" else {}),
        "selectedProvider": provider_id,
        "modelProfile": DISPATCH_MODEL_PROFILE,
        "providerPolicy": {"allowedProviders": list(agent["allowedProviders"])},
        "artifactContractSchemaVersion": int(snapshot["schemaVersion"]),
        "artifactRequiredFields": list(required_fields),
        **dict(frozen),
    }
    input_snapshot: dict[str, Any] = {
        "instruction": work_unit.instruction,
        "inputArtifactRefs": list(work_unit.input_artifact_refs),
        "inputMemoryRefs": list(work_unit.input_memory_refs),
        "researchDesignVersion": work_unit.research_design_version,
        "substageInstanceId": work_unit.substage_instance_id,
    }
    resolved_input_memory = _resolved_input_memory(task, work_unit)
    if resolved_input_memory:
        input_snapshot["resolvedInputMemory"] = resolved_input_memory
    if assignment.role.value != "reviewer" and work_unit.input_artifact_refs:
        try:
            input_snapshot["inputArtifactMaterials"] = repository.read_agent_input_materials_for_dispatch(
                task_id=request.task_id,
                owner_id=request.owner_id,
                workspace_id=request.workspace_id,
                execution_decision_id=request.execution_decision_id,
                work_unit_id=request.work_unit_id,
            )
        except ResearchOrchestrationPersistenceError as exc:
            raise DispatchProviderKnownRejected("input_artifact_materials_unavailable") from exc
    if assignment.role.value == "reviewer":
        try:
            input_snapshot["reviewMaterials"] = repository.read_review_materials_for_dispatch(
                task_id=request.task_id,
                owner_id=request.owner_id,
                workspace_id=request.workspace_id,
                execution_decision_id=request.execution_decision_id,
                reviewer_work_unit_id=request.work_unit_id,
            )
        except ResearchOrchestrationPersistenceError as exc:
            raise DispatchProviderKnownRejected("review_materials_unavailable") from exc
    return FrozenOrchestrationExecutionRequest(
        runId=request.dispatch_id,
        taskId=request.task_id,
        ownerId=request.owner_id,
        workspaceId=request.workspace_id,
        orchestrationPlan=plan,
        frozenInputSnapshot=input_snapshot,
        selectedAgentId=work_unit.executor_ref.executor_id,
        selectedSkillIds=selected_skills,
        logicalProviderId=provider_id,
        modelProfile=DISPATCH_MODEL_PROFILE,
        artifactContract=str(frozen["artifactContract"]),
        artifactContractSchemaVersion=int(snapshot["schemaVersion"]),
        artifactRequiredFields=tuple(required_fields),
        artifactSemanticVersion=str(frozen["artifactSemanticVersion"]),
        artifactSchemaDigest=str(frozen["artifactSchemaDigest"]),
        artifactSchema=schema,
        artifactSchemaRef=str(frozen["artifactSchemaRef"]),
        allowSkillRequests=allow_skill_requests,
    )


def _should_allow_skill_requests_for_durable_adapter(selected_skills: tuple[str, ...]) -> bool:
    """Permit skillRequest returns only when a selected skill has a durable
    python_adapter runtime. The frozen-dispatch default is strategy_only
    (allowSkillRequests=False) because most contracts expect a final JSON
    artifact, not a tool invocation. Skills registered as durable Python
    adapters (materials-mp-constraint-search, materials-magnet-design-mvp,
    crystal-design-formula-crossover-py, academic-source-retrieval-skill,
    etc.) are explicit opt-ins: their LLM is allowed to emit a skillRequest
    so the orchestrator can dispatch the adapter out-of-band.
    """
    if not selected_skills:
        return False
    try:
        from app.execution.run_single_structured_skill import PYTHON_ADAPTER_SKILLS
    except Exception:
        return False
    return any(skill_id in PYTHON_ADAPTER_SKILLS for skill_id in selected_skills)


def _task_goal(task: Any) -> str | None:
    """Resolve the durable user goal without depending on storage/model internals."""
    payload: Mapping[str, Any] | None = None
    model_dump = getattr(task, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", by_alias=True)
        if isinstance(dumped, Mapping):
            payload = dumped
    elif isinstance(task, Mapping):
        payload = task

    if payload is None:
        return None
    direct = payload.get("goalSummary")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    active_ref = payload.get("activeResearchDesignRef")
    versions = payload.get("researchDesignVersions")
    if not isinstance(versions, list):
        return None
    candidates = [row for row in versions if isinstance(row, Mapping)]
    if isinstance(active_ref, Mapping):
        design_id = active_ref.get("researchDesignId")
        version = active_ref.get("version")
        candidates = [
            row for row in candidates
            if row.get("researchDesignId") == design_id and row.get("version") == version
        ] or candidates
    else:
        confirmed = [row for row in candidates if row.get("status") == "confirmed"]
        if confirmed:
            candidates = sorted(confirmed, key=lambda row: int(row.get("version") or 0), reverse=True)
    for row in candidates:
        goal = row.get("goal")
        if isinstance(goal, str) and goal.strip():
            return goal.strip()
    return None


def _resolved_input_memory(task: Any, work_unit: Any) -> dict[str, str]:
    """Materialize API-authoritative semantic aliases used by frozen Definitions.

    WorkUnit inputMemoryRefs are durable lineage identifiers. The provider must
    receive the immutable value behind well-known semantic refs as well as the
    identifier; otherwise an Agent sees only strings such as ``user_goal`` and
    can incorrectly conclude that the research topic is missing.
    """
    refs = {
        str(value).strip()
        for value in getattr(work_unit, "input_memory_refs", ())
        if str(value).strip()
    }
    resolved: dict[str, str] = {}
    if "user_goal" in refs:
        goal = _task_goal(task)
        if goal:
            resolved["user_goal"] = goal

    instruction = str(getattr(work_unit, "instruction", "") or "").strip()
    if instruction:
        # These aliases are Definition-owned semantic targets, not external
        # memory records. The immutable WorkUnit objective is their value.
        for ref in ("search_target", "validation_target"):
            if ref in refs:
                resolved[ref] = instruction
    return resolved


def validate_completed_dispatch_result(
    request: DispatchSubmissionRequest,
    result: DispatchSubmissionCompleted,
    *,
    repository: ResearchOrchestrationRepository,
) -> FrozenOrchestrationExecutionRequest:
    frozen = frozen_request_for_dispatch(
        request,
        provider_id=result.provider_id,
        repository=repository,
    )
    validate_frozen_artifact_content(
        FrozenArtifactContract(
            contract_id=frozen.artifactContract,
            semantic_version=frozen.artifactSemanticVersion,
            schema_digest=frozen.artifactSchemaDigest,
            schema=frozen.artifactSchema,
            source_ref=frozen.artifactSchemaRef,
        ),
        result.content,
    )
    return frozen


def _one_agent(snapshot: Mapping[str, Any], agent_id: str, version: int | None) -> Mapping[str, Any]:
    definitions = snapshot.get("definitions")
    agents = definitions.get("agents") if isinstance(definitions, Mapping) else None
    matches = [
        row for row in agents or []
        if isinstance(row, Mapping)
        and row.get("agentId") == agent_id
        and row.get("definitionVersion") == version
        and row.get("enabled") is True
    ]
    if len(matches) != 1:
        raise DispatchProviderKnownRejected("dispatch_agent_snapshot_invalid")
    return matches[0]


_frozen_request = frozen_request_for_dispatch


__all__ = [
    "DISPATCH_MODEL_PROFILE",
    "HuiyanResearchDispatchProvider",
    "frozen_request_for_dispatch",
    "validate_completed_dispatch_result",
]
