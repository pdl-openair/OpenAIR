"""Preserve scientific payloads and report existing required-key gaps."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

import jsonschema

ContractRole = Literal["input", "output"]


_SECRET_LABEL = re.compile(
    r"\b(?:api[_ -]?key|authorization|bearer|access[_ -]?token|password)\b",
    re.I,
)
_JSON_FENCE = re.compile(
    r"\A\s*```(?:json|markdown|md|text|yaml|yml)?\s*(?P<body>[\s\S]*?)\s*```\s*\Z",
    re.I,
)
_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_READABLE_KEYS = (
    "content", "markdown", "text", "body", "findings", "evidence", "notes",
    "summary", "description", "rationale", "query",
)


def normalize_semantic_input(
    instance: Any,
    schema: Mapping[str, Any] | None,
    *,
    label: str = "Skill input",
) -> tuple[Any, list[str]]:
    return apply_semantic_contract(instance, schema, label=label, role="input")


def normalize_semantic_output(
    raw: Any,
    *,
    fallback_title: str,
    mime_type: str,
    max_output_chars: int,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    return materialize_semantic_output(
        raw,
        fallback_title=fallback_title,
        mime_type=mime_type,
        max_output_chars=max_output_chars,
        secret_values=secret_values,
    )


def strip_semantic_wrappers(value: str) -> str:
    """Strip outer fences and template HTML comments; keep inner scientific markup."""
    text = str(value or "").strip()
    fenced = _JSON_FENCE.fullmatch(text)
    if fenced is not None:
        text = fenced.group("body").strip()
    return _HTML_COMMENT.sub("", text).strip()


def apply_semantic_contract(
    instance: Any,
    schema: Mapping[str, Any] | None,
    *,
    label: str = "payload",
    role: ContractRole = "input",
    schema_validator: Callable[[Any], None] | None = None,
) -> tuple[Any, list[str]]:
    """Keep payload values intact and report existing required-key errors."""
    warnings: list[str] = []
    schema_map = dict(schema or {"type": "object"})
    normalized = instance
    validate_payload = (
        schema_validator
        if schema_validator is not None
        else jsonschema.validators.validator_for(schema_map)(schema_map).validate
    )
    if role == "input":
        from app.execution.scientific_input_fidelity import validate_scientific_input

        return validate_scientific_input(
            normalized, schema_map, label=label, warnings=warnings,
            validate_payload=validate_payload,
            collect_errors=schema_validator is None,
        )
    try:
        validate_payload(normalized)
        return normalized, warnings
    except jsonschema.ValidationError as exc:
        owner = getattr(validate_payload, "__self__", None)
        iterator = getattr(getattr(owner, "validator", owner), "iter_errors", None)
        errors = iterator(normalized) if callable(iterator) else [exc]
        warnings.extend(
            f"output_contract_gap:{error.json_path}:{error.validator}"
            for error in errors if error.validator == "required"
        )
        return normalized, warnings


def materialize_semantic_output(
    raw: Any,
    *,
    fallback_title: str,
    mime_type: str,
    max_output_chars: int,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Turn one provider content value into a durable title/content artifact."""
    if str(mime_type or "").strip() == "application/json":
        return _materialize_json_output(
            raw,
            fallback_title=fallback_title,
            max_output_chars=max_output_chars,
            secret_values=secret_values,
        )
    warnings: list[str] = []
    mime = str(mime_type or "text/plain").strip() or "text/plain"
    title_fallback = str(fallback_title or "result").strip() or "result"
    text = raw if isinstance(raw, str) else ""
    document: Any = None
    if isinstance(raw, Mapping):
        document = dict(raw)
    elif isinstance(raw, str):
        candidate = strip_semantic_wrappers(raw)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, Mapping):
            document = dict(parsed)
        elif isinstance(parsed, str) and parsed.strip():
            text = parsed
        else:
            text = candidate
    if document is not None:
        title, content, extra_warnings = _document_to_title_content(document, title_fallback)
        warnings.extend(extra_warnings)
    else:
        title = title_fallback
        content = strip_semantic_wrappers(text)
    content = _redact_secret_values(content, secret_values)
    title = _redact_secret_values(title, secret_values)
    if _SECRET_LABEL.search(f"{title}\n{content}"):
        content = _SECRET_LABEL.sub("[redacted]", content)
        title = _SECRET_LABEL.sub("[redacted]", title)
        warnings.append("secret_label_redacted")
    if mime == "text/yaml":
        yaml_text, yaml_warnings = _coerce_yaml_semantic_content(content, document)
        warnings.extend(yaml_warnings)
        content = yaml_text
    if len(content) > max_output_chars:
        content = content[:max_output_chars].rstrip()
        warnings.append("content_truncated_to_max_output_chars")
    title = title.strip() or title_fallback
    if len(title) > 240:
        title = title[:240].rstrip()
    title = "".join(ch if ord(ch) >= 32 else " " for ch in title).strip() or title_fallback
    return {"title": title, "content": content, "warnings": warnings}


def _materialize_json_output(
    raw: Any,
    *,
    fallback_title: str,
    max_output_chars: int,
    secret_values: Sequence[str],
) -> dict[str, Any]:
    """Preserve JSON material without asserting its machine contract is valid."""
    warnings: list[str] = []
    title = str(fallback_title or "result").strip() or "result"
    content = json.dumps(dict(raw), ensure_ascii=False) if isinstance(raw, Mapping) else raw
    content = content if isinstance(content, str) else ""
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        # Invalid content is evidence to retain, never an object to invent.
        document = None
    if isinstance(document, Mapping):
        provided_title = document.get("title")
        if isinstance(provided_title, str) and provided_title.strip():
            title = provided_title.strip()
    content = _redact_secret_values(content, secret_values)
    title = _redact_secret_values(title, secret_values)
    if _SECRET_LABEL.search(f"{title}\n{content}"):
        content = _SECRET_LABEL.sub("[redacted]", content)
        title = _SECRET_LABEL.sub("[redacted]", title)
        warnings.append("secret_label_redacted")
    if len(content) > max_output_chars:
        warnings.append("output_contract_gap:$:max_output_chars")
    title = "".join(ch if ord(ch) >= 32 else " " for ch in title).strip()
    return {"title": title[:240], "content": content, "warnings": warnings}


def _document_to_title_content(document: Mapping[str, Any], fallback_title: str) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    title = document.get("title")
    content = document.get("content")
    if isinstance(content, str) and content.strip():
        if title is None or not str(title).strip():
            warnings.append("fallback_title")
        resolved_title = str(title).strip() if isinstance(title, str) and title.strip() else fallback_title
        extras = sorted(str(key) for key in document if key not in {"title", "content"})
        if extras:
            warnings.append("ignored_extra_semantic_fields")
        return resolved_title, strip_semantic_wrappers(content), warnings
    chunks: list[str] = []
    heading = str(title).strip() if isinstance(title, str) and title.strip() else ""
    if heading:
        chunks.append(f"# {heading}")
    for key in _READABLE_KEYS:
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(f"## {_pretty_key(key)}\n\n{value.strip()}")
        elif isinstance(value, list) and value:
            bullets = [str(item).strip() for item in value if str(item).strip()]
            if bullets:
                chunks.append("## " + _pretty_key(key) + "\n\n" + "\n".join(f"- {item}" for item in bullets))
    if not chunks:
        chunks.append("```json\n" + json.dumps(dict(document), ensure_ascii=False, indent=2) + "\n```")
        warnings.append("semantic_object_json_fallback")
    else:
        extras = sorted(str(key) for key in document if key not in {"title", *_READABLE_KEYS})
        if extras:
            warnings.append("ignored_extra_semantic_fields")
    resolved_title = heading or fallback_title
    if not heading:
        warnings.append("fallback_title")
    return resolved_title, "\n\n".join(chunks).strip(), warnings


def _coerce_yaml_semantic_content(content: str, document: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    import yaml

    warnings: list[str] = []
    parsed: Any = None
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError:
        parsed = None
    if isinstance(document, Mapping) and _looks_like_research_config(document):
        dumped = yaml.safe_dump(
            _project_research_config(document),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).strip()
        warnings.append("normalized_yaml_research_config")
        return dumped, warnings
    if isinstance(parsed, Mapping) and _looks_like_research_config(parsed):
        dumped = yaml.safe_dump(
            _project_research_config(parsed),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).strip()
        return dumped, warnings
    return content, warnings


def _looks_like_research_config(value: Mapping[str, Any]) -> bool:
    if isinstance(value.get("content"), str) and str(value.get("content")).strip() and "title" in value:
        return False
    if str(value.get("schema") or "").strip() == "sdr-driven":
        return True
    if str(value.get("research_type") or "") in {"M", "D", "C", "H"}:
        return True
    keys = set(value)
    return "project" in keys and bool(
        keys & {"minimum_success_criteria", "ai_mode", "target_venue", "checkpoint_stages"}
    )


def _project_research_config(value: Mapping[str, Any]) -> dict[str, Any]:
    research_type = value.get("research_type")
    if research_type not in {"M", "D", "C", "H"}:
        research_type = "D"
    ai_mode = value.get("ai_mode")
    if ai_mode not in {"copilot", "autopilot"}:
        ai_mode = "copilot"
    target_venue = value.get("target_venue") if isinstance(value.get("target_venue"), Mapping) else {}
    resources = value.get("resources") if isinstance(value.get("resources"), Mapping) else {}
    criteria = value.get("minimum_success_criteria") if isinstance(value.get("minimum_success_criteria"), Mapping) else {}
    modification_log = value.get("modification_log") if isinstance(value.get("modification_log"), list) else []
    return {
        "project": str(value.get("project") or "").strip() or "Untitled research",
        "schema": "sdr-driven",
        "research_type": research_type,
        "ai_mode": ai_mode,
        "domain": str(value.get("domain") or "").strip() or "unspecified",
        "subdomain": str(value.get("subdomain") or ""),
        "target_venue": {
            "primary": str(target_venue.get("primary") or ""),
            "tier": str(target_venue.get("tier") or ""),
        },
        "resources": {
            "compute": str(resources.get("compute") or ""),
            "data_status": str(resources.get("data_status") or ""),
        },
        "checkpoint_stages": [str(item) for item in value.get("checkpoint_stages") or [] if isinstance(item, str)],
        "minimum_success_criteria": {
            "min_baselines": int(criteria.get("min_baselines") or 0) if _is_nonneg(criteria.get("min_baselines")) else 0,
            "min_ablation_runs": int(criteria.get("min_ablation_runs") or 0) if _is_nonneg(criteria.get("min_ablation_runs")) else 0,
            "require_proposed": bool(criteria.get("require_proposed")) if isinstance(criteria.get("require_proposed"), bool) else False,
            "required_metrics": [str(item) for item in criteria.get("required_metrics") or [] if isinstance(item, str)],
        },
        "modification_log": [
            entry for entry in modification_log
            if isinstance(entry, Mapping)
        ],
    }


def _redact_secret_values(text: str, secret_values: Sequence[str]) -> str:
    redacted = text
    for value in secret_values:
        token = str(value or "").strip()
        if len(token) < 16:
            continue
        if token in redacted:
            redacted = redacted.replace(token, "[redacted]")
    return redacted


def _pretty_key(value: str) -> str:
    return re.sub(r"[_-]+", " ", value).strip().title() or "Content"


def _is_nonneg(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_action_input(definition: Mapping[str, Any], parameters: Mapping[str, Any]) -> None:
    """Validate only parameters about to cross a real action boundary."""
    source = definition.get("sourceContract")
    frozen = source.get("frozenSchemas") if isinstance(source, Mapping) else None
    entry = frozen.get("input") if isinstance(frozen, Mapping) else None
    schema = entry.get("schema") if isinstance(entry, Mapping) else None
    digest = entry.get("sha256") if isinstance(entry, Mapping) else None
    if not isinstance(schema, Mapping) or not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("input_contract_skill_schema_missing")
    try:
        validator = jsonschema.validators.validator_for(dict(schema))
        validator.check_schema(dict(schema))
    except jsonschema.SchemaError as exc:
        raise ValueError("input_contract_schema_invalid") from exc
    try:
        validator(dict(schema)).validate(dict(parameters))
    except jsonschema.ValidationError as exc:
        raise ValueError("input_contract_invalid") from exc


def safe_skill_failure_detail(result: Mapping[str, Any]) -> dict[str, str] | None:
    """Keep only bounded runtime diagnostics; never retain the provider body."""
    detail = {
        key: value
        for key in ("failedStage", "firstMismatch")
        if isinstance((value := result.get(key)), str)
        and value
        and len(value) <= 160
        and value.isascii()
        and all(character.isalnum() or character in "._:-/" for character in value)
    }
    return detail or None


class ResearchOutputContractGap(ValueError):
    """A known response contains material that lacks an accepted output contract."""

    def __init__(self, reason: str, *, stage: str = "json_parse_failed") -> None:
        super().__init__(reason)
        self.result_protocol = {
            "status": "degraded",
            "quarantined_items": [{"path": "$", "reason": stage}],
            "warnings": [f"research_output_contract_gap:{stage}:{reason}"],
        }


def provider_content_for_preservation(body: Any) -> str:
    """Read one received body without constructing or repairing research facts."""
    try:
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def finalizer_failure_preserves_draft(code: str) -> bool:
    """Separate failed local postprocessing from frozen code/path identity faults."""
    return code in {
        "finalizer_execution_failed", "finalizer_execution_timeout",
        "finalizer_result_invalid", "finalizer_rejected",
    }


def required_error_affects_runtime(error: Any, scientific_fields: frozenset[str]) -> bool:
    """Keep required execution fields; report scientific gaps to their consumer."""
    if error.absolute_path:
        return error.absolute_path[0] not in scientific_fields
    return any(
        field not in scientific_fields
        for field in error.validator_value
        if field not in error.instance
    )


def validate_composed_input_schema(
    *,
    identity: Any,
    document: Mapping[str, Any],
    schema: Mapping[str, Any],
    failure_code: str,
    unavailable_source_fields: frozenset[str] = frozenset(),
) -> None:
    from app.execution.research_input_contract_gate import _failure

    try:
        validator_class = jsonschema.validators.validator_for(dict(schema))
        validator_class.check_schema(dict(schema))
    except jsonschema.SchemaError as exc:
        raise _failure(identity, "input_contract_schema_invalid") from exc

    errors = sorted(
        (error for error in validator_class(dict(schema)).iter_errors(dict(document))
         if error.validator == "required"
         and required_error_affects_runtime(error, unavailable_source_fields)),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    paths = tuple(
        "/" + "/".join(str(part) for part in error.absolute_path)
        for error in errors[:16]
    )
    raise _failure(identity, failure_code, paths)
