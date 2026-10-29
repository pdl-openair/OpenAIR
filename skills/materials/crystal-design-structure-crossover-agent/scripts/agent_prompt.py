# scripts/agent_prompt.py
"""Build the natural-language prompt the agent (Claude) reads between
generations.

Output contract: the agent emits a JSON object of shape
  {"action": "ban|constraint|noise|crossover|noop",
   "args":  {...},
   "reason": "<one sentence>"}

This file owns prompt assembly only. Memory retrieval, snapshot
loading, and steer execution live elsewhere.
"""
from __future__ import annotations

import json
from typing import Any

from memory_store import MemoryEntry


_PROMPT_HEADER = """You are the Materials Design Agent. Read the current generation's snapshot, \
consider the retrieved memories (if any), and emit ONE steer action.

FAMILY: {family}
GENERATION: {gen}
PARETO SIZE: {pareto_size}
SUN COUNT: {sun_count}
CURRENT CONSTRAINTS: {constraints_json}

SNAPSHOT SUMMARY:
{snapshot_json}
"""

_MEMORY_SECTION = """

RETRIEVED MEMORIES (most similar past decisions):
{memory_block}
"""

_OUTPUT_CONTRACT = """

STEER ACTION — emit a single JSON object on the last line. Schema:
  {"action": "ban"|"constraint"|"noise"|"crossover"|"noop",
   "args":   {"<key>": <value>, ...},
   "reason": "<one sentence>"}

Examples:
  {"action": "ban",       "args": {"element": "Au"},                      "reason": "single-element collapse detected"}
  {"action": "noise",     "args": {"level": 0.25},                        "reason": "stable count saturated, lower noise to diversify"}
  {"action": "constraint", "args": {"key": "num_elements", "min": 2, "weight": 10}, "reason": "anti-collapse hardening"}
  {"action": "noop",      "args": {},                                     "reason": "front healthy"}
"""


def _format_memory_block(entries: list[MemoryEntry]) -> str:
    if not entries:
        return "(none)"
    lines: list[str] = []
    for i, e in enumerate(entries, start=1):
        outcome_str = ", ".join(f"{k}={v:+.3f}" for k, v in e.outcome.items())
        lines.append(
            f"{i}. [conf={e.confidence:.2f} run={e.run_id} gen={e.gen}] "
            f"CONTEXT: {e.context} | ACTION: {e.action} | OUTCOME: {outcome_str}"
        )
    return "\n".join(lines)


def build_agent_prompt(
    snapshot: dict[str, Any],
    family: str,
    retrieved: list[MemoryEntry],
    constraints: dict[str, Any],
) -> str:
    header = _PROMPT_HEADER.format(
        family=family,
        gen=snapshot.get("generation", "?"),
        pareto_size=snapshot.get("pareto_size", "?"),
        sun_count=snapshot.get("sun_count", "?"),
        constraints_json=json.dumps(constraints, ensure_ascii=False),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, indent=2),
    )
    return (
        header
        + _MEMORY_SECTION.format(memory_block=_format_memory_block(retrieved))
        + _OUTPUT_CONTRACT
    )


def build_agent_prompt_no_memory(
    snapshot: dict[str, Any],
    family: str,
    constraints: dict[str, Any],
) -> str:
    header = _PROMPT_HEADER.format(
        family=family,
        gen=snapshot.get("generation", "?"),
        pareto_size=snapshot.get("pareto_size", "?"),
        sun_count=snapshot.get("sun_count", "?"),
        constraints_json=json.dumps(constraints, ensure_ascii=False),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, indent=2),
    )
    return header + _OUTPUT_CONTRACT
