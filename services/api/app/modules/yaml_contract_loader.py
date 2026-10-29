"""读取不允许重复键的 YAML。"""

from __future__ import annotations

from typing import Any

import yaml


class UniqueKeySafeLoader(yaml.SafeLoader):
    """只为严格合同启用重复键检查。"""


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"duplicate YAML key: {key}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_unique_key_yaml(content: str) -> Any:
    """解析 YAML，并在任意层级遇到重复键时失败。"""
    return yaml.load(content, Loader=UniqueKeySafeLoader)


__all__ = ["load_unique_key_yaml"]
