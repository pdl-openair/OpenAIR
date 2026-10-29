"""Reuse parsing of unchanged source bytes without caching runtime authority."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@lru_cache(maxsize=512)
def _parse(text: str, loader: type) -> Any:
    return yaml.load(text, Loader=loader)


def read_yaml_source(path: Path, *, loader: type = SAFE_LOADER) -> Any:
    # Read each time: even same-size edits with unchanged mtimes must invalidate.
    # Return a copy so callers cannot mutate another request's source facts.
    return deepcopy(_parse(path.read_text(encoding="utf-8"), loader))
