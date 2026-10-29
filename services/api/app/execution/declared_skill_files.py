"""Declarative workspace-file collection shared by both execution paths.

A skill's ``runtime_contract.artifact_files`` lists glob patterns relative to
``<OPENAIR_WORKSPACE_ROOT>/<task_id>/<work_unit_id>/``.  Skills running in the
durable worker write into the workspace volume shared with the API, so
declared files (report markdown, figures, pareto CIFs, leaderboards) become
platform artifacts the workbench renders natively (markdown/cif/png/svg/csv).

Two consumers:
- legacy durable-run hook ``durable_execution._persist_declared_skill_files``
- orchestration v1 ``research_orchestration_repository`` dispatch completion

Missing globs are skipped silently — a partial skill run may not have produced
every file.  Images register before markdown so relative ``<img src>`` in
reports can be rewritten to the platform raw URL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DECLARED_FILE_FORMATS = {
    ".md": ("markdown", "text/markdown"),
    ".json": ("json", "application/json"),
    ".csv": ("csv", "text/csv"),
    ".png": ("png", "image/png"),
    ".svg": ("svg", "image/svg+xml"),
    ".cif": ("cif", "text/plain"),
}

_MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_PATTERNS = 12
_MAX_FILES = 24
_IMAGE_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class DeclaredFile:
    """One collected workspace file with its resolved render format."""

    rel: str  # posix path relative to the work-unit dir (pattern namespace)
    path: Path
    format: str
    mime_type: str


def declared_file_segment(value: str) -> str:
    """Sanitize one path segment; must stay byte-identical to the bridge `_segment`."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "run"


def collect_declared_files(
    ws_root: str,
    task_id: str,
    work_unit_id: str,
    patterns: Any,
) -> list[DeclaredFile]:
    """Collect declared output files under the work unit's workspace dir.

    Returns image files first (markdown asset rewriting depends on figures
    being registered before the report that references them).  Unreadable or
    oversized files are skipped; a missing dir simply yields no files.
    """
    if not isinstance(patterns, list) or not patterns:
        return []
    base = (
        Path(ws_root)
        / declared_file_segment(task_id)
        / declared_file_segment(work_unit_id)
    )
    if not base.is_dir():
        return []
    matches: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for pattern in patterns[:_MAX_PATTERNS]:
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        try:
            paths = sorted(base.glob(pattern.strip()))
        except (ValueError, OSError):
            continue
        for match in paths:
            rel = match.relative_to(base).as_posix()
            if rel in seen or not match.is_file():
                continue
            try:
                if match.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            seen.add(rel)
            matches.append((rel, match))
    collected: list[DeclaredFile] = []
    for rel, path in sorted(matches, key=lambda pair: 0 if pair[1].suffix.lower() in _IMAGE_SUFFIXES else 1)[:_MAX_FILES]:
        fmt_mime = DECLARED_FILE_FORMATS.get(path.suffix.lower())
        if fmt_mime is None:
            continue
        collected.append(DeclaredFile(rel=rel, path=path, format=fmt_mime[0], mime_type=fmt_mime[1]))
    return collected


def read_declared_file_bytes(
    file: DeclaredFile,
    *,
    base: Path,
    task_id: str,
    registered: dict[str, str],
) -> bytes | None:
    """Read one declared file's bytes; markdown gets relative asset refs rewritten.

    ``registered`` maps already-collected rel paths to artifact ids (images
    first, so ``<img src="figures/x.png">`` can resolve).  Returns None for
    unreadable or empty files — callers skip them.
    """
    try:
        if file.path.suffix.lower() == ".md":
            raw_text = file.path.read_text(encoding="utf-8", errors="replace")
            raw_text = rewrite_markdown_asset_refs(raw_text, base, file.path.parent, task_id, registered)
            content = raw_text.encode("utf-8")
        else:
            content = file.path.read_bytes()
    except OSError:
        return None
    return content or None


def rewrite_markdown_asset_refs(
    raw: str,
    base: Path,
    markdown_dir: Path,
    task_id: str,
    registered: dict[str, str],
) -> str:
    """Rewrite relative asset refs in a markdown report to platform raw URLs.

    ``<img src="figures/x.png">`` and ``![alt](figures/x.png)`` referencing
    an already-registered sibling file become
    ``/api/tasks/<task>/artifacts/<id>/raw`` so the workbench markdown
    renderer can resolve them same-origin. Unresolvable refs stay intact.
    """
    if not registered:
        return raw
    base_resolved = base.resolve()

    def _artifact_for(src: str) -> str | None:
        if not src or src.startswith(("http://", "https://", "/", "data:", "#")):
            return None
        try:
            rel = (markdown_dir / src).resolve().relative_to(base_resolved).as_posix()
        except (ValueError, OSError):
            return None
        return registered.get(rel)

    def _html_src_sub(match: re.Match[str]) -> str:
        src = match.group("src")
        artifact_id = _artifact_for(src)
        if artifact_id is None:
            return match.group(0)
        quote = match.group("q")
        return f"src={quote}/api/tasks/{task_id}/artifacts/{artifact_id}/raw{quote}"

    def _md_link_sub(match: re.Match[str]) -> str:
        src = match.group("src")
        artifact_id = _artifact_for(src)
        if artifact_id is None:
            return match.group(0)
        return f"](/api/tasks/{task_id}/artifacts/{artifact_id}/raw)"

    raw = re.sub(r'src=(?P<q>["\'])(?P<src>(?:(?!(?P=q)).)+)(?P=q)', _html_src_sub, raw)
    raw = re.sub(r"\]\((?P<src>[^)\s]+)\)", _md_link_sub, raw)
    return raw
