"""Download user-private skills into the worker via presigned-manifest delivery.

Phase-1 security model: the worker holds **no** MinIO credentials. At launch the API
signs short-lived, object-scoped presigned GET URLs for THIS owner's own skill files
only, and bakes them into the ``OPENAIR_USER_SKILLS_MANIFEST`` env var. We fetch each
URL and reconstruct the skill tree under ``OPENAIR_SKILLS_USER_DIR`` (default
``/app/.claude/user-skills/``).

Because every URL is a single-object presigned GET scoped to this owner's prefix, a
malicious user skill running inside this worker cannot enumerate or read another
tenant's skills (no root key, no list capability, no owner-header-spoofable endpoint).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60

# Single safe path segment — mirrors services/api/app/modules/user_skills_service._SKILL_NAME_RE.
# The skill name becomes ``user_dir / name``; anything allowing "/", "..", or a leading
# dash would be a path-traversal vector from a forged manifest or a historical dirty
# object key. Defense-in-depth alongside the per-file path guard below.
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def download_user_skills(
    owner_id: str,
    skill_names: list[str] | None = None,  # noqa: ARG001 — kept for call-site compat; manifest is the source of truth
) -> list[str]:
    """Fetch the owner's skills via the presigned manifest.

    Returns the list of skill names that were successfully fetched AND have a
    ``SKILL.md`` (i.e. usable by the Claude SDK). The ``skill_names`` argument is
    ignored — the manifest already contains only the skills the API selected for this
    owner/turn.
    """
    user_dir = Path(os.environ.get("OPENAIR_SKILLS_USER_DIR", "/app/.claude/user-skills"))
    user_dir.mkdir(parents=True, exist_ok=True)

    raw = os.environ.get("OPENAIR_USER_SKILLS_MANIFEST", "").strip()
    if not raw:
        logger.info("user skills: no manifest present (owner=%s), skipping download", owner_id)
        return []

    try:
        manifest: list[dict[str, Any]] = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("user skills: invalid manifest JSON: %s", exc)
        return []

    downloaded: list[str] = []
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as http:
        for skill in manifest:
            name = skill.get("name")
            files = skill.get("files") or []
            if not name or not files:
                continue

            # Skill-name guard: ``name`` becomes a path component (user_dir / name), so
            # reject anything that isn't a single safe segment. A forged manifest (or a
            # historical dirty object key that slipped in before upload validation) must
            # not traverse out of the user-skills dir. Mirrors the API-side allowlist.
            if not _SKILL_NAME_RE.match(name):
                logger.warning("user skills: refusing unsafe skill name %r", name)
                continue

            skill_dir = user_dir / name
            has_skill_md = False

            for entry in files:
                path = entry.get("path")
                url = entry.get("url")
                if not path or not url:
                    continue
                # Path-traversal guard: manifest paths must be relative, single-tree,
                # no parent segments. (They originate from MinIO keys under this
                # owner's prefix, so this is defense-in-depth against a forged manifest.)
                if path.startswith("/") or "\\" in path or any(
                    seg in {"", ".."} for seg in path.split("/")
                ):
                    logger.warning("user skills: refusing unsafe path %r in skill %s", path, name)
                    continue

                dest = skill_dir / path
                try:
                    resp = http.get(url)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("user skills: fetch failed %s: %s", path, exc)
                    continue
                if resp.status_code >= 300:
                    logger.warning("user skills: fetch %s → HTTP %s", path, resp.status_code)
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                if path.lower() == "skill.md":
                    has_skill_md = True
                logger.info("user skills: fetched %s → %s", path, dest)

            if has_skill_md:
                downloaded.append(name)
                logger.info("user skills: skill '%s' ready at %s", name, skill_dir)
            else:
                logger.warning("user skills: skill '%s' has no SKILL.md after fetch, skipping", name)

    return downloaded
