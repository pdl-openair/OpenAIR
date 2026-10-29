"""Repository path helpers (no fixture loading)."""

from __future__ import annotations

import os
from pathlib import Path

_repo_root_cache: Path | None = None


def repo_root() -> Path:
    """Return the repository root directory.

    Works in both the API server and the Worker container:
    - API server: app/modules/repo_paths.py → parents[4]
    - Worker container: /app/app/modules/repo_paths.py → /app
    - Environment override: OPENAIR_REPO_ROOT
    """
    global _repo_root_cache
    if _repo_root_cache is not None:
        return _repo_root_cache

    # Allow explicit override
    env_root = os.environ.get("OPENAIR_REPO_ROOT", "").strip()
    if env_root:
        _repo_root_cache = Path(env_root).resolve()
        return _repo_root_cache

    here = Path(__file__).resolve()

    # Try ascending parents to find the repo root marker
    for parent in here.parents:
        # API server layout: repo_root/services/api/app/modules/repo_paths.py
        if (parent / "services" / "api" / "app" / "modules").is_dir():
            _repo_root_cache = parent
            return _repo_root_cache
        # Worker container layout: /app/app/modules/repo_paths.py
        if (parent / "app" / "modules").is_dir() and (parent / ".claude").is_dir():
            _repo_root_cache = parent
            return _repo_root_cache
        # Worker container flat: /app is the root
        if (parent / "schemas").is_dir() and (parent / ".claude").is_dir():
            _repo_root_cache = parent
            return _repo_root_cache

    # Fallback: use the highest reasonable parent
    # Worker: /app/app/modules → parents[1] = /app
    # API: app/modules → parents[4] = repo root
    if len(here.parents) > 4:
        _repo_root_cache = here.parents[4]
    elif len(here.parents) > 1:
        _repo_root_cache = here.parents[1]  # /app for worker container
    else:
        _repo_root_cache = here.parent

    return _repo_root_cache


def backend_app_root() -> Path:
    """Return the source root of the backend ``app`` package actually loaded.

    ``repo_root()`` remains authority for repository-owned resources such as
    skills and schemas. Backend code may be deployed in a flattened service
    image, so runtime code identity and service-local resources must follow the
    imported package rather than reconstructing the checkout layout.
    """
    import app as app_package

    package_file = getattr(app_package, "__file__", None)
    if not isinstance(package_file, str) or not package_file.strip():
        raise RuntimeError("OpenAIR backend package source root is unavailable")
    root = Path(package_file).resolve().parent
    if not (root / "__init__.py").is_file():
        raise RuntimeError("OpenAIR backend package source root is invalid")
    return root


def api_service_root() -> Path:
    """Return the API service root containing ``app`` and service resources."""
    return backend_app_root().parent
