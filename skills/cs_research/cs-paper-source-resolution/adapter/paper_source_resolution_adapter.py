"""Bounded, evidence-preserving source resolution for CS checkpoint reproduction."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

_HF_SEARCH = "https://huggingface.co/api/models?search={query}&limit=20"
_HF_CARD = "https://huggingface.co/{model_id}/raw/main/README.md"
_MODEL_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9._-]{2,}")
_GITHUB_URL = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", re.IGNORECASE)
_SOURCE_URL = re.compile(r"https://[^\s<>\]\[\](){}]+", re.IGNORECASE)
# Strict author self-reference used for fulltext/body evidence.
_AUTHOR_CODE_DECLARATION = re.compile(
    r"(?<![A-Za-z])(?:our|we)\b\s+(?:[\w-]+\s+){0,8}(?:code|implementation|software)(?:[\w\s,;:().-]{0,180})?\b(?:at|from|via)\s+(https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?)",
    re.IGNORECASE,
)
# Author-owned metadata / comment phrasing (e.g. arXiv comment), still requires an
# explicit code/implementation/software availability statement + unique GitHub URL.
_AUTHOR_CODE_AVAILABILITY = re.compile(
    r"(?<![A-Za-z])(?:our|we|the)\b\s+(?:[\w-]+\s+){0,8}(?:code|implementation|software)(?:[\w\s,;:().-]{0,120})?\b(?:are\s+)?(?:publicly\s+)?available\s+(?:at|from|via)\s+(https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?)",
    re.IGNORECASE,
)
_DECLARED_SOURCE_HOSTS = frozenset({"arxiv.org", "aclanthology.org", "openreview.net", "doi.org", "huggingface.co"})
_GENERIC_TOKENS = frozenset({
    "official", "paper", "reproduce", "reproduction", "result", "results", "model",
    "checkpoint", "semantic", "similarity", "sentence", "embedding", "embeddings", "help",
})
_HTTP_TIMEOUT_SECONDS = 30
_GIT_TIMEOUT_SECONDS = 60
_SHARED_RETRIEVAL_ROOT = (
    Path(__file__).resolve().parents[3]
    / "opentrajectory"
    / "academic-source-retrieval-skill"
    / "adapter"
)


def _load_shared_module(module_name: str, filename: str) -> Any:
    import sys

    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    path = _SHARED_RETRIEVAL_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"shared literature module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses (and similar) require the module to be present in sys.modules
    # while the class body executes.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _shared_sources() -> Any:
    return _load_shared_module("openair_shared_standalone_sources", "standalone_sources.py")


def _shared_content_access() -> Any:
    return _load_shared_module("openair_shared_content_form_access", "content_form_access.py")


# Formal Schema operation vocabulary (stable research semantics — not helper names).
_OP_LITERATURE_METADATA_SEARCH = "literature_metadata_search"
_OP_PRIMARY_PAPER_CONTENT_READ = "primary_paper_content_read"
_OP_PRIMARY_PAPER_PDF_READ = "primary_paper_pdf_read"
_FORMAL_CONTENT_FORMS = frozenset({"metadata_only", "landing_page", "html", "pdf", "xml", "tei"})


def _operation(operation: str, endpoint: str, outcome: str, *, http_status: int | None = None) -> dict[str, Any]:
    if outcome not in {"succeeded", "failed"}:
        outcome = "failed"
    row: dict[str, Any] = {"operation": operation, "endpoint": endpoint, "outcome": outcome}
    if http_status is not None:
        row["http_status"] = http_status
    return row


def _evidence(operations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"operations": operations, "network_action_performed": bool(operations)}


def _paper_identity_from_hit(hit: Any) -> dict[str, str] | None:
    """Project only present identity facts into the Formal Schema field."""
    identity: dict[str, str] = {}
    arxiv_id = getattr(hit, "arxiv_id", None)
    if isinstance(arxiv_id, str) and arxiv_id.strip() and not arxiv_id.strip().startswith("openalex:"):
        identity["arxiv_id"] = arxiv_id.strip()
    openalex_raw = getattr(hit, "openalex_id", None) or getattr(hit, "dedupe_key", None)
    if isinstance(arxiv_id, str) and arxiv_id.strip().startswith("openalex:"):
        identity["openalex_id"] = arxiv_id.strip().removeprefix("openalex:")
    elif isinstance(openalex_raw, str) and openalex_raw.strip():
        identity["openalex_id"] = openalex_raw.strip()
    title = getattr(hit, "title", None)
    if isinstance(title, str) and title.strip():
        identity["title"] = title.strip()
    doi = getattr(hit, "doi", None)
    if isinstance(doi, str) and doi.strip():
        identity["doi"] = doi.strip()
    return identity or None


def _failure(code: str, stage: str, operations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "state": "failed",
        "failure_code": code,
        "failed_stage": stage,
        "execution_evidence": _evidence(operations or []),
    }


def _valid_text(value: Any, *, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip() and len(value) <= maximum and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _valid_research_goal(value: Any) -> bool:
    # Research goals are user text, not single-line identifiers. Formal Schema
    # permits normal multiline content; only an embedded NUL remains a runtime
    # safety rejection.
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096 and "\x00" not in value


def _valid_repository(value: Any) -> bool:
    if not _valid_text(value, maximum=2048):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"https", "git"} and bool(parsed.netloc) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment


def _validated_input(payload: Any) -> tuple[str, str | None, str | None] | None:
    if not isinstance(payload, dict) or set(payload) != {"research_goal", "repository", "revision"}:
        return None
    goal, repository, revision = payload.get("research_goal"), payload.get("repository"), payload.get("revision")
    if not _valid_research_goal(goal):
        return None
    if repository is not None and not _valid_repository(repository):
        return None
    if revision is not None and not _valid_text(revision, maximum=512):
        return None
    return goal, repository, revision


def _http_get(url: str) -> tuple[int, str]:
    host = (urlsplit(url).hostname or "").casefold()
    accept = (
        "application/atom+xml"
        if host == "export.arxiv.org"
        else "application/json, text/plain;q=0.8"
    )
    request = Request(url, headers={"Accept": accept, "User-Agent": "OpenAIR-CS-source-resolution"})
    with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        status = int(response.getcode() or 200)
        return status, response.read().decode("utf-8")


def _http_get_bytes(url: str) -> tuple[int, bytes]:
    request = Request(url, headers={"Accept": "application/pdf, application/octet-stream;q=0.9", "User-Agent": "OpenAIR-CS-source-resolution"})
    with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        status = int(response.getcode() or 200)
        return status, response.read()


def _candidate_tokens(goal: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for token in _MODEL_TOKEN.findall(goal):
        normalized = token.casefold()
        if normalized in _GENERIC_TOKENS or normalized in seen:
            continue
        seen.add(normalized)
        values.append(token)
    return sorted(values, key=lambda value: (-len(value), value.casefold()))[:6]


def _paper_labels(goal: str) -> list[str]:
    """Extract only specific model labels that can be verified against a paper title.

    A label is not source authority by itself.  It merely bounds an academic
    metadata lookup; the later author-declared link remains the authority.
    """
    seen: set[str] = set()
    labels: list[str] = []
    for token in _candidate_tokens(goal):
        for part in re.findall(r"[A-Za-z][A-Za-z0-9]{4,}", token):
            # A lower-case checkpoint qualifier (for example "uncased") is
            # not a paper identity.  Requiring an explicitly named proper
            # label avoids treating architecture/configuration words as a
            # primary-paper selector.
            if part.islower():
                continue
            normalized = part.casefold()
            if normalized in _GENERIC_TOKENS or normalized in seen:
                continue
            seen.add(normalized)
            labels.append(part)
    return sorted(labels, key=lambda value: (-len(value), value.casefold()))[:6]


def _canonical_github_repository(url: str) -> str | None:
    match = _GITHUB_URL.fullmatch(url.rstrip(".,;:!?)]}\"'"))
    if match is None:
        return None
    parsed = urlsplit(match.group(0))
    pieces = [piece for piece in parsed.path.split("/") if piece]
    if len(pieces) != 2:
        return None
    owner, name = pieces
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        return None
    return f"https://github.com/{owner}/{name}.git"


def _github_repositories_in_model_card(markdown: str) -> list[str]:
    values: list[str] = []
    for raw in _GITHUB_URL.findall(markdown):
        canonical = _canonical_github_repository(raw)
        if canonical is not None and canonical.casefold() not in {value.casefold() for value in values}:
            values.append(canonical)
    return values


def _declared_source_urls(goal: str) -> list[str]:
    """Return only bounded, HTTPS academic/provider pages named by the user."""
    values: list[str] = []
    for raw in _SOURCE_URL.findall(goal):
        url = raw.rstrip(".,;:!?)]}\"'")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or host not in _DECLARED_SOURCE_HOSTS or not parsed.path:
            continue
        if url.casefold() not in {value.casefold() for value in values}:
            values.append(url)
    return values[:3]


def _resolve_declared_source_pages(goal: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Use a user-named academic/provider page only when it directly declares one repository."""
    operations: list[dict[str, Any]] = []
    for source_url in _declared_source_urls(goal):
        try:
            status, body = _http_get(source_url)
        except HTTPError as error:
            operations.append(_operation("declared_source_page_read", source_url, "failed", http_status=error.code))
            continue
        except (URLError, TimeoutError, OSError, UnicodeDecodeError):
            operations.append(_operation("declared_source_page_read", source_url, "failed"))
            continue
        operations.append(_operation("declared_source_page_read", source_url, "succeeded", http_status=status))
        repositories = _github_repositories_in_model_card(body)
        if len(repositories) == 1:
            repository = repositories[0]
            return {
                "state": "succeeded",
                "repository": repository,
                "requested_revision": "HEAD",
                "source_evidence": {
                    "kind": "declared_source_page_code_link",
                    "source_url": source_url,
                    "repository_url": repository,
                    "selection_basis": "unique_code_link_in_declared_source_page",
                },
                "execution_evidence": _evidence(operations),
            }, operations
        if len(repositories) > 1:
            return _incomplete("code_link_ambiguous", repositories, operations), operations
    return None, operations


def _probe_user_repository(repository: str, revision: str | None) -> dict[str, Any]:
    """Confirm repository reachability only; preserve requested_revision as-is.

    Revision existence / checkout is owned by repository_revision_acquisition.
    Do not pass any ref pattern to ``git ls-remote``: even ``HEAD`` is absent
    from a reachable empty repository and cannot decide transport reachability.
    """
    git = shutil.which("git")
    endpoint = repository
    if git is None:
        return _failure("git_unavailable", "repository_probe", [_operation("git_ls_remote", endpoint, "failed")])
    requested = revision or "HEAD"
    try:
        completed = subprocess.run(
            [git, "ls-remote", repository],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failure("repository_unavailable", "repository_probe", [_operation("git_ls_remote", endpoint, "failed")])
    operation = _operation("git_ls_remote", endpoint, "succeeded" if completed.returncode == 0 else "failed")
    if completed.returncode != 0:
        return _failure("repository_unavailable", "repository_probe", [operation])
    return {
        "state": "succeeded",
        "repository": repository,
        "requested_revision": requested,
        "source_evidence": {
            "kind": "user_provided_repository",
            "source_url": repository,
            "repository_url": repository,
            "selection_basis": "user_provided",
        },
        "execution_evidence": _evidence([operation]),
    }


def _incomplete(status: str, options: list[str], operations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "state": "incomplete",
        "resolution_status": status,
        "repository": None,
        "requested_revision": None,
        "candidate_options": options[:5],
        "execution_evidence": _evidence(operations),
    }


def _paper_title_matches_label(title: str, label: str) -> bool:
    normalized_title = " ".join(title.split()).casefold()
    normalized_label = label.casefold()
    if not normalized_title.startswith(normalized_label):
        return False
    suffix = normalized_title[len(normalized_label):]
    return not suffix or suffix[0].isspace() or suffix[0] == ":"


def _pdf_text(pdf_bytes: bytes) -> str:
    # PyMuPDF's legacy ``fitz`` import emits a deprecation warning to stdout
    # in the production image.  Adapter stdout is a strict JSON transport, so
    # use the current module name to keep execution diagnostics off that wire.
    import pymupdf

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in document)
    finally:
        document.close()


def _author_declared_repositories(text: str, *, allow_availability_phrasing: bool = False) -> list[str]:
    values: list[str] = []
    normalized = " ".join(text.split())
    patterns = [_AUTHOR_CODE_DECLARATION]
    if allow_availability_phrasing:
        patterns.append(_AUTHOR_CODE_AVAILABILITY)
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            repository = _canonical_github_repository(match.group(1))
            if repository is not None and repository.casefold() not in {value.casefold() for value in values}:
                values.append(repository)
    return values


def _record_content_form_operations(operations: list[dict[str, Any]], forms: list[dict[str, Any]]) -> None:
    """Normalize shared content-form helper facts into Formal Schema operations.

    Skipped routes are local access planning, not performed external actions, so
    they are omitted from formal evidence rather than encoded as helper names.
    """
    for form in forms:
        outcome = str(form.get("outcome") or "failed")
        if outcome == "skipped":
            continue
        access_type = str(form.get("access_type") or "")
        endpoint = str(form.get("source_url") or f"content_form:{access_type or 'unknown'}")
        http_status = form.get("http_status")
        operation = (
            _OP_PRIMARY_PAPER_PDF_READ
            if access_type == "pdf"
            else _OP_PRIMARY_PAPER_CONTENT_READ
        )
        operations.append(
            _operation(
                operation,
                endpoint,
                outcome if outcome in {"succeeded", "failed"} else "failed",
                http_status=int(http_status) if isinstance(http_status, int) else None,
            )
        )


def _resolve_from_primary_paper(goal: str, operations: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Resolve code only from a uniquely matched primary paper's own declaration.

    Discovery and content-form access are delegated to the shared literature
    stack. A single PDF/HTML/source route failure is recorded locally and does
    not decide overall source-resolution failure.
    """
    if operations is None:
        operations = []
    paper_options: list[str] = []
    shared_sources = _shared_sources()
    shared_access = _shared_content_access()

    def _finish_from_hit(hit: Any, *, query: str) -> dict[str, Any] | None:
        candidate = shared_access.candidate_from_arxiv_hit(hit, query=query)
        resolved: dict[str, Any] | None = None

        def _stop_when_author_code_resolved(form: dict[str, Any]) -> bool:
            nonlocal resolved
            if form.get("outcome") != "succeeded" or not isinstance(form.get("text"), str):
                return False
            access_type = str(form.get("access_type") or "")
            allow_availability = access_type in {"metadata_only", "landing_page", "html"}
            repositories = _author_declared_repositories(
                form["text"],
                allow_availability_phrasing=allow_availability,
            )
            if len(repositories) == 1:
                repository = repositories[0]
                source_url = form.get("source_url") or candidate.get("landing_page_url") or candidate.get("pdf_url")
                source_evidence: dict[str, Any] = {
                    "kind": "primary_paper_author_declared_code_link",
                    "source_url": source_url,
                    "repository_url": repository,
                    "selection_basis": "unique_author_declared_code_link_in_primary_paper",
                }
                if access_type in _FORMAL_CONTENT_FORMS:
                    source_evidence["content_form"] = access_type
                paper_identity = _paper_identity_from_hit(hit)
                if paper_identity is not None:
                    source_evidence["paper_identity"] = paper_identity
                resolved = {
                    "state": "succeeded",
                    "repository": repository,
                    "requested_revision": "HEAD",
                    "source_evidence": source_evidence,
                }
                return True
            if len(repositories) > 1:
                resolved = _incomplete("code_link_ambiguous", repositories, [])
                return True
            return False

        forms = shared_access.collect_readable_content_forms(
            candidate,
            allow_pdf=True,
            pdf_text_extractor=_pdf_text,
            http_get_text=_http_get,
            http_get_bytes=_http_get_bytes,
            should_stop=_stop_when_author_code_resolved,
        )
        _record_content_form_operations(operations, forms)

        if resolved is not None:
            resolved["execution_evidence"] = _evidence(operations)
            return resolved
        return _incomplete("code_link_missing", [], operations)

    def _hits_from_openalex(label: str) -> list[Any]:
        endpoint = f"literature_metadata:openalex:{label}"
        try:
            cands = shared_sources.search_openalex(label, max_results=8, timeout_s=_HTTP_TIMEOUT_SECONDS)
        except HTTPError as error:
            operations.append(
                _operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "failed", http_status=error.code)
            )
            return []
        except Exception:
            operations.append(_operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "failed"))
            return []
        operations.append(_operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "succeeded"))
        matched = [
            cand for cand in cands
            if _paper_title_matches_label(str(getattr(cand, "title", "") or ""), label)
        ]
        hits: list[Any] = []
        for cand in matched:
            arxiv_id = getattr(cand, "arxiv_id", None)
            if isinstance(arxiv_id, str) and arxiv_id.strip():
                enriched = _fetch_arxiv_hit_by_id(shared_sources, arxiv_id.strip(), operations)
                if enriched is not None:
                    hits.append(enriched)
                    continue
            hits.append(_simple_hit_from_openalex(cand))
        return hits

    for label in _paper_labels(goal):
        endpoint = f"literature_metadata:arxiv:{label}"
        hits: list[Any] = []
        try:
            hits = list(shared_sources.search_arxiv(label, max_results=8, timeout_s=_HTTP_TIMEOUT_SECONDS))
            operations.append(_operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "succeeded"))
        except HTTPError as error:
            operations.append(
                _operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "failed", http_status=error.code)
            )
        except Exception:
            operations.append(_operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "failed"))

        if not hits:
            hits = _hits_from_openalex(label)
            matches = list(hits)  # OpenAlex path already title-filtered
        else:
            matches = [hit for hit in hits if _paper_title_matches_label(getattr(hit, "title", "") or "", label)]
            if not matches:
                matches = _hits_from_openalex(label)
        if len(matches) > 1:
            for hit in matches:
                title = getattr(hit, "title", None)
                if isinstance(title, str) and title not in paper_options:
                    paper_options.append(title)
            continue
        if not matches:
            continue

        return _finish_from_hit(matches[0], query=label)

    if paper_options:
        return _incomplete("paper_ambiguous", paper_options, operations)
    return None


def _simple_hit_from_openalex(cand: Any) -> Any:
    from types import SimpleNamespace

    arxiv_id = getattr(cand, "arxiv_id", None)
    dedupe = getattr(cand, "dedupe_key", None) or getattr(cand, "openalex_id", None)
    return SimpleNamespace(
        arxiv_id=arxiv_id if isinstance(arxiv_id, str) and arxiv_id.strip() else (
            f"openalex:{dedupe}" if isinstance(dedupe, str) and dedupe.strip() else "openalex:unknown"
        ),
        title=getattr(cand, "title", None),
        abs_url=getattr(cand, "landing_page_url", None),
        pdf_url=getattr(cand, "pdf_url", None),
        abstract=getattr(cand, "abstract_snippet", None),
        comment=None,
        authors=getattr(cand, "authors", None),
        year=getattr(cand, "year", None),
        venue=getattr(cand, "venue", None),
        doi=getattr(cand, "doi", None),
        openalex_id=getattr(cand, "openalex_id", None) or dedupe,
    )


def _fetch_arxiv_hit_by_id(shared_sources: Any, arxiv_id: str, operations: list[dict[str, Any]]) -> Any | None:
    """Fetch one arXiv Atom entry by id for comment/abstract enrichment."""
    endpoint = f"literature_metadata:arxiv_id:{arxiv_id}"
    fetch = getattr(shared_sources, "fetch_arxiv_by_id", None)
    if fetch is None:
        return None
    try:
        hit = fetch(arxiv_id, timeout_s=_HTTP_TIMEOUT_SECONDS)
    except HTTPError as error:
        operations.append(
            _operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "failed", http_status=error.code)
        )
        return None
    except Exception:
        operations.append(_operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "failed"))
        return None
    if hit is None:
        operations.append(_operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "failed"))
        return None
    operations.append(_operation(_OP_LITERATURE_METADATA_SEARCH, endpoint, "succeeded"))
    return hit


def _resolve_from_checkpoint(goal: str, operations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if operations is None:
        operations = []
    ambiguous: list[str] = []
    for token in _candidate_tokens(goal):
        endpoint = _HF_SEARCH.format(query=quote(token, safe=""))
        try:
            status, raw = _http_get(endpoint)
            rows = json.loads(raw)
        except HTTPError as error:
            operations.append(_operation("huggingface_model_search", endpoint, "failed", http_status=error.code))
            # Single HF search failure is local; try remaining tokens / incomplete.
            continue
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            operations.append(_operation("huggingface_model_search", endpoint, "failed"))
            continue
        operations.append(_operation("huggingface_model_search", endpoint, "succeeded", http_status=status))
        if not isinstance(rows, list):
            operations.append(_operation("huggingface_model_search", endpoint, "failed"))
            continue
        exact = [str(row.get("id") or "") for row in rows if isinstance(row, dict) and str(row.get("id") or "").rsplit("/", 1)[-1].casefold() == token.casefold()]
        exact = list(dict.fromkeys(value for value in exact if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value)))
        if len(exact) > 1:
            ambiguous.extend(value for value in exact if value not in ambiguous)
            continue
        if not exact:
            continue
        model_id = exact[0]
        card_url = _HF_CARD.format(model_id=model_id)
        try:
            card_status, markdown = _http_get(card_url)
        except HTTPError as error:
            operations.append(_operation("huggingface_model_card_read", card_url, "failed", http_status=error.code))
            continue
        except (URLError, TimeoutError, OSError, UnicodeDecodeError):
            operations.append(_operation("huggingface_model_card_read", card_url, "failed"))
            continue
        operations.append(_operation("huggingface_model_card_read", card_url, "succeeded", http_status=card_status))
        repositories = _github_repositories_in_model_card(markdown)
        if len(repositories) == 1:
            repository = repositories[0]
            return {
                "state": "succeeded",
                "repository": repository,
                "requested_revision": "HEAD",
                "source_evidence": {
                    "kind": "checkpoint_model_card_code_link",
                    "source_url": card_url,
                    "repository_url": repository,
                    "selection_basis": "unique_code_link_in_checkpoint_model_card",
                    "model_id": model_id,
                },
                "execution_evidence": _evidence(operations),
            }
        if len(repositories) > 1:
            return _incomplete("code_link_ambiguous", repositories, operations)
        return _incomplete("code_link_missing", [], operations)
    if ambiguous:
        return _incomplete("model_ambiguous", ambiguous, operations)
    return _incomplete("model_not_found", [], operations)


def run_cs_paper_source_resolution(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve only source-backed code identity; no paper or metric judgment happens here."""
    validated = _validated_input(payload)
    if validated is None:
        return _failure("skill_input_invalid", "input_validation")
    goal, repository, revision = validated
    if repository is not None:
        return _probe_user_repository(repository, revision)
    declared, prior_operations = _resolve_declared_source_pages(goal)
    if declared is not None:
        return declared
    primary_paper = _resolve_from_primary_paper(goal, prior_operations)
    if primary_paper is not None:
        return primary_paper
    return _resolve_from_checkpoint(goal, prior_operations)
