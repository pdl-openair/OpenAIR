"""Bounded reception of scientific prose before formal Artifact assembly."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def scientific_texts(value: object) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return list(
        dict.fromkeys(item.strip() for item in values if isinstance(item, str) and item.strip())
    )


def receive_texts(
    payload: Mapping[str, Any],
    canonical: str,
    notes: list[str],
    *aliases: str,
    positional: bool = False,
) -> list[str]:
    """Preserve conflicting expressions as prose, never adjudicate their meaning."""
    expressions: list[tuple[str, list[str]]] = []
    for key in (canonical, *aliases):
        if key not in payload:
            continue
        value = payload[key]
        texts = scientific_texts(value)
        if positional and isinstance(value, list):
            texts = [item.strip() if isinstance(item, str) else "" for item in value]
        valid = isinstance(value, str) or (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        )
        if not valid and value is not None:
            notes.append(
                f"规划内容 {key} 的表达未能映射，原值：{json.dumps(value, ensure_ascii=False)}"
            )
        expressions.append((key, texts))
    if len({tuple(texts) for _, texts in expressions}) > 1:
        notes.append(
            f"规划内容 {canonical} 存在不同表达，尚未裁决："
            + json.dumps({key: texts for key, texts in expressions}, ensure_ascii=False)
        )
        if positional:
            # Conflicting positions cannot be assigned to a responsibility by code.
            return []
    merged = [text for _, texts in expressions for text in texts]
    if positional and expressions:
        return expressions[0][1]
    return list(dict.fromkeys(merged))


def receive_goal(payload: Mapping[str, Any], notes: list[str]) -> str:
    values = receive_texts(payload, "researchGoal", notes, "goal")
    if len(values) <= 1:
        return values[0] if values else ""
    notes.append(
        "规划目标存在多个表达，保留原始研究请求作为目标；待核对内容："
        + json.dumps(values, ensure_ascii=False)
    )
    return ""


def indexed_target_outputs(value: object) -> tuple[list[str], dict[int, int]]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    outputs: list[str] = []
    numbers: dict[int, int] = {}
    for original, item in enumerate(values, 1):
        if not isinstance(item, str) or not item.strip():
            continue
        text = item.strip()
        if text not in outputs:
            outputs.append(text)
        numbers[original] = outputs.index(text) + 1
    return outputs, numbers


def remap_target_numbers(
    value: object,
    numbers: Mapping[int, int],
    notes: list[str],
    *,
    context: str,
) -> list[int]:
    if not isinstance(value, list):
        if value is not None:
            notes.append(f"{context} 的目标引用未能映射：{json.dumps(value, ensure_ascii=False)}")
        return []
    mapped: list[int] = []
    invalid: list[Any] = []
    for number in value:
        if isinstance(number, int) and not isinstance(number, bool) and number in numbers:
            mapped.append(numbers[number])
        else:
            invalid.append(number)
    if invalid:
        notes.append(f"{context} 的目标引用未能映射：{json.dumps(invalid, ensure_ascii=False)}")
    return list(dict.fromkeys(mapped))


def coalesce_responsibility_deltas(
    deltas: Sequence[Any],
    valid_choices: set[str],
    notes: list[str],
) -> dict[str, Any]:
    """Join repeated contexts without choosing between conflicting reuse sources."""
    joined: dict[str, Any] = {}
    for delta in deltas:
        choice = delta.responsibility_choice.strip()
        if choice not in valid_choices:
            notes.append(
                "职责内容未能映射，原内容："
                + json.dumps(delta.model_dump(mode="json", by_alias=True), ensure_ascii=False)
            )
            continue
        if choice not in joined:
            joined[choice] = delta.model_copy(deep=True)
            continue
        previous = joined[choice]
        contexts = list(
            dict.fromkeys(
                [
                    *(previous.work_item_scientific_contexts or []),
                    *(delta.work_item_scientific_contexts or []),
                ]
            )
        )
        reuse = previous.reuse_candidate_key
        if reuse != delta.reuse_candidate_key or (reuse and contexts):
            notes.append(
                "同一职责的复用表达未裁决，保留当前注册工作与原表达："
                + json.dumps(
                    [
                        previous.model_dump(mode="json", by_alias=True),
                        delta.model_dump(mode="json", by_alias=True),
                    ],
                    ensure_ascii=False,
                )
            )
            reuse = None
        joined[choice] = previous.model_copy(
            update={
                "work_item_scientific_contexts": contexts or None,
                "reuse_candidate_key": reuse,
                "target_output_numbers": list(
                    dict.fromkeys(
                        [
                            *previous.target_output_numbers,
                            *delta.target_output_numbers,
                        ]
                    )
                ),
            }
        )
    return joined
