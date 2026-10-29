"""Scope-preserving query preparation for academic source retrieval.

Deterministic code only normalizes structure, bounds length, joins already-structured
context, and preserves the confirmed query. Scientific topic/dimension decomposition
belongs to the Agent/LLM semantic layer and is never re-derived from free text here.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Callable

_MAX_QUERY_VARIANTS = 4
_MAX_QUERY_CHARS = 220
_TRANSLATION_MAX_TOKENS = 320

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_WS_RE = re.compile(r"\s+")

def _clean(value: Any) -> str:
    text = _WS_RE.sub(" ", str(value or "").strip())
    return text.strip(" ，,；;。.!！？?、:/")


def _contains_cjk(value: Any) -> bool:
    return bool(_CJK_RE.search(str(value or "")))


def academic_query_translation_budget_seconds() -> float:
    """Capability-owned budget; lower transports must not shorten it."""
    raw = (
        os.environ.get("OPENAIR_ACADEMIC_QUERY_TRANSLATION_TIMEOUT_SECONDS")
        or os.environ.get("OPENAIR_LLM_PROVIDER_TIMEOUT_SECONDS")
        or "90"
    )
    try:
        value = float(raw)
    except ValueError:
        return 90.0
    return value if math.isfinite(value) and value > 0 else 90.0




def _bounded(text: str) -> str:
    text = _clean(text)
    if len(text) <= _MAX_QUERY_CHARS:
        return text
    return text[:_MAX_QUERY_CHARS].rstrip()


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _bounded(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def _with_deterministic_priorities(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach schema-required priority 1..3 by stable variant order."""

    output: list[dict[str, Any]] = []
    for index, row in enumerate(variants):
        item = dict(row)
        item["priority"] = min(index + 1, 3)
        output.append(item)
    return output


def build_academic_query_variants(user_query: str, *, max_variants: int = 3) -> list[dict[str, Any]]:
    """Return one bounded structural query without re-interpreting scientific semantics.

    ``max_variants`` remains in the public signature for compatibility. Multiple
    semantic search variants, when needed, must come from an Agent/LLM or another
    explicit structured semantic input rather than Python free-text rules.
    """

    original = _bounded(str(user_query or ""))
    if not original:
        return []

    return _with_deterministic_priorities([{
        "query": original,
        "variant_type": "confirmed_goal_fastpath",
        "search_intent": "academic_source_discovery",
    }])


def _extract_chat_content(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for row in content:
            if isinstance(row, dict) and isinstance(row.get("text"), str):
                parts.append(row["text"])
        return "\n".join(parts).strip()
    return ""


def _parse_query_json(content: str) -> list[str]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
    raw_queries = parsed.get("queries") if isinstance(parsed, dict) else None
    if not isinstance(raw_queries, list):
        return []
    queries = _dedupe([str(value or "") for value in raw_queries])[:_MAX_QUERY_VARIANTS]
    return [query for query in queries if query and not _contains_cjk(query)]


def _default_english_query_generator(user_query: str, variants: list[str]) -> list[str]:
    """Translate/normalize once via the already-configured provider.

    This is intentionally not a research/planning call. It may only preserve the
    supplied concepts and produce concise English search strings.
    """

    api_key = str(os.environ.get("LLM_PROVIDER_API_KEY") or "").strip()
    api_base = str(os.environ.get("LLM_PROVIDER_BASE_URL") or "").strip()
    model = str(
        os.environ.get("LLM_PROVIDER_MODEL_TERRA")
        or os.environ.get("LLM_PROVIDER_MODEL")
        or ""
    ).strip()
    if not api_key or not api_base or not model:
        return []

    source_queries = _dedupe([*variants, user_query])[:_MAX_QUERY_VARIANTS]
    prompt = (
        "Convert the confirmed research search queries below into concise English academic-search queries. "
        "Preserve the exact research scope and named technical entities. Do not add materials, methods, metrics, "
        "comparators, dates, or claims that are absent from the input. Return JSON only as "
        "{\"queries\":[\"...\"]}, with 2-4 distinct queries when the input supports them.\n"
        f"Confirmed query: {user_query}\n"
        f"Decomposed queries: {json.dumps(source_queries, ensure_ascii=False)}"
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a bounded academic query normalizer, not a research agent. "
                    "Translate and compress only. Never broaden scope. Output one JSON object only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": _TRANSLATION_MAX_TOKENS,
    }
    try:
        from app.modules.provider_transport_evidence import execute_observed_openai_compatible_json

        body = execute_observed_openai_compatible_json(
            protocol="chat_completions",
            base_url=api_base,
            api_key=api_key,
            timeout_seconds=academic_query_translation_budget_seconds(),
            payload=payload,
            adapter="academic_query_normalizer",
            provider="configured_openai_compatible",
            model=model,
            operation_identity="academic_query_normalization",
        )
    except Exception:
        return []
    return _parse_query_json(_extract_chat_content(body))


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _clean(value)
        return [text] if text else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean(item) for item in value if _clean(item)]
    return []


def _compose_retrieval_query(
    user_query: str,
    domain_context: list[str],
    targeted_gap_intent: list[str],
) -> str:
    """Join confirmed goal, domain context, and gap intent as extra clauses.

    Clause splitting is the existing decomposer; this does not invent query terms.
    """

    parts = [_clean(user_query), *domain_context, *targeted_gap_intent]
    return "。".join(_dedupe(parts))


def _sanitize_search_query(query: str) -> str:
    """Remove clause-join markers introduced only for internal decomposition."""

    text = str(query or "").replace("。", " ").replace("；", " ")
    return _bounded(_WS_RE.sub(" ", text).strip())


def _schema_variant_rows(
    queries: list[str],
    *,
    variant_type: str,
    search_intent: str,
) -> list[dict[str, Any]]:
    return _with_deterministic_priorities([
        {
            "query": _sanitize_search_query(query),
            "variant_type": variant_type,
            "search_intent": search_intent,
        }
        for query in queries[:_MAX_QUERY_VARIANTS]
        if _sanitize_search_query(query)
    ])


def build_english_academic_query_variants(
    user_query: str,
    *,
    english_query_generator: Callable[[str, list[str]], list[str]] | None = None,
    targeted: bool = False,
    max_variants: int = _MAX_QUERY_VARIANTS,
) -> list[dict[str, Any]]:
    """Return retrieval-ready English variants without adding research scope."""

    deterministic = build_academic_query_variants(user_query, max_variants=max_variants)
    raw_queries = [row["query"] for row in deterministic]
    search_intent = "targeted_gap" if targeted else "academic_source_discovery"
    if not any(_contains_cjk(query) for query in raw_queries):
        # confirmed_goal_fastpath is internal provenance; the Skill schema only
        # accepts targeted/fallback (and the other historical enum values).
        return _schema_variant_rows(
            raw_queries,
            variant_type="targeted",
            search_intent=search_intent,
        )

    generator = english_query_generator or _default_english_query_generator
    generated: list[str] = []
    try:
        generated = generator(_clean(user_query), raw_queries)
    except Exception:
        generated = []
    valid = [query for query in _dedupe(generated) if query and not _contains_cjk(query)]
    if not valid:
        return []
    return _schema_variant_rows(
        valid,
        variant_type="targeted",
        search_intent=search_intent,
    )


def build_academic_retrieval_payload(
    user_query: str,
    *,
    english_query_generator: Callable[[str, list[str]], list[str]] | None = None,
    domain_context: str | list[str] | None = None,
    targeted_gap_intent: str | list[str] | None = None,
) -> dict[str, Any]:
    """Build the shared direct-retrieval payload used by API and durable dispatch."""

    confirmed_query = str(user_query or "").strip()
    query = _clean(confirmed_query)
    context_items = _as_text_list(domain_context)
    gap_items = _as_text_list(targeted_gap_intent)
    composite = _compose_retrieval_query(query, context_items, gap_items)
    extra_clauses = bool(context_items or gap_items)
    variants = build_english_academic_query_variants(
        composite,
        english_query_generator=english_query_generator,
        targeted=bool(gap_items),
        max_variants=_MAX_QUERY_VARIANTS if extra_clauses else 3,
    )
    retrieval_user_query = variants[0]["query"] if variants else confirmed_query
    return {
        "user_query": retrieval_user_query,
        "search_matrix": [],
        "expanded_query_variants": variants,
        "retrieval_config": {
            "target_sources": ["openalex", "semantic_scholar", "arxiv"],
            "max_results_per_query": 10,
            "max_total_candidates": 120,
            "timeout_seconds": 45,
            "allow_partial_success": True,
        },
    }
