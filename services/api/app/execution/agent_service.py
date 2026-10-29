"""Orchestrate workbench agent chat and skill execute (live / replay)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from app.config import settings
from app.execution.academic_retrieval_query import build_academic_retrieval_payload
from app.modules.skill_binding_source import SkillBindingSourceError, skill_ids_with_binding_inputs
from app.modules.repo_paths import repo_root
from app.modules.system_agents import (
    build_presentation_agent_narrative,
    build_summary_agent_narrative,
)


class SessionUnavailableError(RuntimeError):
    """执行后端未回填 session_id（已知兼容情况，例如 GLM 端点不返回 session_id）。

    这不是错误：live 模式下应静默回退到 replay，而非记为 Claude 调用失败。
    """


def _load_skill_meta(skill_id: str) -> dict[str, Any]:
    """Read skill.yaml for a registered skill. Returns empty dict on failure."""
    yaml_path = repo_root() / "skills" / "registry.yaml"
    if not yaml_path.exists():
        return {}
    try:
        import yaml

        with open(yaml_path, encoding="utf-8") as f:
            registry = yaml.safe_load(f) or {}
    except Exception:
        return {}
    # Accept both underscore (skill_id) and hyphen (claude_name) formats
    key = skill_id.replace("-", "_")
    for entry in registry.get("skills", []):
        if entry.get("skill_id", "").replace("-", "_") == key:
            skill_rel = entry.get("skill_path", "")
            sy_path = repo_root() / skill_rel / "skill.yaml"
            if sy_path.exists():
                try:
                    import yaml as _yaml

                    with open(sy_path, encoding="utf-8") as f:
                        return _yaml.safe_load(f) or {}
                except Exception:
                    pass
            break
    return {}


def _declared_live_runtime(skill_id: str, skill_meta: dict[str, Any]) -> str:
    """Return the runtime declared by the selected Skill, with an explicit live override."""
    if skill_id in _GENERIC_SKILL_IDS:
        return AGENT_RUNTIME_CLAUDE_CODE
    override = (os.environ.get("OPENAIR_LIVE_SKILL_RUNTIME") or "").strip().lower()
    if override in {"huiyan", HUIYAN_SKILL_RUNTIME}:
        return HUIYAN_SKILL_RUNTIME
    declared = str(skill_meta.get("runtime") or "claude_code").strip().lower()
    if declared in {"huiyan", HUIYAN_SKILL_RUNTIME}:
        return HUIYAN_SKILL_RUNTIME
    return AGENT_RUNTIME_CLAUDE_CODE


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    return default


def _skill_risk_level(skill_meta: dict[str, Any]) -> str:
    level = str(skill_meta.get("risk_level") or "low").strip().lower()
    return level if level in {"low", "medium", "high"} else "low"


def _skill_review_required(skill_meta: dict[str, Any]) -> bool:
    return _coerce_bool(skill_meta.get("review_required"), default=False)


def _skill_auto_allowed(skill_meta: dict[str, Any]) -> bool:
    if "auto_allowed" in skill_meta:
        return _coerce_bool(skill_meta.get("auto_allowed"), default=False)
    if "auto" in skill_meta:
        return _coerce_bool(skill_meta.get("auto"), default=False)
    return False


def _artifact_state_for_skill_output(skill_meta: dict[str, Any]) -> str:
    if _skill_review_required(skill_meta) or _skill_risk_level(skill_meta) in {"medium", "high"}:
        return "needs_review"
    return "needs_review"


def _maybe_auto_review_low_risk_artifact(
    task_id: str,
    artifact_id: str,
    skill_meta: dict[str, Any],
) -> None:
    if not _skill_review_required(skill_meta) or _skill_risk_level(skill_meta) != "low":
        return
    try:
        note = "Low-risk review_required output was automatically reviewed by the review agent."
        store.update_artifact_review_state(
            task_id,
            artifact_id,
            artifact_state="usable",
            reviewer="openair_review_agent",
            note=note,
            source="review_agent",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("low-risk artifact auto-review failed for %s: %s", artifact_id, exc)


def _skill_execution_block_reason(
    task: dict[str, Any],
    skill_meta: dict[str, Any],
    *,
    explicit_user_confirmed: bool,
) -> str | None:
    if _skill_risk_level(skill_meta) == "high":
        return "high_risk_requires_user_confirmation"
    if not explicit_user_confirmed and not _skill_auto_allowed(skill_meta):
        return "auto_not_allowed_requires_user_confirmation"
    return None


def _direct_skill_dependency_block(skill_id: str | None) -> str | None:
    """Return the fail-closed reason for legacy direct Skill execution.

    These routes do not materialize Binding v2 input authority. A
    binding-bearing Skill must therefore use durable orchestration rather
    than silently synthesizing or omitting its prerequisite input.
    """
    if settings.skill_exec_mode == "replay" or not skill_id:
        return None
    try:
        requires_binding = skill_id in skill_ids_with_binding_inputs()
    except SkillBindingSourceError:
        return "skill_binding_contract_invalid"
    return "skill_binding_precondition_missing" if requires_binding else None


def _skill_registered_packs(skill_id: str) -> list[str]:
    """Return the ``packs`` list a skill is registered under (registry.yaml).

    Empty list when the skill is unknown or has no packs field. Used by the
    execute endpoint to block cross-pack invocations (e.g. a materials task
    explicitly requesting sdr's ``constraints_criteria`` skill).
    """
    yaml_path = repo_root() / "skills" / "registry.yaml"
    if not yaml_path.exists():
        return []
    try:
        import yaml

        with open(yaml_path, encoding="utf-8") as f:
            registry = yaml.safe_load(f) or {}
    except Exception:
        return []
    key = skill_id.replace("-", "_")
    for entry in registry.get("skills", []):
        if entry.get("skill_id", "").replace("-", "_") == key:
            packs = entry.get("packs") or []
            return [p for p in packs if isinstance(p, str)] if isinstance(packs, list) else []
    return []


def _load_l2_skill_meta(pack: str, substage_key: str) -> dict[str, Any]:
    """Load L2 Skill metadata by canonical Substage; pack is naming context."""
    canonical = canonical_substage_key(substage_key)
    candidates = list(dict.fromkeys([
        f"{pack}_{canonical}",
        canonical,
        f"{pack}_{substage_key}",
        substage_key,
    ]))
    for candidate in candidates:
        meta = _load_skill_meta(candidate)
        if not meta:
            continue
        declared = {
            canonical_substage_key(value)
            for value in meta.get("substages") or []
            if str(value or "").strip()
        }
        if not declared or canonical in declared:
            return meta
    return {}


def _lookup_claude_name(skill_id: str) -> str:
    """Resolve skill_id to claude_name via registry. Falls back to kebab-case."""
    meta = _load_skill_meta(skill_id)
    if not meta and "-" in skill_id:
        meta = _load_skill_meta(skill_id.replace("-", "_"))
    return meta.get("claude_name") or skill_id.replace("_", "-")


def _skill_hash(skill_id: str) -> str | None:
    path = repo_root() / ".claude" / "skills" / _lookup_claude_name(skill_id) / "SKILL.md"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _l2_claude_name(pack: str, substage_key: str) -> str | None:
    """Resolve L2 Skill name from canonical Substage metadata."""
    meta = _load_l2_skill_meta(pack, substage_key)
    if meta.get("claude_name"):
        return meta["claude_name"]
    return substage_key.replace("_", "-")


def _resolve_input_call_refs(task: dict[str, Any], input_skill_ids: list[str]) -> list[str]:
    """Resolve input_skills to IDs of the most recent completed matching calls."""
    if not input_skill_ids:
        return []
    calls = task.get("capabilityCalls") or []
    resolved: list[str] = []
    for sid in input_skill_ids:
        claude_name = _lookup_claude_name(sid)
        underscore_name = sid.replace("-", "_")
        for c in reversed(calls):
            name = c.get("name", "")
            if name in (claude_name, underscore_name, sid) and c.get("status") == "completed":
                resolved.append(c["id"])
                break
    return resolved


def _validate_python_adapter_input(skill_path: str, input_payload: dict[str, Any]) -> None:
    """Mirror Skill Lab input-schema validation before production adapter dispatch."""
    schema_path = repo_root() / skill_path / "input.schema.json"
    if not schema_path.exists():
        raise RuntimeError(f"python adapter input schema not found: {schema_path}")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"python adapter input schema unreadable: {schema_path}") from exc
    try:
        from jsonschema import ValidationError, validate

        validate(instance=input_payload, schema=schema)
    except ValidationError as exc:
        raise RuntimeError(f"python adapter input invalid: {exc.message}") from exc


# _parse_l3plan / _parse_l3done_tags are pure text parsers kept here for convenience;
# they do not trigger runtime invocation.
from app.execution.claude_code_runner import (
    _L3DONE_RE,
    _L3PLAN_RE,
    _parse_l3plan,
    _parse_l3done_tags,
)
from app.execution.execution_context import get_task_workspace_dir
from app.execution.huiyan_skill_runner import RUNTIME as HUIYAN_SKILL_RUNTIME
from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import resolve_python_adapter_skill
from app.execution.replay_runner import replay_chat_reply, replay_skill
from app.execution.runtime_boundary import ExecutionRequest, get_execution_runtime
from app.modules import sdr_config
from app.modules import pack_config
from app.modules.task_constants import STAGE_SUBSTAGE_CATALOG
from app.modules.task_store import AGENT_RUNTIME_CLAUDE_CODE, store, build_stage_catalog
from app.modules.substage_identity import canonical_substage_key

CREATION_ALLOWED_SKILLS = {"workbench_chat", "new_task_planning", "new-task-planning"}

_GENERIC_SKILL_IDS = frozenset({"", "workbench_chat", "workbench-chat"})
_ARXIV_IDENTIFIER_RE = re.compile(r"\b(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.IGNORECASE)
_LITERATURE_QUERY_STOPWORDS = frozenset(
    {
        "arxiv", "http", "https", "doi", "et", "al", "the", "and", "for", "with", "from",
        "that", "this", "are", "was", "not", "use", "using", "will", "may", "can", "via",
    }
)

_USER_VISIBLE_INTERNAL_RE = re.compile(
    r"("
    r"research-config\.ya?ml|"
    r"\bconfig(?:uration)?\b|"
    r"workspace\s+(?:directory|path|scan|structure)|"
    r"工作区目录|目录结构|"
    r"配置文件|"
    r"file\s+not\s+found|"
    r"未找到.*(?:ya?ml|配置|文件)|"
    r"(?:ya?ml|配置|文件).*未找到|"
    r"\btrace\b|"
    r"\badapter\b|"
    r"\bskill[_ -]?id\b"
    r")",
    re.IGNORECASE,
)

_USER_VISIBLE_INTERNAL_STRONG_RE = re.compile(
    r"("
    r"research-config\.ya?ml|"
    r"workspace\s+(?:directory|path|scan|structure)|"
    r"工作区目录|目录结构|"
    r"file\s+not\s+found|"
    r"未找到\s*(?:ya?ml|配置|文件)|"
    r"(?:ya?ml|配置|文件).*未找到"
    r")",
    re.IGNORECASE,
)

_FENCED_JSON_BLOCK_RE = re.compile(r"```json\s*[\s\S]*?```", re.IGNORECASE)


def _strip_fenced_json_blocks(text: str) -> str:
    return _FENCED_JSON_BLOCK_RE.sub("", text)


_MISSING_INPUT_RE = re.compile(
    r"(缺少|未提供|没有提供|需要补充|请补充|未检测到|missing|required|needs?\s+input|provide)",
    re.IGNORECASE,
)

_INSUFFICIENT_EVIDENCE_RE = re.compile(
    r"(证据不足|无法可靠|不能可靠|无法判断|没有足够|未能可靠定位|no reliable|insufficient evidence|not enough evidence)",
    re.IGNORECASE,
)


def _contains_user_visible_internal_detail(text: str | None) -> bool:
    return bool(text and _USER_VISIBLE_INTERNAL_RE.search(_strip_fenced_json_blocks(text)))


def _contains_blocking_internal_detail(reply: str | None, *, has_artifact: bool) -> bool:
    if not reply:
        return False
    visible_text = _strip_fenced_json_blocks(reply)
    if has_artifact:
        return bool(_USER_VISIBLE_INTERNAL_STRONG_RE.search(visible_text))
    return bool(_USER_VISIBLE_INTERNAL_RE.search(visible_text))


def _safe_business_reply_for_blocked_detail(
    *,
    status: str,
    substage_label: str | None,
) -> str:
    label = substage_label or "当前节点"
    if status == "needs_input":
        return (
            f"当前「{label}」还缺少可定位的输入材料。"
            "你可以补充论文 PDF、标题、DOI、链接、摘要或摘录；也可以说明允许我先基于公开来源检索。"
        )
    if status == "insufficient_evidence":
        return (
            f"当前「{label}」没有形成足够可靠的证据。"
            "我不能把条件不足包装成已完成；建议补充来源范围、论文标识或更具体的检索边界后继续。"
        )
    return (
        f"当前「{label}」没有形成可用业务产物。"
        "我已停止把内部准备过程当作完成结果展示；请补充输入材料或重试公开检索。"
    )


def _classify_business_outcome(
    *,
    reply: str,
    error: str | None,
    created_artifact_ids: list[str],
    confirmation_requests: list[dict[str, Any]],
    substage: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return (status, safe_reply) for the user-visible business node.

    A capability process may exit cleanly without producing a usable research
    outcome. That must not complete the substage. Internal preparation details
    are translated to a business state before messages/calls are persisted.
    """
    if error:
        return "failed", reply

    if re.search(r"not\s+logged\s+in|please\s+run\s+/login|authentication\s+(?:failed|required)", reply, re.IGNORECASE):
        return "failed", "当前技能的运行身份不可用，未生成科研产物。请由管理员恢复运行身份后重试。"

    label = (substage or {}).get("label") or (substage or {}).get("subStage")
    if _contains_blocking_internal_detail(reply, has_artifact=bool(created_artifact_ids)):
        return "blocked_recoverable", _safe_business_reply_for_blocked_detail(
            status="blocked_recoverable",
            substage_label=label,
        )
    if created_artifact_ids:
        return "completed", reply
    if confirmation_requests:
        return "needs_input", reply
    if _INSUFFICIENT_EVIDENCE_RE.search(reply):
        return "insufficient_evidence", _safe_business_reply_for_blocked_detail(
            status="insufficient_evidence",
            substage_label=label,
        )
    if _MISSING_INPUT_RE.search(reply):
        return "needs_input", _safe_business_reply_for_blocked_detail(
            status="needs_input",
            substage_label=label,
        )
    return "completed", reply


def _resolve_effective_skill_id(skill_id: str | None, substage_key: str | None, *, pack: str = "sdr") -> str | None:
    """Map generic workbench chat to the active substage/L2 skill via registry lookup.

    Resolution order for substage-keyed lookups (registry.yaml historically uses the bare
    `substage_key` as skill_id, e.g. `literature_search`, not `{pack}_{substage_key}`):
      1. bare `substage_key`  → matches the common registry convention
      2. `{pack}_{substage_key}` → legacy fallback for entries using pack-prefixed ids
    Without step 1, the lookup misses and `_lookup_claude_name` falls back to kebab-case
    on the prefixed id, fabricating non-existent skill folders like `sdr-literature-search`.
    """
    normalized = (skill_id or "").strip()
    if normalized and normalized not in _GENERIC_SKILL_IDS:
        return _lookup_claude_name(normalized)
    if substage_key:
        return _l2_claude_name(pack, substage_key)
    return normalized or "workbench_chat"


def _to_registry_skill_id(skill_ref: str) -> str:
    return skill_ref.strip().replace("-", "_")

SKILL_SUBSTAGE_HINTS = sdr_config.load_skill_id_to_substage()

DEFAULT_SUBSTAGE_BY_STAGE = {
    stage: sdr_config.get_default_substage_for_stage(stage)
    for stage in ("problem_modeling", "solution_design", "execution_validation", "insight_generation")
}


def _get_substage_hints(pack: str) -> dict[str, str]:
    return pack_config.load_skill_id_to_substage(pack=pack)


def _get_default_substage_by_stage(pack: str) -> dict[str, str | None]:
    return {
        stage: pack_config.get_default_substage_for_stage(stage, pack=pack)
        for stage in ("problem_modeling", "solution_design", "execution_validation", "insight_generation")
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _task_mode(task: dict[str, Any]) -> str:
    status = task.get("status")
    if status == "draft":
        return "creation"
    if status in ("running", "awaiting_confirmation", "completed"):
        return "execution"
    return "idle"


def _catalog_entries(task: dict[str, Any], stage_key: str) -> list[dict[str, Any]]:
    for row in task.get("stageCatalog") or []:
        if row.get("stageKey") == stage_key:
            return list(row.get("allowedSubStages") or [])
    return list(STAGE_SUBSTAGE_CATALOG.get(stage_key, []))


def _resolve_substage(task: dict[str, Any], skill_id: str | None, message: str) -> tuple[str, str]:
    """Fallback substage resolution when no explicit substageKey is provided.

    Priority:
      1. skill_id hint (try kebab + underscore + as-is)
      2. direct match against stage catalog substage keys (L2 orchestrator self-match)
      3. first substage for current stage (with warning when skill_id was given)
    """
    pack = task.get("skillPack", "sdr") or "sdr"
    hints = _get_substage_hints(pack) if pack != "sdr" else SKILL_SUBSTAGE_HINTS
    defaults = _get_default_substage_by_stage(pack) if pack != "sdr" else DEFAULT_SUBSTAGE_BY_STAGE

    stage_key = task.get("currentStage") or "problem_modeling"
    entries = _catalog_entries(task, stage_key)
    by_key = {entry.get("key"): entry for entry in entries if entry.get("key")}

    # Cross-stage catalog: needed for L2 self-bind when the requested skill belongs to a
    # different stage than currentStage (e.g. user on insight_generation types Skill: variable-definition)
    cross_stage_by_key: dict[str, dict[str, Any]] = {}
    for row in task.get("stageCatalog") or []:
        for entry in row.get("allowedSubStages") or []:
            key = entry.get("key")
            if key and key not in cross_stage_by_key:
                cross_stage_by_key[key] = entry

    # Build candidate forms of the requested skill_id (kebab, underscore, as-is)
    candidates: list[str] = []
    if skill_id:
        normalized = skill_id.strip()
        if normalized:
            seen: set[str] = set()
            for form in (normalized, normalized.replace("-", "_"), normalized.replace("_", "-")):
                if form and form not in seen:
                    seen.add(form)
                    candidates.append(form)

    all_key = by_key | cross_stage_by_key

    # 1. Hint lookup (covers L2 skills registered in skill-id-to-substage)
    for candidate in candidates:
        preferred = hints.get(candidate)
        if preferred and preferred in all_key:
            entry = all_key[preferred]
            return preferred, entry.get("label") or preferred

    # 2. Direct match against substage keys (L2 orchestrator self-bind).
    # Search current stage first, then cross-stage catalog.
    for candidate in candidates:
        if candidate in by_key:
            entry = by_key[candidate]
            return candidate, entry.get("label") or candidate
    for candidate in candidates:
        if candidate in cross_stage_by_key:
            entry = cross_stage_by_key[candidate]
            return candidate, entry.get("label") or candidate

    if skill_id and skill_id not in _GENERIC_SKILL_IDS:
        logger.warning(
            "skill_id=%s not registered in pack=%s hints; falling back to default substage for stage=%s",
            skill_id, pack, stage_key,
        )

    # 3. Fallback: first substage for current stage
    if entries:
        entry = entries[0]
        key = entry.get("key") or defaults.get(stage_key) or "task_orchestration"
        return key, entry.get("label") or key

    fallback = defaults.get(stage_key) or "task_orchestration"
    return fallback, fallback


async def ensure_task_session(task_id: str) -> str | None:
    """Return a session created by a prior explicit live execution, if any."""
    if settings.skill_exec_mode != "live":
        return None

    task = store.get_task(task_id)
    if not task:
        raise KeyError("task not found")

    existing = store.get_agent_session_id(task_id)
    if existing:
        logger.info("ensure_task_session reuse session_id=%s task_id=%s", existing[:12], task_id)
    return existing


def _resolve_owner_user_skills(task: dict[str, Any]) -> list[str]:
    """Return the task owner's private skill names, for auto-inclusion this turn.

    Phase-1 "how is it used": a user's uploaded skills are made available to the agent
    on every turn (the SDK decides whether to invoke them based on descriptions). This
    populates ``ExecutionRequest.user_skill_names`` → the presigned delivery manifest.
    Safe to fail (returns ``[]``); never blocks a turn.
    """
    owner = task.get("ownerId") or task.get("owner_id")
    if not owner:
        return []
    try:
        from app.modules.user_skills_service import list_user_skill_names

        return list_user_skill_names(owner)
    except Exception:  # noqa: BLE001
        logger.warning("could not resolve user skills for owner=%s", owner, exc_info=True)
        return []


def _record_turn_usage(
    *,
    task_id: str | None,
    owner_id: str | None,
    source: str,
    result: Any = None,
    usage_wire: dict[str, Any] | None = None,
    call_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    status: str = "success",
) -> None:
    """Record one chat/execution turn's token usage to the ledger (S03-14-02).

    Accepts either an ``ExecutionResult`` (inline/container non-stream path) or
    a pre-extracted ``usage_wire`` (streaming done event). Best-effort: never
    raises into the business path.
    """
    if result is not None:
        audit = getattr(result, "audit", None) or {}
        usage_wire = usage_wire or audit.get("usage")
        provider = provider or audit.get("provider")
        model = model or audit.get("model")
        if getattr(result, "status", "completed") in {"failed", "timeout"}:
            status = "failed"
    if not owner_id:
        return
    # U-04: record + settle via the single metering mechanism.
    from app.modules.token_ledger_repository import meter_call

    meter_call(
        user_id=owner_id,
        source=source,
        usage_wire=usage_wire,
        task_id=task_id,
        call_id=call_id,
        provider=provider,
        model=model,
        status=status,
    )


async def _run_claude_chat(
    task: dict[str, Any],
    message: str,
    task_id: str,
    *,
    substage_key: str | None = None,
    requested_skill_id: str | None = None,
    l3_skills_meta: list[dict[str, str]] | None = None,
    existing_l3_plan: dict[str, Any] | None = None,
    ledger_source: str = "workbench_chat",
    capability_call_id: str | None = None,
) -> tuple[str, str | None]:
    session_id = await ensure_task_session(task_id)
    mode = _task_mode(task)
    exec_kwargs: dict[str, Any] = {}
    if mode != "creation" and substage_key:
        pack = task.get("skillPack", "sdr")
        exec_kwargs["substage_key"] = substage_key
        l3_skill_ids = pack_config.get_substage_skills(substage_key, pack=pack)
        exec_kwargs["available_skills"] = [_lookup_claude_name(s) for s in l3_skill_ids]
        l2_meta = _load_l2_skill_meta(pack, substage_key)
        exec_kwargs["l2_claude_name"] = _l2_claude_name(pack, substage_key)
        spec = pack_config.get_substage_spec(substage_key, pack=pack)
        if spec:
            exec_kwargs["spec_template"] = spec
        if l2_meta.get("output_file"):
            exec_kwargs["output_file"] = l2_meta["output_file"]
        input_skills = l2_meta.get("input_skills", [])
        if input_skills:
            input_files = []
            for dep_skill_id in input_skills:
                dep_meta = _load_skill_meta(dep_skill_id)
                if dep_meta.get("output_file"):
                    input_files.append(f"workspace/{task_id}/{dep_meta['output_file']}")
            if input_files:
                exec_kwargs["input_files"] = input_files
    if requested_skill_id:
        exec_kwargs["requested_skill_id"] = requested_skill_id
    if l3_skills_meta:
        exec_kwargs["l3_skills_meta"] = l3_skills_meta
    if existing_l3_plan:
        exec_kwargs["existing_l3_plan"] = existing_l3_plan

    owner_skills = _resolve_owner_user_skills(task)
    if owner_skills:
        exec_kwargs["user_skill_names"] = owner_skills

    owner_id = task.get("ownerId") or task.get("owner_id")
    request = ExecutionRequest(
        task_id=task_id,
        message=message,
        mode="creation" if mode == "creation" else "execution",
        session_id=session_id,
        task=task,
        owner_id=owner_id,
        capability_call_id=capability_call_id,
        **exec_kwargs,
    )
    runtime = get_execution_runtime()
    result = await runtime.submit(request)
    _record_turn_usage(task_id=task_id, owner_id=owner_id, result=result, source=ledger_source)
    return result.reply, result.session_id


async def _stream_claude_chat(
    task: dict[str, Any],
    message: str,
    task_id: str,
    *,
    substage_key: str | None = None,
    requested_skill_id: str | None = None,
    l3_skills_meta: list[dict[str, str]] | None = None,
    existing_l3_plan: dict[str, Any] | None = None,
    capability_call_id: str | None = None,
):
    session_id = await ensure_task_session(task_id)
    mode = _task_mode(task)
    logger.info("_stream_claude_chat task_id=%s session_id=%s", task_id, (session_id or "none")[:12])
    exec_kwargs: dict[str, Any] = {}
    if mode != "creation" and substage_key:
        pack = task.get("skillPack", "sdr")
        exec_kwargs["substage_key"] = substage_key
        l3_skill_ids = pack_config.get_substage_skills(substage_key, pack=pack)
        exec_kwargs["available_skills"] = [_lookup_claude_name(s) for s in l3_skill_ids]
        l2_meta = _load_l2_skill_meta(pack, substage_key)
        exec_kwargs["l2_claude_name"] = _l2_claude_name(pack, substage_key)
        spec = pack_config.get_substage_spec(substage_key, pack=pack)
        if spec:
            exec_kwargs["spec_template"] = spec
        if l2_meta.get("output_file"):
            exec_kwargs["output_file"] = l2_meta["output_file"]
        input_skills = l2_meta.get("input_skills", [])
        if input_skills:
            input_files = []
            for dep_skill_id in input_skills:
                dep_meta = _load_skill_meta(dep_skill_id)
                if dep_meta.get("output_file"):
                    input_files.append(f"workspace/{task_id}/{dep_meta['output_file']}")
            if input_files:
                exec_kwargs["input_files"] = input_files
    if requested_skill_id:
        exec_kwargs["requested_skill_id"] = requested_skill_id
    if l3_skills_meta:
        exec_kwargs["l3_skills_meta"] = l3_skills_meta
    if existing_l3_plan:
        exec_kwargs["existing_l3_plan"] = existing_l3_plan

    owner_skills = _resolve_owner_user_skills(task)
    if owner_skills:
        exec_kwargs["user_skill_names"] = owner_skills

    # See _run_claude_chat: owner_id must ride on the real turn so ContainerRuntime
    # builds the manifest and the worker downloads the owner's skills.
    owner_id = task.get("ownerId") or task.get("owner_id")
    request = ExecutionRequest(
        task_id=task_id,
        message=message,
        mode="creation" if mode == "creation" else "execution",
        session_id=session_id,
        task=task,
        owner_id=owner_id,
        capability_call_id=capability_call_id,
        **exec_kwargs,
    )
    runtime = get_execution_runtime()
    async for event in runtime.submit_stream(request):
        if event.get("type") == "done":
            sid = event.get("sessionId")
            if sid:
                store.set_agent_session_id(task_id, sid)
        yield event


def _find_substage_parent_stage(task: dict[str, Any], substage_key: str) -> str | None:
    """Locate the parent stage_key for an existing substage instance, regardless of stage status."""
    for detail in task.get("stageDetails") or []:
        for ss in detail.get("subStages") or []:
            if ss.get("subStage") == substage_key:
                return detail.get("stageKey") or ss.get("parentStageKey")
    for catalog_entry in task.get("stageCatalog") or []:
        for entry in catalog_entry.get("allowedSubStages") or []:
            if entry.get("key") == substage_key:
                return catalog_entry.get("stageKey")
    return None


def _bind_runtime_context(
    task_id: str,
    task: dict[str, Any],
    skill_id: str | None,
    message: str,
    substage_key_override: str | None = None,
) -> tuple[str, str, dict[str, Any] | None]:
    """Bind (stage_key, substage_key, substage_instance) for a chat turn.

    When the user targets a specific substage, prefer the substage's real parent
    stage — even if that stage is already marked completed. This lets users
    re-enter a finished stage to iterate on its artifacts. Falls back to
    task.currentStage only when no explicit target is given.
    """
    pack = task.get("skillPack", "sdr") or "sdr"
    if substage_key_override:
        substage_key = substage_key_override
        substage_label = pack_config.get_substage_label(substage_key, pack=pack) or substage_key
        stage_key = _find_substage_parent_stage(task, substage_key) or task.get("currentStage") or "problem_modeling"
    else:
        substage_key, substage_label = _resolve_substage(task, skill_id, message)
        stage_key = (
            _find_substage_parent_stage(task, substage_key)
            or task.get("currentStage")
            or "problem_modeling"
        )
    substage = store.get_or_create_substage_instance(
        task_id,
        stage_key=stage_key,
        substage_key=substage_key,
        label=substage_label,
        status="running",
    )
    return stage_key, substage_key, substage


def _try_register_substage_artifact(
    task_id: str,
    substage_key: str | None,
    substage: dict[str, Any] | None,
    call_id: str | None,
    pack: str = "sdr",
) -> list[str]:
    """After a chat turn, check if the current substage's output_file exists on disk.

    If the file is present and not yet registered as an artifact, register it.
    Returns list of newly created artifact IDs.
    """
    if not substage_key:
        return []

    meta = _load_l2_skill_meta(pack, substage_key)
    output_file = meta.get("output_file")
    if not output_file:
        return []

    artifact_path = f"workspace/{task_id}/{output_file}"
    full_path = get_task_workspace_dir(task_id) / output_file
    if not full_path.exists():
        return []

    substage_instance_id = (substage or {}).get("subStageInstanceId")

    # Deduplicate only the same output bound to the same runtime context. The
    # same skill output_file can be reused by another substage/call and must
    # still become a distinct task artifact so stage/substage refs stay complete.
    task = store.get_task(task_id)
    if task:
        for a in task.get("artifacts") or []:
            if (
                (a.get("path") == artifact_path or a.get("sourceOutputPath") == artifact_path)
                and a.get("subStageInstanceId") == substage_instance_id
                and a.get("sourceCapabilityCallId") == call_id
            ):
                return []

    substage_label = (substage or {}).get("label") or substage_key
    stage_key = pack_config.get_substage_stage(substage_key, pack=task.get("skillPack", "sdr") if task else "sdr")

    fmt = "markdown"
    if output_file.endswith(".yaml") or output_file.endswith(".yml"):
        fmt = "yaml"

    # Determine inline vs local storage
    inline_content: str | None = None
    use_inline = False
    try:
        size = full_path.stat().st_size
        if size <= _INLINE_SIZE_THRESHOLD:
            inline_content = full_path.read_text(encoding="utf-8")
            use_inline = True
    except (OSError, UnicodeDecodeError):
        pass

    # If this substage output is the result of a requested regeneration, fold it
    # into the original artifact as a new agent_regenerate version instead of
    # creating a duplicate artifact (issue #302).
    folded = _maybe_fold_regenerated_output(
        task_id, substage_instance_id, full_path, artifact_path, inline_content, use_inline
    )
    if folded is not None:
        return folded

    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title=f"{substage_label} 输出",
        summary=None,
        path=None if use_inline else artifact_path,
        format=fmt,
        related_stage=stage_key,
        related_skill_id=substage_key,
        substage_instance_id=substage_instance_id,
        source_capability_call_id=call_id,
        extra_fields={
            "sourceOutputPath": artifact_path,
            "artifactState": _artifact_state_for_skill_output(meta),
            "userFacingNarrative": build_presentation_agent_narrative(
                f"{substage_label} 输出已登记为产物。",
                source_type="artifact",
                source_id=None,
                status="completed",
            ),
        },
    )
    if artifact:
        # Always pass both the file path and inline content —
        # _decide_storage_and_upload will pick the right strategy:
        #   MinIO: upload file (or bytes if no file), never inline
        #   local:  inline for small content, local path for large
        storage, content, ext_key = _decide_storage_and_upload(
            task_id,
            full_path,
            inline_content if use_inline else None,
        )
        _patch_artifact_storage(
            task_id, artifact["id"],
            storage_type=storage, inline_content=content, external_key=ext_key,
        )
        _maybe_auto_review_low_risk_artifact(task_id, artifact["id"], meta)
        logger.info("auto-registered artifact %s for substage %s (storage=%s)", artifact["id"], substage_key, storage)
        return [artifact["id"]]
    return []


# Content file extensions considered substage outputs when scanning the
# workspace as a fallback (the LLM doesn't always write the exact output_file).
# Images (png/svg) are included so chart/figure outputs (paper-figure,
# figure-spec, phase diagrams) auto-register; binary content is never inlined
# (read_text raises UnicodeDecodeError -> use_inline stays False).
_ARTIFACT_CONTENT_EXTS = {
    ".md", ".json", ".yaml", ".yml", ".tex", ".txt", ".py", ".csv", ".tsv", ".bib",
    ".png", ".svg",
    ".cif",
}


def _scan_unregistered_outputs(task_id: str, ws_dir: Path) -> list[Path]:
    """Content files written to the task workspace but not yet registered.

    Fallback for when the LLM doesn't write the skill's exact output_file path
    (e.g. GLM writing ``references.md`` instead of ``proposal/reference.md``).
    """
    if not ws_dir.is_dir():
        return []
    task = store.get_task(task_id)
    registered = {a.get("path") or "" for a in ((task or {}).get("artifacts") or [])}
    out: list[Path] = []
    for p in sorted(ws_dir.rglob("*")):
        try:
            if not p.is_file() or p.suffix.lower() not in _ARTIFACT_CONTENT_EXTS:
                continue
            if p.stat().st_size > 5 * 1024 * 1024:  # skip huge files
                continue
        except OSError:
            continue
        rel = p.relative_to(ws_dir).as_posix()
        if f"workspace/{task_id}/{rel}" in registered:
            continue
        out.append(p)
    return out


def _register_workspace_outputs_fallback(
    task_id: str,
    substage_key: str | None,
    substage: dict[str, Any] | None,
    call_id: str | None,
) -> list[str]:
    """Register workspace content files the exact output_file match missed.

    Called only when ``_try_register_substage_artifact`` found nothing — i.e.
    the substage's configured output_file isn't on disk. Scans the workspace
    for any unregistered content file the LLM did write and registers those, so
    a deviating filename still becomes a visible/uploaded artifact.
    """
    ws_dir = get_task_workspace_dir(task_id)
    files = _scan_unregistered_outputs(task_id, ws_dir)
    if not files:
        return []

    task = store.get_task(task_id)
    substage_label = (substage or {}).get("label") or (substage_key or "")
    substage_instance_id = (substage or {}).get("subStageInstanceId")
    stage_key = (
        pack_config.get_substage_stage(
            substage_key or "", pack=task.get("skillPack", "sdr") if task else "sdr"
        )
        if substage_key
        else None
    )

    created: list[str] = []
    for full_path in files:
        rel = full_path.relative_to(ws_dir).as_posix()
        artifact_path = f"workspace/{task_id}/{rel}"
        low = full_path.suffix.lower()
        if low in (".yaml", ".yml"):
            fmt = "yaml"
        elif low == ".json":
            fmt = "json"
        elif low == ".png":
            fmt = "png"
        elif low == ".svg":
            fmt = "svg"
        else:
            fmt = "markdown"

        inline_content: str | None = None
        use_inline = False
        try:
            if full_path.stat().st_size <= _INLINE_SIZE_THRESHOLD:
                inline_content = full_path.read_text(encoding="utf-8")
                use_inline = True
        except (OSError, UnicodeDecodeError):
            pass

        # issue #302: if a regeneration is pending for this substage, fold this
        # output into the original artifact as an agent_regenerate version instead
        # of registering a duplicate. Mirrors the exact output_file path in
        # _try_register_substage_artifact; consume_pending_regenerate peeks
        # (substage-scoped) and adopt_artifact_as_version clears the marker.
        folded = _maybe_fold_regenerated_output(
            task_id, substage_instance_id, full_path, artifact_path, inline_content, use_inline
        )
        if folded is not None:
            created.extend(folded)
            continue

        title = f"{substage_label} 输出" if substage_label else f"工作区输出 ({rel})"
        skill_meta = (
            _load_l2_skill_meta(task.get("skillPack", "sdr") if task else "sdr", substage_key or "")
            if substage_key
            else {}
        )
        artifact = store.add_artifact(
            task_id,
            kind="stage_summary",
            title=title,
            summary=None,
            path=None if use_inline else artifact_path,
            format=fmt,
            related_stage=stage_key,
            related_skill_id=substage_key or "workbench_chat",
            substage_instance_id=(substage or {}).get("subStageInstanceId"),
            source_capability_call_id=call_id,
            extra_fields={
                "artifactState": _artifact_state_for_skill_output(skill_meta),
                "userFacingNarrative": build_presentation_agent_narrative(
                    f"{title} 已登记为产物。",
                    source_type="artifact",
                    source_id=None,
                    status="completed",
                ),
            },
        )
        if artifact:
            storage, content, ext_key = _decide_storage_and_upload(
                task_id, full_path, inline_content if use_inline else None,
            )
            _patch_artifact_storage(
                task_id, artifact["id"],
                storage_type=storage, inline_content=content, external_key=ext_key,
            )
            _maybe_auto_review_low_risk_artifact(task_id, artifact["id"], skill_meta)
            created.append(artifact["id"])
            logger.info(
                "fallback-registered artifact %s (substage=%s file=%s storage=%s)",
                artifact["id"], substage_key, rel, storage,
            )
    return created


_INLINE_SIZE_THRESHOLD = 100 * 1024  # 100 KB


def _decide_storage_and_upload(
    task_id: str,
    file_path: Path | None,
    inline_content: str | None,
) -> tuple[str, str | None, str | None]:
    """Decide storage type and upload to MinIO if configured.

    Delegates to :class:`ArtifactWriteService`, which respects the inline threshold
    (``OPENAIR_ARTIFACT_INLINE_MAX_BYTES``) in both local and MinIO modes — small text
    stays inline even when MinIO is enabled (Artifact-02).

    Returns (storage_type, inline_content_or_None, external_key_or_None).
    """
    from app.modules.artifact_write import artifact_write_service

    return artifact_write_service.decide_and_store(task_id, file_path, inline_content)


def _patch_artifact_storage(
    task_id: str,
    artifact_id: str,
    *,
    storage_type: str,
    inline_content: str | None = None,
    external_key: str | None = None,
) -> None:
    """Patch artifact storage fields after add_artifact.

    Delegates to ``store.patch_artifact_storage``, which patches the top-level
    fields AND re-syncs the current version's content snapshot. The registration
    flow is a two-step (``add_artifact`` creates a content-less shell, then this
    patches the real storage decision); without the version sync the initial v1
    would keep a stale/empty snapshot while the top-level shows the real content,
    breaking the issue-#302 invariant (current version ≡ top-level).
    """
    store.patch_artifact_storage(
        task_id,
        artifact_id,
        storage_type=storage_type,
        inline_content=inline_content,
        external_key=external_key,
    )


def _maybe_fold_regenerated_output(
    task_id: str,
    substage_instance_id: str | None,
    full_path: Path,
    artifact_path: str,
    inline_content: str | None,
    use_inline: bool,
) -> list[str] | None:
    """If the task has a pending regeneration for this substage, fold the freshly
    generated output into the original artifact as a new ``agent_regenerate``
    version (issue #302) instead of registering a separate artifact.

    Returns ``[artifact_id]`` when a fold happened, else ``None``.
    """
    pending = store.consume_pending_regenerate(task_id, substage_instance_id)
    if not pending:
        return None
    target_id = pending.get("artifactId")
    if not target_id:
        return None

    storage, content, ext_key = _decide_storage_and_upload(
        task_id, full_path, inline_content if use_inline else None
    )
    sha256: str | None = None
    size: int | None = None
    try:
        data = full_path.read_bytes()
        size = len(data)
        sha256 = hashlib.sha256(data).hexdigest()
    except OSError:
        pass

    if storage == "inline":
        fold_inline, fold_path, fold_key = content, None, None
    elif storage == "external":
        fold_inline, fold_path, fold_key = None, None, ext_key
    else:  # local
        fold_inline, fold_path, fold_key = None, artifact_path, None

    store.adopt_artifact_as_version(
        task_id,
        target_id,
        feedback=pending.get("feedback"),
        storage_type=storage,
        inline_content=fold_inline,
        path=fold_path,
        external_key=fold_key,
        content_sha256=sha256,
        content_size=size,
    )
    logger.info(
        "folded regenerated output into artifact %s as agent_regenerate version",
        target_id,
    )
    return [target_id]


def _complete_runtime_context(
    task_id: str,
    *,
    substage: dict[str, Any] | None,
    call_id: str | None,
    status: str,
    summary: str | None,
    artifact_ids: list[str] | None = None,
) -> None:
    if not substage:
        return
    refs = list(substage.get("capabilityCallRefs") or [])
    if call_id:
        refs.append(call_id)
    artifact_refs = list(substage.get("artifactRefs") or [])
    if artifact_ids:
        artifact_refs.extend(artifact_ids)
    store.patch_substage_instance(
        task_id,
        substage.get("subStageInstanceId"),
        status=status,
        summary=summary,
        artifact_refs=list(dict.fromkeys(artifact_refs)),
        capability_call_refs=list(dict.fromkeys(refs)),
        finished_at=_utc_now() if status in ("completed", "failed") else None,
    )
    if status not in {"needs_input", "blocked_recoverable", "insufficient_evidence"}:
        return
    task = store.get_task(task_id)
    if task and not any(call.get("status") == "running" for call in task.get("capabilityCalls") or []):
        # A terminal, user-actionable outcome is not an active execution. This
        # prevents the workbench from presenting a stale spinner or locking its
        # input after the runtime has already stopped.
        store.patch_task(task_id, {"status": "awaiting_confirmation"})


def _update_l3_plan_on_substage(task_id: str, substage_instance_id: str, plan: dict[str, Any]) -> None:
    """Persist L3 plan state onto a substage instance."""
    store.patch_substage_l3_plan(task_id, substage_instance_id, plan)


def _extract_fenced_json_block(reply: str) -> str | None:
    marker = "```json"
    lower = reply.lower()
    start = lower.find(marker)
    if start == -1:
        return None
    content_start = start + len(marker)
    end = reply.find("```", content_start)
    if end == -1:
        return None
    return reply[content_start:end].strip()


def _iter_balanced_json_objects(reply: str) -> list[str]:
    in_string = False
    escape = False
    depth = 0
    start = -1
    objects: list[str] = []

    for idx, ch in enumerate(reply):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(reply[start : idx + 1])
                start = -1
    return objects


def _is_valid_creation_plan(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    if parsed.get("readyToConfirm") is not True:
        return False
    if not isinstance(parsed.get("goalSummary"), str) or not parsed["goalSummary"].strip():
        return False
    sp = parsed.get("skillPack")
    if not isinstance(sp, str) or not sp.strip():
        return False
    from app.modules.pack_config import list_available_packs
    known_packs = {p["key"] for p in list_available_packs()}
    if sp not in known_packs:
        logger.warning("rejected creation plan with unknown skillPack=%s known=%s", sp, known_packs)
        return False
    stages = parsed.get("stages")
    if not isinstance(stages, list) or len(stages) == 0:
        return False
    # Accept both old format (subStages: string[]) and new workflow format (substages: object[])
    for stage in stages:
        if not isinstance(stage, dict):
            return False
        # New workflow format: stages[].substages is a list of objects
        substages = stage.get("substages")
        if isinstance(substages, list):
            for ss in substages:
                if not isinstance(ss, dict) or not ss.get("substage"):
                    return False
    return True


def _extract_fenced_yaml_block(reply: str) -> str | None:
    marker = "```yaml"
    lower = reply.lower()
    start = lower.find(marker)
    if start == -1:
        return None
    content_start = start + len(marker)
    end = reply.find("```", content_start)
    if end == -1:
        return None
    return reply[content_start:end].strip()


def _save_creation_plan(task_id: str, plan: dict[str, Any]) -> None:
    ws_dir = get_task_workspace_dir(task_id)
    ws_dir.mkdir(parents=True, exist_ok=True)
    dest = ws_dir / "plan.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dest)
    logger.info("creation plan saved to %s", dest)

    # Upload to MinIO if configured, then remove local copy
    from app.config import settings as _settings
    if _settings.artifact_storage == "minio":
        try:
            from app.modules.minio_client import upload_file
            upload_file(task_id, dest, content_type="application/json")
            dest.unlink()
            logger.info("plan.json uploaded to MinIO, local copy removed")
        except Exception as exc:
            logger.warning("plan.json MinIO upload failed, keeping local: %s", exc)


def _extract_task_plan_from_reply(reply: str) -> dict[str, Any] | None:
    candidates: list[str] = [reply]
    fenced_json = _extract_fenced_json_block(reply)
    if fenced_json:
        candidates.append(fenced_json)
    candidates.extend(_iter_balanced_json_objects(reply))

    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if _is_valid_creation_plan(parsed):
            return parsed

    # Try YAML extraction
    fenced_yaml = _extract_fenced_yaml_block(reply)
    if fenced_yaml:
        try:
            import yaml
            parsed = yaml.safe_load(fenced_yaml)
            if _is_valid_creation_plan(parsed):
                return parsed
        except Exception:
            pass

    return None


def _extract_confirmation_requests(reply: str, skill_id: str | None, call_id: str | None) -> list[dict[str, Any]]:
    pending_patterns = [
        r"(?ms)^##\s*待用户确认\s*(.+)$",
        r"(?ms)^##\s*待确认\s*(.+)$",
    ]
    block: str | None = None
    for pattern in pending_patterns:
        matched = re.search(pattern, reply)
        if matched:
            block = matched.group(1).strip()
            break
    if not block:
        return []

    requests: list[dict[str, Any]] = []
    for idx, raw in enumerate(block.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[-*]\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        line = re.sub(r"^\*\*待确认\s*\d+\*\*[:：]?\s*", "", line)
        line = re.sub(r"^待确认\s*\d*[:：]?\s*", "", line)
        line = line.strip()
        if not line:
            continue
        requests.append(
            {
                "id": f"confirm_{call_id or 'chat'}_{idx}",
                "title": "需要你的决定",
                "prompt": line,
                "kind": "approval",
                "status": "pending",
                "blocking": True,
                "recommendedOptionId": "approve",
                "options": [
                    {"id": "approve", "label": "确认采用", "value": "approve", "style": "primary"},
                    {
                        "id": "revise",
                        "label": "需要调整",
                        "value": "revise",
                        "requiresNote": True,
                        "style": "default",
                    },
                ],
                "artifactRefs": [],
                "relatedSkillId": skill_id,
                "relatedCallId": call_id,
            }
        )
    return requests


_ARTIFACT_JSON_MARKER = re.compile(r"```json\s*([\s\S]*?)```", re.IGNORECASE)


def _extract_chat_artifacts(reply: str) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for match in _ARTIFACT_JSON_MARKER.finditer(reply):
        raw = match.group(1).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "artifact":
                continue
            title = item.get("title")
            content = item.get("content")
            if not isinstance(title, str) or not title.strip() or not isinstance(content, str) or not content.strip():
                continue
            artifacts.append(
                {
                    "title": title.strip(),
                    "content": content.strip(),
                    "kind": str(item.get("kind") or "chat_output"),
                    "format": str(item.get("format") or "markdown"),
                    "summary": str(item.get("summary") or title).strip(),
                }
            )
    return artifacts


def _persist_chat_artifacts(
    task_id: str,
    *,
    reply: str,
    stage_key: str,
    skill_id: str | None,
    substage: dict[str, Any] | None,
    call_id: str | None,
) -> list[str]:
    """Register artifacts explicitly emitted by the LLM as JSON blocks.

    Only explicit blocks count — never synthesize a fake chat_output artifact from
    the reply text. Real skill-produced files are registered separately by
    _try_register_substage_artifact, which detects the skill's output_file on disk.
    """
    created_artifact_ids: list[str] = []
    explicit_artifacts = _extract_chat_artifacts(reply)
    if not explicit_artifacts:
        return created_artifact_ids

    base_id = (call_id or "message").replace("/", "-")
    for idx, artifact_spec in enumerate(explicit_artifacts, start=1):
        ext = ".md"
        fmt = (artifact_spec.get("format") or "markdown").lower()
        if fmt in ("yaml", "yml"):
            ext = ".yaml"
        elif fmt == "json":
            ext = ".json"
        content = artifact_spec["content"]
        use_inline = len(content.encode("utf-8")) <= _INLINE_SIZE_THRESHOLD

        if use_inline:
            artifact = store.add_artifact(
                task_id,
                kind=artifact_spec["kind"],
                title=artifact_spec["title"],
                summary=artifact_spec["summary"],
                path=None,
                format=artifact_spec["format"],
                related_stage=stage_key,
                related_skill_id=skill_id or "workbench_chat",
                substage_instance_id=substage.get("subStageInstanceId") if substage else None,
                source_capability_call_id=call_id,
                extra_fields={
                    "userFacingNarrative": build_presentation_agent_narrative(
                        artifact_spec["summary"],
                        source_type="artifact",
                        source_id=None,
                        status="completed",
                    )
                },
            )
            if artifact:
                storage, ic, ext_key = _decide_storage_and_upload(task_id, None, content)
                _patch_artifact_storage(
                    task_id, artifact["id"],
                    storage_type=storage, inline_content=ic, external_key=ext_key,
                )
                created_artifact_ids.append(artifact["id"])
            continue

        rel_path = f"workspace/{task_id}/chat-output-{base_id}-{idx}{ext}"
        full_path = get_task_workspace_dir(task_id) / f"chat-output-{base_id}-{idx}{ext}"

        # Write local file first (needed for local fallback and as upload source)
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
        except OSError:
            logger.exception("write chat artifact failed task_id=%s path=%s", task_id, rel_path)
            continue

        artifact = store.add_artifact(
            task_id,
            kind=artifact_spec["kind"],
            title=artifact_spec["title"],
            summary=artifact_spec["summary"],
            path=rel_path,
            format=artifact_spec["format"],
            related_stage=stage_key,
            related_skill_id=skill_id or "workbench_chat",
            substage_instance_id=substage.get("subStageInstanceId") if substage else None,
            source_capability_call_id=call_id,
            extra_fields={
                "userFacingNarrative": build_presentation_agent_narrative(
                    artifact_spec["summary"],
                    source_type="artifact",
                    source_id=None,
                    status="completed",
                )
            },
        )
        if artifact:
            storage, _, ext_key = _decide_storage_and_upload(task_id, full_path, None)
            _patch_artifact_storage(
                task_id, artifact["id"],
                storage_type=storage, external_key=ext_key,
            )
            created_artifact_ids.append(artifact["id"])
    return created_artifact_ids


def _persist_selected_skill_response_artifact(
    task_id: str,
    *,
    reply: str,
    stage_key: str,
    skill_id: str,
    substage: dict[str, Any] | None,
    call_id: str | None,
    skill_meta: dict[str, Any],
) -> list[str]:
    """Preserve a real selected-Skill response when it did not write a file.

    A Claude Code Skill may complete its bounded research response in the stream
    without creating its declared workspace file. That response is still the
    actual output of this capability call, so it must remain reviewable rather
    than disappearing into chat. This intentionally does not manufacture the
    declared file or claim any external source was retrieved.
    """
    if not reply.strip() or not call_id or _contains_blocking_internal_detail(reply, has_artifact=False):
        return []

    substage_label = (substage or {}).get("label") or skill_id
    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title=f"{substage_label} 输出",
        summary=f"本次 {substage_label} 的实际运行结果，待你核查后继续。",
        format="markdown",
        related_stage=stage_key,
        related_skill_id=skill_id,
        substage_instance_id=(substage or {}).get("subStageInstanceId"),
        source_capability_call_id=call_id,
        storage_type="inline",
        inline_content=reply,
        extra_fields={
            "artifactState": _artifact_state_for_skill_output(skill_meta),
            "outputCapture": "runtime_response",
            "userFacingNarrative": build_presentation_agent_narrative(
                f"{substage_label} 的实际运行结果已登记为待核查产物。",
                source_type="artifact",
                source_id=None,
                status="completed",
            ),
        },
    )
    return [artifact["id"]] if artifact else []


async def _persist_verified_literature_artifact(
    task_id: str,
    *,
    reply: str,
    stage_key: str,
    skill_id: str,
    substage: dict[str, Any] | None,
    call_id: str | None,
    skill_meta: dict[str, Any],
) -> tuple[list[str], str | None]:
    """Turn model-suggested arXiv ids into a real, source-audited literature artifact."""
    identifiers = list(dict.fromkeys(match.group(1) for match in _ARXIV_IDENTIFIER_RE.finditer(reply)))
    from app.modules.literature_live import (
        retrieve_arxiv_identifiers,
        retrieve_semantic_scholar_arxiv_identifiers,
        run_literature_live,
    )

    rows, attempt = await asyncio.to_thread(retrieve_arxiv_identifiers, identifiers[:12])
    attempts = [attempt]
    if not rows:
        semantic_rows, semantic_attempt = await asyncio.to_thread(
            retrieve_semantic_scholar_arxiv_identifiers,
            identifiers[:12],
        )
        attempts.append(semantic_attempt)
        rows = semantic_rows
    if not rows:
        tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", reply)
            if token.lower() not in _LITERATURE_QUERY_STOPWORDS
        ]
        query_terms = [term for term, _count in Counter(tokens).most_common(14)]
        if query_terms:
            live_run = await asyncio.to_thread(
                run_literature_live,
                " ".join(query_terms),
                top_k=5,
                timeout=12.0,
            )
            attempts.extend(live_run.attempted_apis)
            rows = [
                row
                for row in live_run.sources
                if row.is_relevant_enough and (row.url or row.doi or row.arxiv_id)
            ][:5]
    if not rows:
        return [], "公开学术来源未能核验本次候选文献，未生成可引用结果。请稍后重试，或补充 DOI、arXiv ID 和更具体的检索范围。"

    source_lines = [
        "## 已核验的文献候选",
        "",
        "以下条目由本次运行向 arXiv 公开元数据接口核验；仅代表题录与摘要线索，尚未阅读全文或形成系统综述。",
        "",
        "| 文献 | 年份 | arXiv | 可访问链接 |",
        "|---|---:|---|---|",
    ]
    evidence_refs: list[dict[str, Any]] = []
    for row in rows:
        identifier = row.doi or row.arxiv_id or row.external_id
        url = row.url or (f"https://doi.org/{row.doi}" if row.doi else f"https://arxiv.org/abs/{identifier}")
        source_lines.append(f"| {row.title} | {row.year or '-'} | {identifier} | {url} |")
        evidence_refs.append(
            {
                "id": f"{row.source_api.lower().replace(' ', '_')}:{identifier}",
                "claim": f"该文献题录由本次运行向 {row.source_api} 公开元数据接口核验。",
                "sourceTitle": row.title,
                "sourceUrl": url,
                "excerpt": "本次只核验题录、摘要和链接，不代表已阅读全文或验证其研究结论。",
            }
        )
    source_lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 本次只核验公开题录、摘要与链接，不把它们表述为已阅读全文或已证实的研究结论。",
            "- 请在核查题名、版本、研究对象与相关性后，再将条目用于正式引用或下游分析。",
        ]
    )

    substage_label = (substage or {}).get("label") or "文献检索"
    artifact = store.add_artifact(
        task_id,
        kind="literature_candidates",
        title=f"{substage_label}核验结果",
        summary=f"本次从 arXiv 公开元数据核验 {len(rows)} 条可追溯文献候选，待你筛读确认。",
        format="markdown",
        related_stage=stage_key,
        related_skill_id=skill_id,
        substage_instance_id=(substage or {}).get("subStageInstanceId"),
        source_capability_call_id=call_id,
        evidence_refs=evidence_refs,
        storage_type="inline",
        inline_content="\n".join(source_lines),
        extra_fields={
            "artifactState": _artifact_state_for_skill_output(skill_meta),
            "outputCapture": "verified_external_sources",
            "sourceAttempts": [
                {
                    "sourceApi": source_attempt.source_api,
                    "status": source_attempt.status,
                    "retrievedAt": source_attempt.retrieved_at,
                    "resultCount": source_attempt.result_count,
                    "error": source_attempt.error,
                }
                for source_attempt in attempts
            ],
            "userFacingNarrative": build_presentation_agent_narrative(
                f"已核验 {len(rows)} 条可追溯文献候选，等待你筛读确认。",
                source_type="artifact",
                source_id=None,
                status="completed",
            ),
        },
    )
    return ([artifact["id"]] if artifact else []), None


async def agent_chat_stream(
    task_id: str,
    message: str,
    skill_id: str | None = None,
    substage_key: str | None = None,
    *,
    runtime_message: str | None = None,
):
    task = store.get_task(task_id)
    if not task:
        raise KeyError("task not found")

    mode = _task_mode(task)
    if mode == "idle":
        yield {"type": "assistant_delta", "text": '当前任务尚未开始执行。请先完成计划确认并点击"开始任务"，或在创建流程中继续完善计划。'}
        yield {
            "type": "done",
            "reply": '当前任务尚未开始执行。请先完成计划确认并点击"开始任务"，或在创建流程中继续完善计划。',
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": "task is not in draft/running/awaiting_confirmation",
        }
        return

    if mode == "creation" and skill_id and skill_id not in CREATION_ALLOWED_SKILLS:
        yield {"type": "assistant_delta", "text": "当前为创建流程，只允许任务规划相关能力。请先完成澄清与计划确认。"}
        yield {
            "type": "done",
            "reply": "当前为创建流程，只允许任务规划相关能力。请先完成澄清与计划确认。",
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": f"skill {skill_id} is blocked in creation mode",
        }
        return

    if mode == "creation":
        effective_skill_id = "new-task-planning"
        stage_key = task.get("currentStage") or "problem_modeling"
        substage = None
        substage_key = None
    else:
        stage_key, resolved_substage_key, substage = _bind_runtime_context(
            task_id, task, skill_id or "workbench_chat", message, substage_key,
        )
        substage_key = substage.get("subStage") if substage else resolved_substage_key
        effective_skill_id = _resolve_effective_skill_id(skill_id, substage_key, pack=task.get("skillPack", "sdr"))

    # Cross-pack bleed guard: substage_key leaked from another pack (e.g.
    # constraints_criteria inside a materials task) has no valid L2 skill in
    # this pack. _resolve_effective_skill_id returns None — short-circuit
    # instead of fabricating a foreign skill name and running the wrong skill.
    if mode != "creation" and effective_skill_id is None:
        pack_label = task.get("skillPack", "sdr")
        blocked_text = (
            f"当前 substage 「{substage_key}」不属于 pack 「{pack_label}」，"
            f"已阻止跨 pack 加载技能。请检查工作流是否混入了其他 pack 的 substage。"
        )
        yield {"type": "assistant_delta", "text": blocked_text}
        yield {
            "type": "done",
            "reply": blocked_text,
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": f"cross-pack substage blocked: substage={substage_key} pack={pack_label}",
        }
        return

    dependency_block = _direct_skill_dependency_block(effective_skill_id)
    if mode != "creation" and dependency_block is not None:
        blocked_text = "该能力依赖系统生成的上游结构化输入，当前直接执行入口不会绕过该依赖。"
        yield {"type": "assistant_delta", "text": blocked_text}
        yield {
            "type": "done",
            "reply": blocked_text,
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": dependency_block,
        }
        return

    # Load L3 skill metadata and existing plan for orchestration
    l3_skills_meta = None
    existing_l3_plan = None
    if mode != "creation" and substage_key:
        pack = task.get("skillPack", "sdr")
        l3_skills_meta = pack_config.get_substage_skills_with_meta(substage_key, pack=pack)
        existing_l3_plan = substage.get("l3Plan") if substage else None

    yield {
        "type": "substage_bound",
        "stageKey": stage_key,
        "substageKey": substage_key,
        "subStageInstanceId": substage.get("subStageInstanceId") if substage else None,
        "substageLabel": substage.get("label") if substage else substage_key,
    }

    store.append_agent_message(task_id, role="user", text=message)
    effective_message = runtime_message or message

    _skill_meta = _load_skill_meta(effective_skill_id)
    _input_call_refs = _resolve_input_call_refs(task, _skill_meta.get("input_skills", []))

    selected_live_runtime = _declared_live_runtime(effective_skill_id, _skill_meta)
    runtime = "replay" if settings.skill_exec_mode == "replay" else selected_live_runtime
    call = store.add_capability_call(
        task_id,
        skill_id=effective_skill_id,
        status="running",
        runtime=runtime,
        stage_key=stage_key,
        substage_instance_id=substage.get("subStageInstanceId") if substage else None,
        input_call_refs=_input_call_refs,
        skill_hash=_skill_hash(effective_skill_id),
    )
    call_id = call["id"] if call else None

    error: str | None = None
    runtime_audit: dict[str, Any] = {}
    final_usage: dict[str, Any] | None = None
    reply_parts: list[str] = []
    logger.info(
        "agent_chat_stream task_id=%s mode=%s skill_id=%s effective=%s substage=%s",
        task_id, settings.skill_exec_mode, skill_id, effective_skill_id, substage_key,
    )
    yield {
        "type": "started",
        "runtime": runtime,
        "capabilityCallId": call_id,
        "skillId": effective_skill_id,
        "stageKey": stage_key,
        "substageKey": substage_key,
        "subStageInstanceId": substage.get("subStageInstanceId") if substage else None,
        "substageLabel": substage.get("label") if substage else substage_key,
        "requestedAt": call.get("requestedAt") if call else None,
    }
    if call_id:
        yield {
            "type": "capability_call_started",
            "capabilityCallId": call_id,
            "skillId": effective_skill_id,
            "runtime": runtime,
            "stageKey": stage_key,
            "substageKey": substage_key,
            "subStageInstanceId": substage.get("subStageInstanceId") if substage else None,
            "substageLabel": substage.get("label") if substage else substage_key,
            "requestedAt": call.get("requestedAt") if call else None,
        }
        yield {
            "type": "progress",
            "text": f"Launching skill: {effective_skill_id}",
            "capabilityCallId": call_id,
        }

    try:
        # L3 plan tracking state
        l3_plan_detected = False
        l3_plan_steps: list[dict[str, Any]] = []
        accumulated_text = ""
        stream_internal_notice_emitted = False
        processed_l3_done_ids: set[str] = set()

        if settings.skill_exec_mode == "replay":
            replay_text = replay_chat_reply(task, effective_message, effective_skill_id)
            reply_parts.append(replay_text)
            if _contains_user_visible_internal_detail(replay_text):
                stream_internal_notice_emitted = True
                replay_delta = _safe_business_reply_for_blocked_detail(
                    status="blocked_recoverable",
                    substage_label=(substage or {}).get("label") or (substage or {}).get("subStage"),
                )
            else:
                replay_delta = replay_text
            yield {"type": "assistant_delta", "text": replay_delta, "capabilityCallId": call_id}
        else:
            async for ev in _stream_claude_chat(
                task,
                effective_message,
                task_id,
                substage_key=substage_key,
                requested_skill_id=effective_skill_id,
                l3_skills_meta=l3_skills_meta,
                existing_l3_plan=existing_l3_plan,
                capability_call_id=call_id,
            ):
                if ev.get("type") == "assistant_delta":
                    text = ev.get("text") or ""
                    if text:
                        reply_parts.append(text)
                        accumulated_text += text
                    delta_text = text
                    if text and _contains_user_visible_internal_detail(accumulated_text):
                        if not stream_internal_notice_emitted:
                            stream_internal_notice_emitted = True
                            delta_text = _safe_business_reply_for_blocked_detail(
                                status="blocked_recoverable",
                                substage_label=(substage or {}).get("label") or (substage or {}).get("subStage"),
                            )
                        else:
                            delta_text = ""
                    if delta_text:
                        yield {"type": "assistant_delta", "text": delta_text, "capabilityCallId": call_id}

                    # --- L3 plan tag detection ---
                    if not l3_plan_detected:
                        parsed_plan = _parse_l3plan(accumulated_text)
                        if parsed_plan:
                            l3_plan_detected = True
                            meta_map = {
                                m["skill_id"]: m["display_name"]
                                for m in (l3_skills_meta or [])
                            }
                            l3_plan_steps = [
                                {
                                    "skill_id": s["skill_id"],
                                    "display_name": meta_map.get(s["skill_id"], s["skill_id"]),
                                    "purpose": s["purpose"],
                                    "status": "pending",
                                    "result": None,
                                }
                                for s in parsed_plan
                            ]
                            plan_payload = {"status": "executing", "steps": l3_plan_steps}
                            if substage and substage.get("subStageInstanceId"):
                                _update_l3_plan_on_substage(task_id, substage["subStageInstanceId"], plan_payload)
                            yield {
                                "type": "l3_plan_created",
                                "capabilityCallId": call_id,
                                "subStageInstanceId": substage.get("subStageInstanceId") if substage else None,
                                "plan": plan_payload,
                            }

                    # --- L3 done tag detection ---
                    if l3_plan_detected:
                        done_tags = _parse_l3done_tags(accumulated_text)
                        for tag in done_tags:
                            if tag["skill_id"] not in processed_l3_done_ids:
                                processed_l3_done_ids.add(tag["skill_id"])
                                for step in l3_plan_steps:
                                    if step["skill_id"] == tag["skill_id"] and step["status"] == "pending":
                                        step["status"] = "completed"
                                        step["result"] = tag["result"]
                                        break
                                yield {
                                    "type": "l3_progress",
                                    "capabilityCallId": call_id,
                                    "subStageInstanceId": substage.get("subStageInstanceId") if substage else None,
                                    "skillId": tag["skill_id"],
                                    "status": "completed",
                                    "result": tag["result"],
                                }
                        # Persist updated plan
                        if processed_l3_done_ids:
                            plan_payload = {"status": "executing", "steps": l3_plan_steps}
                            if substage and substage.get("subStageInstanceId"):
                                _update_l3_plan_on_substage(task_id, substage["subStageInstanceId"], plan_payload)

                    continue
                if ev.get("type") == "progress":
                    yield {"type": "progress", "text": ev.get("text") or "", "capabilityCallId": call_id}
                    continue
                if ev.get("type") == "tool_use":
                    yield {
                        "type": "tool_use",
                        "toolUseId": ev.get("toolUseId") or "",
                        "toolName": ev.get("toolName") or "",
                        "input": ev.get("input") or {},
                        "capabilityCallId": call_id,
                    }
                    continue
                if ev.get("type") == "tool_result":
                    yield {
                        "type": "tool_result",
                        "toolUseId": ev.get("toolUseId") or "",
                        "content": ev.get("content") or "",
                        "isError": ev.get("isError") or False,
                        "capabilityCallId": call_id,
                    }
                    continue
                if ev.get("type") == "done":
                    runtime = ev.get("runtime") or runtime
                    runtime_audit = ev.get("audit") or runtime_audit
                    # Usage rides on the done event: top-level for claude_code /
                    # container worker, nested under audit for huiyan (S03-14-02).
                    final_usage = ev.get("usage") or (runtime_audit or {}).get("usage") or final_usage
                    if ev.get("error"):
                        error = str(ev["error"])
                    continue
    except asyncio.CancelledError:
        error = "Agent stream was interrupted before completion."
        runtime = "error"
        if call_id:
            store.patch_capability_call(
                task_id,
                call_id,
                status="failed",
                result_summary=error,
                ended_at=_utc_now(),
                artifact_refs=[],
                output_artifact_refs=[],
                substage_instance_id=substage.get("subStageInstanceId") if substage else None,
                stage_key=stage_key,
            )
            _complete_runtime_context(
                task_id,
                substage=substage,
                call_id=call_id,
                status="failed",
                summary=error,
                artifact_ids=[],
            )
        raise
    except Exception as exc:
        logger.exception("agent_chat_stream provider execution failed task_id=%s", task_id)
        error = f"{type(exc).__name__}: {exc}"
        fail_text = "当前 Live 技能未完成，未使用历史回放替代结果。请恢复运行时后重试该技能。"
        reply_parts = [fail_text]
        yield {"type": "assistant_delta", "text": fail_text, "capabilityCallId": call_id}

    # --- Post-processing & final events (protected) ---
    created_artifact_ids: list[str] = []
    confirmation_requests: list[dict[str, Any]] = []
    try:
        reply = "".join(reply_parts).strip()

        # Strip L3 plan/done tags from the persisted reply
        # (they are already emitted as structured events during streaming)
        reply = _L3PLAN_RE.sub("", reply)
        reply = _L3DONE_RE.sub("", reply)
        reply = reply.strip()

        if not reply:
            if runtime == HUIYAN_SKILL_RUNTIME:
                reply = "模型暂时不可用，未使用回放或模拟数据，请稍后重试当前步骤。"
            else:
                reply = "（Claude Code 未返回文本，请检查本机是否已登录 `claude` CLI。）"

        # Finalize any pending L3 steps that weren't explicitly marked
        if l3_plan_detected and l3_plan_steps:
            changed = False
            for step in l3_plan_steps:
                if step["status"] == "pending":
                    step["status"] = "skipped"
                    step["result"] = "会话结束前未完成"
                    changed = True
            if changed:
                plan_payload = {"status": "completed", "steps": l3_plan_steps}
                if substage and substage.get("subStageInstanceId"):
                    _update_l3_plan_on_substage(task_id, substage["subStageInstanceId"], plan_payload)

        confirmation_requests = _extract_confirmation_requests(reply, effective_skill_id, call_id)
        created_artifact_ids = _persist_chat_artifacts(
            task_id,
            reply=reply,
            stage_key=stage_key,
            skill_id=effective_skill_id,
            substage=substage,
            call_id=call_id,
        )
        if call_id and not error:
            pack = task.get("skillPack", "sdr")
            file_artifact_ids = _try_register_substage_artifact(task_id, substage_key, substage, call_id, pack=pack)
            if not file_artifact_ids:
                file_artifact_ids = _register_workspace_outputs_fallback(task_id, substage_key, substage, call_id)
            if file_artifact_ids:
                created_artifact_ids = list(dict.fromkeys([*created_artifact_ids, *file_artifact_ids]))
            if (
                not created_artifact_ids
                and effective_skill_id.replace("_", "-") == "literature-search"
                and not _contains_blocking_internal_detail(reply, has_artifact=False)
            ):
                created_artifact_ids, verification_error = await _persist_verified_literature_artifact(
                    task_id,
                    reply=reply,
                    stage_key=stage_key,
                    skill_id=effective_skill_id,
                    substage=substage,
                    call_id=call_id,
                    skill_meta=_skill_meta,
                )
                if verification_error:
                    error = verification_error
            if (
                not error
                and not created_artifact_ids
                and mode == "execution"
                and effective_skill_id not in _GENERIC_SKILL_IDS
            ):
                created_artifact_ids = _persist_selected_skill_response_artifact(
                    task_id,
                    reply=reply,
                    stage_key=stage_key,
                    skill_id=effective_skill_id,
                    substage=substage,
                    call_id=call_id,
                    skill_meta=_skill_meta,
                )

        business_status, safe_reply = _classify_business_outcome(
            reply=reply,
            error=error,
            created_artifact_ids=created_artifact_ids,
            confirmation_requests=confirmation_requests,
            substage=substage,
        )
        reply = safe_reply

        message_narrative = build_summary_agent_narrative(
            reply,
            source_type="assistant_message",
            source_id=call_id,
            severity=(
                "error"
                if business_status == "failed"
                else "blocked"
                if business_status in {"needs_input", "blocked_recoverable", "insufficient_evidence"}
                else "success"
            ),
        )

        store.append_agent_message(
            task_id,
            role="assistant",
            text=reply,
            runtime=runtime,
            capability_call_id=call_id,
            artifact_refs=created_artifact_ids,
            confirmation_requests=confirmation_requests,
            user_facing_narrative=message_narrative,
        )
        _record_turn_usage(
            task_id=task_id,
            owner_id=task.get("ownerId") or task.get("owner_id"),
            source="workbench_chat",
            usage_wire=final_usage,
            call_id=call_id,
            provider=(runtime_audit or {}).get("provider"),
            model=(runtime_audit or {}).get("model"),
            status="failed" if business_status == "failed" else "success",
        )
        if call_id:
            failed = business_status == "failed"
            call_narrative = build_presentation_agent_narrative(
                error if failed else reply,
                source_type="capability_call",
                source_id=call_id,
                artifact_refs=created_artifact_ids,
                status="failed" if failed else business_status,
            )
            store.patch_capability_call(
                task_id,
                call_id,
                status="failed" if failed else business_status,
                result_summary=error if failed else reply[:500],
                ended_at=_utc_now(),
                artifact_refs=created_artifact_ids,
                output_artifact_refs=created_artifact_ids,
                substage_instance_id=substage.get("subStageInstanceId") if substage else None,
                stage_key=stage_key,
                metadata={"userFacingNarrative": call_narrative, **runtime_audit},
            )
            _complete_runtime_context(
                task_id,
                substage=substage,
                call_id=call_id,
                status="failed" if failed else business_status,
                summary=reply[:500],
                artifact_ids=created_artifact_ids,
            )
            yield {
                "type": "capability_call_updated",
                "capabilityCallId": call_id,
                "status": "failed" if failed else business_status,
                "runtime": runtime,
                "resultSummary": error if failed else reply[:500],
                "artifactRefs": created_artifact_ids,
                "inputArtifactRefs": [],
                "outputArtifactRefs": created_artifact_ids,
                "stageKey": stage_key,
                "subStageInstanceId": substage.get("subStageInstanceId") if substage else None,
                "endedAt": _utc_now(),
                "userFacingNarrative": call_narrative,
            }

        for artifact_id in created_artifact_ids:
            yield {
                "type": "artifact_created",
                "capabilityCallId": call_id,
                "artifactId": artifact_id,
            }

        for confirmation in confirmation_requests:
            yield {
                "type": "confirmation_requested",
                "capabilityCallId": call_id,
                "confirmation": confirmation,
            }

        if mode == "creation":
            plan = _extract_task_plan_from_reply(reply)
            if plan is not None:
                patch_body: dict[str, Any] = {
                    "creationPlan": plan,
                    "creationStatus": "awaiting_confirmation",
                }
                chosen_pack = (plan.get("skillPack") or "").strip()
                current_pack = (task.get("skillPack") or "sdr").strip()
                if chosen_pack and chosen_pack != current_pack:
                    patch_body["skillPack"] = chosen_pack
                    patch_body["stageCatalog"] = build_stage_catalog(pack=chosen_pack)
                    logger.info(
                        "task_id=%s skillPack switch %s -> %s, stageCatalog rebuilt",
                        task_id, current_pack, chosen_pack,
                    )
                store.patch_task(task_id, patch_body)
                _save_creation_plan(task_id, plan)
            elif "readyToConfirm" in reply or '"stages"' in reply:
                logger.warning(
                    "creation plan extraction failed task_id=%s reply_len=%d",
                    task_id,
                    len(reply),
                )
    except Exception as exc:
        logger.exception("agent_chat_stream post-processing failed task_id=%s", task_id)
        error = f"post-processing error: {type(exc).__name__}"
        runtime = "error"

    try:
        messages = store.get_agent_messages(task_id)
    except Exception:
        logger.exception("get_agent_messages failed task_id=%s", task_id)
        messages = []
    yield {
        "type": "done",
        "reply": reply if "reply" in locals() else ("".join(reply_parts).strip() if reply_parts else ""),
        "runtime": runtime,
        "messages": messages,
        "capabilityCallId": call_id,
        "createdArtifactIds": created_artifact_ids,
        "error": error,
    }


async def agent_chat(
    task_id: str,
    message: str,
    skill_id: str | None = None,
    substage_key: str | None = None,
    *,
    runtime_message: str | None = None,
) -> dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        raise KeyError("task not found")

    mode = _task_mode(task)
    if mode == "idle":
        return {
            "reply": '当前任务尚未开始执行。请先完成计划确认并点击"开始任务"，或在创建流程中继续完善计划。',
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": "task is not in draft/running/awaiting_confirmation",
        }

    if mode == "creation" and skill_id and skill_id not in CREATION_ALLOWED_SKILLS:
        return {
            "reply": "当前为创建流程，只允许任务规划相关能力。请先完成澄清与计划确认。",
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": f"skill {skill_id} is blocked in creation mode",
        }

    if mode == "creation":
        effective_skill_id = "new-task-planning"
        stage_key = task.get("currentStage") or "problem_modeling"
        substage = None
        substage_key = None
    else:
        stage_key, resolved_substage_key, substage = _bind_runtime_context(
            task_id, task, skill_id or "workbench_chat", message, substage_key,
        )
        substage_key = substage.get("subStage") if substage else resolved_substage_key
        effective_skill_id = _resolve_effective_skill_id(skill_id, substage_key, pack=task.get("skillPack", "sdr"))

    # Cross-pack bleed guard (see agent_chat_stream for rationale).
    if mode != "creation" and effective_skill_id is None:
        pack_label = task.get("skillPack", "sdr")
        blocked_text = (
            f"当前 substage 「{substage_key}」不属于 pack 「{pack_label}」，"
            f"已阻止跨 pack 加载技能。请检查工作流是否混入了其他 pack 的 substage。"
        )
        return {
            "reply": blocked_text,
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": f"cross-pack substage blocked: substage={substage_key} pack={pack_label}",
        }

    dependency_block = _direct_skill_dependency_block(effective_skill_id)
    if mode != "creation" and dependency_block is not None:
        return {
            "reply": "该能力依赖系统生成的上游结构化输入，当前直接执行入口不会绕过该依赖。",
            "runtime": "system_guard",
            "messages": store.get_agent_messages(task_id),
            "capabilityCallId": None,
            "error": dependency_block,
        }

    store.append_agent_message(task_id, role="user", text=message)
    effective_message = runtime_message or message

    _skill_meta = _load_skill_meta(effective_skill_id)
    _input_call_refs = _resolve_input_call_refs(task, _skill_meta.get("input_skills", []))

    call = store.add_capability_call(
        task_id,
        skill_id=effective_skill_id,
        status="running",
        runtime=AGENT_RUNTIME_CLAUDE_CODE,
        stage_key=stage_key,
        substage_instance_id=substage.get("subStageInstanceId") if substage else None,
        input_call_refs=_input_call_refs,
        skill_hash=_skill_hash(effective_skill_id),
    )
    call_id = call["id"] if call else None

    runtime = "replay" if settings.skill_exec_mode == "replay" else AGENT_RUNTIME_CLAUDE_CODE
    error: str | None = None
    reply: str

    try:
        if settings.skill_exec_mode == "replay":
            reply = replay_chat_reply(task, effective_message, effective_skill_id)
        else:
            reply, new_session = await _run_claude_chat(
                task,
                effective_message,
                task_id,
                substage_key=substage_key,
                requested_skill_id=effective_skill_id,
                capability_call_id=call_id,
            )
            if new_session:
                store.set_agent_session_id(task_id, new_session)
    except Exception as exc:
        logger.exception("agent_chat provider execution failed task_id=%s", task_id)
        error = f"{type(exc).__name__}: {exc}"
        runtime = "error"
        reply = f"执行失败：{error}"

    # Strip L3 plan/done tags from the persisted reply
    reply = _L3PLAN_RE.sub("", reply)
    reply = _L3DONE_RE.sub("", reply)
    reply = reply.strip()

    if not reply:
        if runtime == HUIYAN_SKILL_RUNTIME:
            reply = "模型暂时不可用，未使用回放或模拟数据，请稍后重试当前步骤。"
        else:
            reply = "（Claude Code 未返回文本，请检查本机是否已登录 `claude` CLI。）"

    confirmation_requests = _extract_confirmation_requests(reply, effective_skill_id, call_id)
    created_artifact_ids = _persist_chat_artifacts(
        task_id,
        reply=reply,
        stage_key=stage_key,
        skill_id=effective_skill_id,
        substage=substage,
        call_id=call_id,
    )

    if not error:
        pack = task.get("skillPack", "sdr")
        file_artifact_ids = _try_register_substage_artifact(task_id, substage_key, substage, call_id, pack=pack)
        if file_artifact_ids:
            created_artifact_ids = list(dict.fromkeys([*created_artifact_ids, *file_artifact_ids]))

    business_status, safe_reply = _classify_business_outcome(
        reply=reply,
        error=error,
        created_artifact_ids=created_artifact_ids,
        confirmation_requests=confirmation_requests,
        substage=substage,
    )
    reply = safe_reply

    message_narrative = build_summary_agent_narrative(
        reply,
        source_type="assistant_message",
        source_id=call_id,
        severity=(
            "error"
            if business_status == "failed"
            else "blocked"
            if business_status in {"needs_input", "blocked_recoverable", "insufficient_evidence"}
            else "success"
        ),
    )

    store.append_agent_message(
        task_id,
        role="assistant",
        text=reply,
        runtime=runtime,
        capability_call_id=call_id,
        artifact_refs=created_artifact_ids,
        confirmation_requests=confirmation_requests,
        user_facing_narrative=message_narrative,
    )

    if call_id:
        failed = business_status == "failed" and runtime not in ("replay_fallback", "replay")
        call_narrative = build_presentation_agent_narrative(
            error if failed else reply,
            source_type="capability_call",
            source_id=call_id,
            artifact_refs=created_artifact_ids,
            status="failed" if failed else business_status,
        )
        store.patch_capability_call(
            task_id,
            call_id,
            status="failed" if failed else business_status,
            result_summary=error if failed else reply[:500],
            ended_at=_utc_now(),
            artifact_refs=created_artifact_ids,
            output_artifact_refs=created_artifact_ids,
            substage_instance_id=substage.get("subStageInstanceId") if substage else None,
            stage_key=stage_key,
            metadata={"userFacingNarrative": call_narrative},
        )
        _complete_runtime_context(
            task_id,
            substage=substage,
            call_id=call_id,
            status="failed" if failed else business_status,
            summary=reply[:500],
            artifact_ids=created_artifact_ids,
        )

    if mode == "creation":
        plan = _extract_task_plan_from_reply(reply)
        if plan is not None:
            patch_body: dict[str, Any] = {
                "creationPlan": plan,
                "creationStatus": "awaiting_confirmation",
            }
            chosen_pack = (plan.get("skillPack") or "").strip()
            current_pack = (task.get("skillPack") or "sdr").strip()
            if chosen_pack and chosen_pack != current_pack:
                patch_body["skillPack"] = chosen_pack
                patch_body["stageCatalog"] = build_stage_catalog(pack=chosen_pack)
                logger.info(
                    "task_id=%s skillPack switch %s -> %s, stageCatalog rebuilt",
                    task_id, current_pack, chosen_pack,
                )
            store.patch_task(task_id, patch_body)
            _save_creation_plan(task_id, plan)
        elif "readyToConfirm" in reply or '"stages"' in reply:
            logger.warning(
                "creation plan extraction failed task_id=%s reply_len=%d",
                task_id,
                len(reply),
            )

    messages = store.get_agent_messages(task_id)
    return {
        "reply": reply,
        "runtime": runtime,
        "messages": messages,
        "capabilityCallId": call_id,
        "createdArtifactIds": created_artifact_ids,
        "error": error,
    }


async def execute_skill(task_id: str, skill_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        raise KeyError("task not found")

    mode = _task_mode(task)
    if mode != "execution":
        return {
            "callId": "",
            "status": "failed",
            "summary": "当前任务不在执行态，不能触发执行类技能。",
            "runtime": "system_guard",
            "error": "execute skill blocked outside execution mode",
        }

    python_adapter_spec = resolve_python_adapter_skill(skill_id)
    canonical_skill_id = python_adapter_spec[0] if python_adapter_spec else skill_id
    dependency_block = _direct_skill_dependency_block(canonical_skill_id)
    if dependency_block is not None:
        return {
            "callId": "",
            "status": "failed",
            "summary": "该能力依赖系统生成的上游结构化输入，当前直接执行入口不会绕过该依赖。",
            "runtime": "system_guard",
            "error": dependency_block,
        }
    _skill_meta = _load_skill_meta(skill_id)
    if not _skill_meta and python_adapter_spec:
        _skill_meta = _load_skill_meta(canonical_skill_id)
    block_reason = _skill_execution_block_reason(task, _skill_meta, explicit_user_confirmed=True)
    if block_reason:
        store.patch_task(task_id, {"status": "awaiting_confirmation"})
        return {
            "callId": "",
            "status": "awaiting_confirmation",
            "summary": "该技能风险等级较高，已暂停执行，需用户确认后再继续。",
            "runtime": "risk_guard",
            "error": block_reason,
        }

    stage_key, substage_key, substage = _bind_runtime_context(
        task_id,
        task,
        skill_id,
        f"execute skill {skill_id}",
    )

    declared_substages = {
        canonical_substage_key(value)
        for value in _skill_meta.get("substages") or []
        if str(value or "").strip()
    }
    canonical_substage = canonical_substage_key(substage_key)
    if declared_substages and canonical_substage not in declared_substages:
        return {
            "callId": "",
            "status": "failed",
            "summary": "该 Skill 不适用于当前研究动作，已拒绝执行。",
            "runtime": "system_guard",
            "error": (
                f"skill substage mismatch: skill_id={canonical_skill_id} "
                f"substage={canonical_substage}"
            ),
        }

    _input_call_refs = _resolve_input_call_refs(task, _skill_meta.get("input_skills", []))

    initial_runtime = (
        "python_adapter"
        if settings.skill_exec_mode != "replay" and python_adapter_spec
        else AGENT_RUNTIME_CLAUDE_CODE
    )
    call = store.add_capability_call(
        task_id,
        skill_id=skill_id,
        status="running",
        runtime=initial_runtime,
        stage_key=stage_key,
        substage_instance_id=substage.get("subStageInstanceId") if substage else None,
        input_call_refs=_input_call_refs,
        skill_hash=_skill_hash(skill_id),
    )
    if not call:
        raise KeyError("task not found")
    call_id = call["id"]

    error: str | None = None
    summary: str
    runtime = initial_runtime
    created_artifact_ids: list[str] = []

    try:
        if settings.skill_exec_mode == "replay":
            response = replay_skill(skill_id)
            summary = response.get("summary") or "回放完成"
            runtime = "replay"
        else:
            if python_adapter_spec:
                canonical_skill_id, skill_path = python_adapter_spec
                runtime = "python_adapter"
                if canonical_skill_id == "academic_source_retrieval_skill":
                    # The retrieval Skill is intentionally standalone again: the
                    # confirmed task goal is sufficient for a fast, truthful search.
                    from app.modules.provider_transport_evidence import provider_execution_parent

                    with provider_execution_parent(
                        kind="capability_call",
                        parent_id=call_id,
                        originating_authority="legacy_direct_skill:academic_query_normalization",
                        only_if_absent=True,
                    ):
                        input_payload = build_academic_retrieval_payload(
                            task.get("goalSummary") or task.get("title") or ""
                        )
                else:
                    # This legacy direct-execute endpoint has no arbitrary parameter
                    # surface. Dependency-bearing Skills were rejected above; remaining
                    # adapters receive only the same explicit empty payload as before.
                    input_payload = {}
                _validate_python_adapter_input(skill_path, input_payload)
                output = run_python_skill_adapter(
                    repo_root=repo_root(),
                    skill_id=canonical_skill_id,
                    skill_path=skill_path,
                    input_payload=input_payload,
                )
                response = output
                if canonical_skill_id == "academic_source_retrieval_skill":
                    rr = output.get("retrieval_report", {}) if isinstance(output, dict) else {}
                    summary = (
                        "metadata retrieval completed: "
                        f"total_candidates={rr.get('total_candidates', 0)}, "
                        f"partial_success={rr.get('partial_success', False)}"
                    )
                else:
                    summary = f"{canonical_skill_id} python adapter completed"
            else:
                prompt = (
                    f"请使用 Skill「{skill_id}」完成当前任务阶段所需工作，"
                    f"并给出结构化阶段摘要。任务目标：{task.get('goalSummary') or task.get('title')}"
                )
                summary, session_id = await _run_claude_chat(
                    task,
                    prompt,
                    task_id,
                    substage_key=substage.get("subStage") if substage else None,
                    requested_skill_id=skill_id,
                    ledger_source="execute_skill",
                    capability_call_id=call_id,
                )
                if session_id:
                    store.set_agent_session_id(task_id, session_id)
                response = {"summary": summary}
    except Exception as exc:
        error = str(exc)
        summary = "当前能力未完成，系统未使用历史结果替代，也未自动重试。"
        store.patch_capability_call(
            task_id,
            call_id,
            status="failed",
            result_summary=error,
            ended_at=_utc_now(),
            substage_instance_id=substage.get("subStageInstanceId") if substage else None,
            stage_key=stage_key,
            metadata={
                "userFacingNarrative": build_presentation_agent_narrative(
                    summary,
                    source_type="capability_call",
                    source_id=call_id,
                    status="failed",
                )
            },
        )
        _complete_runtime_context(
            task_id,
            substage=substage,
            call_id=call_id,
            status="failed",
            summary=summary,
        )
        return {"callId": call_id, "status": "failed", "summary": summary, "runtime": runtime, "error": error}

    skill_meta = _load_skill_meta(skill_id)
    output_file = skill_meta.get("output_file")
    artifact_path = f"workspace/{task_id}/{output_file}" if output_file else None

    # Determine inline vs local storage for execute_skill output
    inline_content: str | None = None
    use_inline = False
    full_path = None
    if output_file:
        full_path = get_task_workspace_dir(task_id) / output_file
        if full_path.exists():
            try:
                size = full_path.stat().st_size
                if size <= _INLINE_SIZE_THRESHOLD:
                    inline_content = full_path.read_text(encoding="utf-8")
                    use_inline = True
            except (OSError, UnicodeDecodeError):
                pass

    # issue #302: a regenerate re-run is armed via ``task.pendingRegenerate``.
    # Fold the freshly generated output into the original artifact as an
    # ``agent_regenerate`` version instead of registering a duplicate — mirrors
    # ``_try_register_substage_artifact``. Only attempted when a workspace
    # output file exists to fold from; otherwise the normal registration runs.
    substage_instance_id = substage.get("subStageInstanceId") if substage else None
    folded_ids: list[str] | None = None
    if full_path:
        folded_ids = _maybe_fold_regenerated_output(
            task_id, substage_instance_id, full_path, artifact_path, inline_content, use_inline
        )
    if folded_ids:
        created_artifact_ids.extend(folded_ids)
    else:
        artifact = store.add_artifact(
            task_id,
            kind="stage_summary",
            title=f"{substage.get('label') if substage else substage_key} 输出",
            summary=summary,
            path=None if use_inline else artifact_path,
            format="markdown",
            related_stage=stage_key,
            related_skill_id=skill_id,
            substage_instance_id=substage.get("subStageInstanceId") if substage else None,
            source_capability_call_id=call_id,
            extra_fields={
                "artifactState": _artifact_state_for_skill_output(skill_meta),
                "userFacingNarrative": build_presentation_agent_narrative(
                    summary,
                    source_type="artifact",
                    source_id=None,
                    status="completed",
                ),
            },
        )
        if artifact:
            if use_inline:
                storage, ic, ext_key = _decide_storage_and_upload(
                    task_id, full_path, inline_content,
                )
                _patch_artifact_storage(
                    task_id, artifact["id"],
                    storage_type=storage, inline_content=ic, external_key=ext_key,
                )
            else:
                storage, _, ext_key = _decide_storage_and_upload(
                    task_id, full_path, None,
                )
                _patch_artifact_storage(
                    task_id, artifact["id"],
                    storage_type=storage, external_key=ext_key,
                )
            _maybe_auto_review_low_risk_artifact(task_id, artifact["id"], skill_meta)
            created_artifact_ids.append(artifact["id"])

    call_narrative = build_presentation_agent_narrative(
        summary,
        source_type="capability_call",
        source_id=call_id,
        artifact_refs=created_artifact_ids,
        status="completed",
    )
    store.patch_capability_call(
        task_id,
        call_id,
        status="completed",
        result_summary=summary[:500] if summary else None,
        ended_at=_utc_now(),
        artifact_refs=created_artifact_ids,
        output_artifact_refs=created_artifact_ids,
        substage_instance_id=substage.get("subStageInstanceId") if substage else None,
        stage_key=stage_key,
        metadata={"userFacingNarrative": call_narrative},
    )

    _complete_runtime_context(
        task_id,
        substage=substage,
        call_id=call_id,
        status="completed",
        summary=summary[:500] if summary else None,
        artifact_ids=created_artifact_ids,
    )

    store.append_agent_message(
        task_id,
        role="assistant",
        text=f"[{skill_id}] {summary}",
        runtime=runtime,
        capability_call_id=call_id,
        artifact_refs=created_artifact_ids,
        user_facing_narrative=build_summary_agent_narrative(
            summary,
            source_type="assistant_message",
            source_id=call_id,
            severity="success",
        ),
    )

    return {
        "callId": call_id,
        "status": "completed",
        "summary": summary,
        "runtime": runtime,
        "error": error,
    }
