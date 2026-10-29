"""Project contributions use existing multi-instance target coverage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def attach_query_contributors(
    *,
    brief: Any,
    owners: dict[str, str],
    contributors: Mapping[int, list[str]],
    work_keys_by_node: Mapping[str, list[str]],
    work_items: Mapping[str, dict[str, Any]],
) -> dict[str, list[str]]:
    from app.modules.target_output_ownership import _mapping_error

    frozen_owners = dict(owners)
    combined: dict[str, list[str]] = {}
    for number, output in enumerate(brief.target_outputs, 1):
        nodes = contributors.get(number, [])
        if output in frozen_owners:
            if any(node != frozen_owners[output] for node in nodes):
                raise _mapping_error("Query association conflicts with frozen output ownership")
            continue
        if not nodes:
            brief.content_notes.append(f"目标尚无明确科研工作关联，保留待核对：{output}")
            continue
        if len(nodes) > 1:
            combined[output] = list(nodes)
        if len(nodes) == 1:
            owners[output] = nodes[0]
        for node in nodes:
            for key in work_keys_by_node[node]:
                refs = work_items[key]["targetOutputRefs"]
                if output not in refs:
                    refs.append(output)
    return combined
