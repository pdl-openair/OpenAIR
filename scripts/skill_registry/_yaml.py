"""PyYAML 包装：未安装时给出友好提示后再退出。

避免每个模块各自 try/except，同时让脚本在缺依赖时仍能给出明确指引。
"""

from __future__ import annotations

import sys
from typing import Any

try:  # pragma: no cover - 仅在缺依赖时触发
    import yaml as _yaml
except ImportError:  # pragma: no cover - 仅在缺依赖时触发
    sys.stderr.write(
        "error: 缺少 PyYAML 依赖。\n"
        "  请运行: uv add pyyaml  （或 pip install pyyaml）\n"
    )
    raise SystemExit(2)


def yaml_safe_load(text: str) -> Any:
    """安全加载 YAML 文本。空文本返回 None。"""
    return _yaml.safe_load(text)


def yaml_safe_dump(data: Any) -> str:
    """以稳定、可读的方式序列化 YAML。"""
    return _yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
