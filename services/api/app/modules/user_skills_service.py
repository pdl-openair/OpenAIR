"""User-private skills storage service.

Stores user skill files in MinIO under the prefix:
    ``user-skills/{owner_id}/{skill_name}/SKILL.md``
    ``user-skills/{owner_id}/{skill_name}/skill.yaml``
    ...

Provides CRUD operations for the skills API routes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import UploadFile

from app.config import settings
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

_MINIO_PREFIX = "user-skills"

# Strict skill-name allowlist (applied AFTER legacy coerce of path separators/space).
# Rejects path traversal (".", ".."), absolute-ish names, and over-long names. The
# name becomes a MinIO path segment (``user-skills/{owner}/{name}/``) so it must be a
# single safe component.
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _validate_skill_name(raw: str) -> str:
    """Coerce legacy separators then enforce a strict allowlist; raise ValueError otherwise."""
    safe = raw.strip().replace("/", "-").replace("\\", "-").replace(" ", "-")
    if not safe or not _SKILL_NAME_RE.match(safe):
        raise ValueError(
            "invalid skill_name: use 1-64 of [A-Za-z0-9_-], starting alphanumeric"
        )
    return safe


def _safe_rel_path(raw: str) -> str:
    """Normalise an uploaded file's relative path, PRESERVING subdir structure.

    A skill is a directory tree (``SKILL.md`` + ``scripts/`` + ``schemas/`` …); flattening
    it to basenames would break code-based skills. We therefore keep the relative path but
    reject anything that could escape the skill prefix: absolute paths and any ``..``
    segment (traversal). Backslashes are normalised to ``/`` so a Windows client's tree
    survives intact. The result is joined under ``user-skills/{owner}/{name}/``.
    """
    rel = (raw or "").replace("\\", "/")
    if not rel or rel.startswith("/"):
        raise ValueError(f"unsafe file path: {raw!r}")
    parts: list[str] = []
    for seg in rel.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ValueError(f"unsafe file path (traversal): {raw!r}")
        parts.append(seg)
    if not parts:
        raise ValueError(f"unsafe file path: {raw!r}")
    return "/".join(parts)


def is_minio_enabled() -> bool:
    """Read MinIO enable flag without hard importing optional MinIO deps."""
    try:
        from app.modules.minio_client import is_minio_enabled as _impl
    except Exception:
        return False
    return _impl()


def _ensure_minio() -> tuple[Any, str]:
    """Return (minio_client, bucket_name) or raise."""
    from app.modules.minio_client import get_minio_client

    client = get_minio_client()
    bucket = settings.minio_bucket
    return client, bucket


def _remove_prefix_objects(client: Any, bucket: str, prefix: str) -> int:
    """Delete every object under ``prefix``. Returns the count removed.

    Shared by ``delete_skill`` and ``upload_skill``'s re-upload cleanup: a re-upload of
    the same skill name wipes stale files the new upload no longer includes, so a script
    the user deleted does not keep being distributed/executed off the old prefix.
    """
    removed = 0
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        client.remove_object(bucket, obj.object_name)
        removed += 1
    return removed


def upload_skill(owner_id: str, skill_name: str, files: list[UploadFile]) -> dict[str, Any]:
    """Upload a user-private skill to MinIO.

    Parameters
    ----------
    owner_id : str
        User ID.
    skill_name : str
        Skill directory name (e.g. "my-custom-skill").
    files : list[UploadFile]
        Must include SKILL.md at minimum.

    Returns
    -------
    dict with skill metadata.
    """
    if not is_minio_enabled():
        raise ValueError("User skill storage is disabled (artifact storage is not minio)")

    # Validate name + count BEFORE touching MinIO (fail fast, no partial upload).
    safe_name = _validate_skill_name(skill_name)
    if len(files) > settings.user_skill_max_files:
        raise ValueError(
            f"too many files ({len(files)} > max {settings.user_skill_max_files}; "
            "raise OPENAIR_USER_SKILL_MAX_FILES or trim the skill folder)"
        )

    # Pre-validate EVERY entry (relative path + per-file + total size) and buffer it,
    # so a bad file aborts atomically — no half-written skill lands in object storage.
    # Subdirectory structure is preserved (a skill is a tree, not a flat file list).
    entries: list[tuple[str, bytes, str]] = []  # (safe_rel_path, data, content_type)
    total_bytes = 0
    for upload_file in files:
        if not upload_file.filename:
            continue
        safe_rel = _safe_rel_path(upload_file.filename)

        declared = getattr(upload_file, "size", None)
        if declared is not None and declared > settings.user_skill_max_file_bytes:
            raise ValueError(
                f"file {safe_rel} exceeds size limit ({settings.user_skill_max_file_bytes} bytes)"
            )
        file_data = upload_file.file.read()
        if len(file_data) > settings.user_skill_max_file_bytes:
            raise ValueError(
                f"file {safe_rel} exceeds size limit ({settings.user_skill_max_file_bytes} bytes)"
            )
        total_bytes += len(file_data)
        if total_bytes > settings.user_skill_max_total_bytes:
            raise ValueError(
                f"skill exceeds total size limit ({settings.user_skill_max_total_bytes} bytes)"
            )
        entries.append((safe_rel, file_data, upload_file.content_type or "application/octet-stream"))

    if not any(rel.lower() == "skill.md" for rel, _, _ in entries):
        raise ValueError("SKILL.md is required (at the skill root)")

    client, bucket = _ensure_minio()
    prefix = f"{_MINIO_PREFIX}/{owner_id}/{safe_name}/"

    # Re-upload cleanup: wipe stale objects under this skill's prefix so files absent
    # from the new upload (e.g. a script the user deleted) don't keep being distributed
    # and executed. All new files are already buffered above (validation precedes this),
    # so deleting here cannot corrupt a partially-written new skill.
    stale = _remove_prefix_objects(client, bucket, prefix)
    if stale:
        logger.info("re-upload wiped %d stale file(s) under %s", stale, prefix)

    uploaded_files: list[str] = []
    for safe_rel, file_data, content_type in entries:
        object_key = f"{prefix}{safe_rel}"
        client.put_object(
            bucket,
            object_key,
            data=_bytes_reader(file_data),
            length=len(file_data),
            content_type=content_type,
        )
        uploaded_files.append(safe_rel)
        logger.info("uploaded user skill file: %s (%d bytes)", object_key, len(file_data))

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "skill_name": safe_name,
        "description": "",
        "created_at": now,
        "files": uploaded_files,
    }


def list_skills(owner_id: str) -> list[dict[str, Any]]:
    """List all user-private skills for a given owner.

    Returns a list of skill metadata dicts.
    """
    if not is_minio_enabled():
        return []

    client, bucket = _ensure_minio()
    prefix = f"{_MINIO_PREFIX}/{owner_id}/"

    objects = list(client.list_objects(bucket, prefix=prefix, recursive=False))
    skills: list[dict[str, Any]] = []

    # Each "directory" (common prefix) is a skill
    seen_skills: set[str] = set()
    for obj in objects:
        # obj.object_name = "user-skills/{owner_id}/{skill_name}/"
        parts = obj.object_name[len(prefix):].split("/")
        if parts:
            skill_name = parts[0]
            if skill_name and skill_name not in seen_skills:
                seen_skills.add(skill_name)
                # List files within this skill to get metadata
                skill_files = _list_skill_files(client, bucket, owner_id, skill_name)
                skills.append({
                    "skill_name": skill_name,
                    "description": "",
                    "created_at": "",
                    "files": skill_files,
                })

    return skills


def list_user_skill_names(owner_id: str) -> list[str]:
    """Return just the skill names for an owner (cheap non-recursive list).

    Used to auto-populate ``ExecutionRequest.user_skill_names`` so the agent can use
    the owner's private skills. No-op (returns ``[]``) when MinIO is disabled.
    """
    if not is_minio_enabled():
        return []

    client, bucket = _ensure_minio()
    prefix = f"{_MINIO_PREFIX}/{owner_id}/"
    objects = list(client.list_objects(bucket, prefix=prefix, recursive=False))

    names: list[str] = []
    for obj in objects:
        parts = obj.object_name[len(prefix):].split("/")
        if parts and parts[0] and parts[0] not in names:
            names.append(parts[0])
    return names


def build_presigned_skills_manifest(
    owner_id: str,
    skill_names: list[str] | None,
    *,
    ttl_seconds: int,
) -> list[dict[str, Any]]:
    """Build an object-scoped presigned-GET manifest for the owner's OWN skills.

    Returns ``[{name, files:[{path, url}]}]`` where each ``url`` is a short-lived
    presigned GET for ONE of this owner's skill-file object keys. This is the Phase-1
    delivery mechanism: the worker fetches its skills via these URLs and holds **no**
    MinIO root key, and there is no owner-header-spoofable endpoint — so a malicious
    user skill cannot enumerate or read another tenant's skills. Skills lacking
    ``SKILL.md`` are skipped; the skill count is capped at
    ``settings.user_skill_manifest_max_skills`` (overflow logged, not silent). The
    serialized manifest is then bounded by ``user_skill_manifest_max_files`` /
    ``user_skill_manifest_max_bytes`` — exceeding either raises ValueError so the caller
    (``_serialise_env``) skips the manifest rather than baking an oversized env var that
    can break worker startup.
    """
    if not is_minio_enabled():
        return []

    from app.modules import minio_client

    client, bucket = _ensure_minio()
    prefix = f"{_MINIO_PREFIX}/{owner_id}/"
    objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
    if not objects:
        return []

    # Group object keys by skill name → {rel_path: object_key}
    grouped: dict[str, dict[str, str]] = {}
    for obj in objects:
        rel = obj.object_name[len(prefix):]
        parts = rel.split("/", 1)
        if len(parts) < 2:
            continue
        name, file_path = parts[0], parts[1]
        # Defense-in-depth against historical dirty data: the name becomes a path
        # segment on the worker (user_dir / name). Reject any name that isn't a single
        # safe component BEFORE signing a presigned URL for it, so traversal-y keys left
        # by a pre-validation upload can't reach the worker.
        try:
            _validate_skill_name(name)
        except ValueError:
            logger.warning(
                "user skills manifest: skipping dirty skill name %r (owner=%s)", name, owner_id,
            )
            continue
        if skill_names and name not in skill_names:
            continue
        grouped.setdefault(name, {})[file_path] = obj.object_name

    selected = sorted(grouped)
    cap = settings.user_skill_manifest_max_skills
    if len(selected) > cap:
        logger.warning(
            "user skills manifest: owner=%s has %d skills, capping to %d",
            owner_id, len(selected), cap,
        )
        selected = selected[:cap]

    manifest: list[dict[str, Any]] = []
    for name in selected:
        files_map = grouped[name]
        if not any(k.lower() == "skill.md" for k in files_map):
            logger.info("user skills manifest: skipping %s (no SKILL.md)", name)
            continue
        files: list[dict[str, str]] = []
        for rel in sorted(files_map):
            url = minio_client.presigned_get_object(files_map[rel], expires_seconds=ttl_seconds)
            files.append({"path": rel, "url": url})
        manifest.append({"name": name, "files": files})

    # Hard caps on the serialized manifest baked into worker env. An unbounded single
    # env var can exceed Docker/process env limits and break worker startup, so on
    # overflow we raise — _serialise_env catches this and skips the manifest (turn
    # degrades to no user skills) rather than shipping an oversized env var.
    total_files = sum(len(s["files"]) for s in manifest)
    if total_files > settings.user_skill_manifest_max_files:
        raise ValueError(
            f"user skills manifest file count {total_files} exceeds cap "
            f"{settings.user_skill_manifest_max_files} (owner={owner_id})"
        )
    serialized_size = len(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    if serialized_size > settings.user_skill_manifest_max_bytes:
        raise ValueError(
            f"user skills manifest size {serialized_size} bytes exceeds cap "
            f"{settings.user_skill_manifest_max_bytes} (owner={owner_id})"
        )
    return manifest


def get_skill(owner_id: str, skill_name: str) -> dict[str, Any] | None:
    """Get details of a specific user-private skill."""
    if not is_minio_enabled():
        return None

    client, bucket = _ensure_minio()
    prefix = f"{_MINIO_PREFIX}/{owner_id}/{skill_name}/"

    objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
    if not objects:
        return None

    files = [obj.object_name[len(prefix):] for obj in objects]
    skill_yaml: dict[str, Any] | None = None
    skill_md_preview = ""

    # Read skill.yaml if present
    for obj in objects:
        rel = obj.object_name[len(prefix):]
        if rel == "skill.yaml":
            try:
                response = client.get_object(bucket, obj.object_name)
                data = response.read()
                response.close()
                response.release_conn()
                import yaml
                skill_yaml = yaml.safe_load(data)
            except Exception as exc:
                logger.warning("failed to read skill.yaml for %s/%s: %s", owner_id, skill_name, exc)

        if rel.lower() == "skill.md":
            try:
                response = client.get_object(bucket, obj.object_name)
                data = response.read().decode("utf-8", errors="replace")
                response.close()
                response.release_conn()
                # Preview: first 500 chars
                skill_md_preview = data[:500]
            except Exception as exc:
                logger.warning("failed to read SKILL.md for %s/%s: %s", owner_id, skill_name, exc)

    return {
        "skill_name": skill_name,
        "description": (skill_yaml or {}).get("description", ""),
        "created_at": "",
        "files": files,
        "skill_yaml": skill_yaml,
        "skill_md_preview": skill_md_preview,
    }



def _read_object_bytes(client: Any, bucket: str, object_key: str) -> bytes:
    response = client.get_object(bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _parse_skill_frontmatter(document: str) -> dict[str, Any] | None:
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    payload: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            try:
                import yaml

                parsed = yaml.safe_load("\n".join(payload)) or {}
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        payload.append(line)
    return None


def _schema_required_fields_from_bytes(data: bytes) -> tuple[list[str], str | None]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], "Schema 不是有效的 JSON"
    if not isinstance(payload, dict):
        return [], "Schema 顶层必须是对象"
    required = payload.get("required", [])
    if not isinstance(required, list) or any(not isinstance(value, str) for value in required):
        return [], "Schema 的 required 必须是字符串数组"
    return [value for value in required if value.strip()], None


def validate_user_skill_for_publication(owner_id: str, skill_name: str) -> dict[str, Any]:
    """Run the bounded structural checks required before human review/publication.

    This check proves only that the uploaded resource has a readable Skill contract and
    valid optional schemas. It intentionally does not claim scientific correctness or
    safe execution; human review and runtime admission remain separate gates.
    """
    issues: list[str] = []
    try:
        safe_name = _validate_skill_name(skill_name)
    except ValueError:
        return {
            "status": "needs_attention",
            "issues": ["资源名称不符合发布规则"],
            "name": skill_name,
            "description": "",
            "input": [],
            "output": [],
            "files": [],
        }
    if not is_minio_enabled():
        return {
            "status": "needs_attention",
            "issues": ["资源存储未启用，暂时无法完成系统检查"],
            "name": safe_name,
            "description": "",
            "input": [],
            "output": [],
            "files": [],
        }

    client, bucket = _ensure_minio()
    prefix = f"{_MINIO_PREFIX}/{owner_id}/{safe_name}/"
    objects = sorted(
        client.list_objects(bucket, prefix=prefix, recursive=True),
        key=lambda obj: obj.object_name,
    )
    if not objects:
        return {
            "status": "needs_attention",
            "issues": ["未找到已上传的资源，请先重新上传"],
            "name": safe_name,
            "description": "",
            "input": [],
            "output": [],
            "files": [],
        }

    by_rel = {obj.object_name[len(prefix):]: obj for obj in objects}
    files = sorted(by_rel)
    skill_md_key = next((rel for rel in files if rel.lower() == "skill.md"), None)
    metadata: dict[str, Any] | None = None
    description = ""
    display_name = safe_name
    if skill_md_key is None:
        issues.append("缺少根目录 SKILL.md")
    else:
        try:
            document = _read_object_bytes(client, bucket, by_rel[skill_md_key].object_name).decode("utf-8")
        except (UnicodeDecodeError, OSError, RuntimeError):
            issues.append("SKILL.md 无法按 UTF-8 读取")
        else:
            metadata = _parse_skill_frontmatter(document)
            if metadata is None:
                issues.append("SKILL.md 缺少有效的 YAML frontmatter")
            else:
                raw_name = metadata.get("name")
                raw_description = metadata.get("description")
                if not isinstance(raw_name, str) or not raw_name.strip():
                    issues.append("SKILL.md frontmatter 缺少 name")
                else:
                    display_name = raw_name.strip()
                if not isinstance(raw_description, str) or not raw_description.strip():
                    issues.append("SKILL.md frontmatter 缺少 description")
                else:
                    description = raw_description.strip()

    inputs: list[str] = []
    outputs: list[str] = []
    for rel, target in (("input.schema.json", inputs), ("output.schema.json", outputs)):
        obj = by_rel.get(rel)
        if obj is None:
            continue
        fields, schema_error = _schema_required_fields_from_bytes(
            _read_object_bytes(client, bucket, obj.object_name)
        )
        if schema_error:
            issues.append(f"{rel}：{schema_error}")
        else:
            target.extend(fields)

    return {
        "status": "passed" if not issues else "needs_attention",
        "issues": issues,
        "name": display_name,
        "description": description,
        "input": inputs,
        "output": outputs,
        "files": files,
    }


def publish_user_skill_snapshot(
    owner_id: str,
    skill_name: str,
    contribution_id: str,
) -> dict[str, Any]:
    """Freeze the reviewed private Skill bytes under an immutable public prefix."""
    check = validate_user_skill_for_publication(owner_id, skill_name)
    if check["status"] != "passed":
        raise ValueError("system check failed: " + "; ".join(check["issues"]))

    client, bucket = _ensure_minio()
    private_prefix = f"{_MINIO_PREFIX}/{owner_id}/{_validate_skill_name(skill_name)}/"
    public_prefix = f"published-skills/{contribution_id}/"
    objects = sorted(
        client.list_objects(bucket, prefix=private_prefix, recursive=True),
        key=lambda obj: obj.object_name,
    )
    if not objects:
        raise ValueError("private resource disappeared before publication")

    _remove_prefix_objects(client, bucket, public_prefix)
    digest = hashlib.sha256()
    published_files: list[str] = []
    try:
        for obj in objects:
            rel = obj.object_name[len(private_prefix):]
            data = _read_object_bytes(client, bucket, obj.object_name)
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(data)
            client.put_object(
                bucket,
                f"{public_prefix}{rel}",
                data=_bytes_reader(data),
                length=len(data),
                content_type=getattr(obj, "content_type", None) or "application/octet-stream",
            )
            published_files.append(rel)
    except Exception:
        _remove_prefix_objects(client, bucket, public_prefix)
        raise

    return {
        "snapshotPrefix": public_prefix,
        "files": published_files,
        "name": check["name"],
        "description": check["description"],
        "input": check["input"],
        "output": check["output"],
        "releaseVersion": f"sha256:{digest.hexdigest()[:16]}",
    }


def delete_published_skill_snapshot(contribution_id: str) -> None:
    if not is_minio_enabled():
        return
    client, bucket = _ensure_minio()
    _remove_prefix_objects(client, bucket, f"published-skills/{contribution_id}/")


def delete_skill(owner_id: str, skill_name: str) -> bool:
    """Delete a user-private skill from MinIO.

    Returns True if anything was deleted.
    """
    if not is_minio_enabled():
        return False

    client, bucket = _ensure_minio()
    prefix = f"{_MINIO_PREFIX}/{owner_id}/{skill_name}/"

    removed = _remove_prefix_objects(client, bucket, prefix)
    if removed:
        logger.info("deleted user skill %s/%s (%d file(s))", owner_id, skill_name, removed)
    return removed > 0


def _list_skill_files(client: Any, bucket: str, owner_id: str, skill_name: str) -> list[str]:
    """List files within a single skill directory."""
    prefix = f"{_MINIO_PREFIX}/{owner_id}/{skill_name}/"
    objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
    return [obj.object_name[len(prefix):] for obj in objects]


class _BytesReader:
    """Helper to wrap bytes as a file-like object for MinIO put_object."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            result = self._data[self._pos:]
            self._pos = len(self._data)
            return result
        result = self._data[self._pos:self._pos + size]
        self._pos += len(result)
        return result


def _bytes_reader(data: bytes) -> _BytesReader:
    return _BytesReader(data)


def _skill_md_description(skill_path: str) -> str:
    """Read the ``description`` from a skill's SKILL.md YAML frontmatter.

    registry.yaml intentionally carries no per-skill description; the canonical one lives
    in each SKILL.md's frontmatter (the Claude Code ``name``/``description`` pair). This
    is what the system-skills list surfaces. Returns ``""`` if the file or frontmatter is
    absent/malformed.
    """
    if not skill_path:
        return ""
    md_path = repo_root() / skill_path / "SKILL.md"
    if not md_path.is_file():
        return ""
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    fm_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        fm_lines.append(line)
    try:
        import yaml

        fm = yaml.safe_load("\n".join(fm_lines)) or {}
    except Exception:
        return ""
    desc = fm.get("description") if isinstance(fm, dict) else None
    return str(desc).strip() if desc else ""


# (registry.yaml mtime, parsed skills) — cache so repeated Settings loads don't re-read
# 100+ SKILL.md files. Touch registry.yaml (or restart) to refresh after a SKILL.md edit.
_registry_cache: tuple[float, list[dict[str, Any]]] | None = None


def load_system_skills_registry() -> list[dict[str, Any]]:
    """Load system skills from skills/registry.yaml.

    Each skill's ``description`` is sourced from its SKILL.md frontmatter (the registry
    itself has no description field) — this is what populates the system-skills panel
    instead of an empty value. Cached on registry.yaml mtime.
    """
    global _registry_cache
    yaml_path = repo_root() / "skills" / "registry.yaml"
    if not yaml_path.exists():
        return []
    try:
        mtime = yaml_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _registry_cache is not None and _registry_cache[0] == mtime:
        return _registry_cache[1]

    try:
        import yaml

        with open(yaml_path, encoding="utf-8") as f:
            registry = yaml.safe_load(f) or {}
    except Exception:
        return []

    skills: list[dict[str, Any]] = []
    for entry in registry.get("skills", []):
        skills.append({
            "skill_id": entry.get("skill_id", ""),
            "claude_name": entry.get("claude_name", ""),
            "display_name": entry.get("display_name", ""),
            "domain": entry.get("domain", ""),
            "stages": entry.get("stages", []),
            "substages": entry.get("substages", []),
            "skill_type": entry.get("skill_type", ""),
            "status": entry.get("status", ""),
            "description": entry.get("description") or _skill_md_description(entry.get("skill_path", "")),
        })
    _registry_cache = (mtime, skills)
    return skills
