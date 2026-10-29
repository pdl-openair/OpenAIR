from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError


_MODULE_PATH = Path(__file__).parents[1] / "adapter" / "paper_source_resolution_adapter.py"
_SPEC = importlib.util.spec_from_file_location("paper_source_resolution_adapter", _MODULE_PATH)
assert _SPEC and _SPEC.loader
adapter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapter)


def _arxiv_hit(**kwargs):
    defaults = {
        "arxiv_id": "1234.56789v2",
        "title": "ExampleNet: Reproducible benchmark model",
        "abs_url": "https://arxiv.org/abs/1234.56789v2",
        "pdf_url": "https://arxiv.org/pdf/1234.56789v2",
        "abstract": None,
        "comment": None,
        "authors": None,
        "year": 2021,
        "venue": "arXiv:cs.CL",
        "doi": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _install_shared_sources(monkeypatch, hits_by_label):
    class SharedSources:
        @staticmethod
        def search_arxiv(query, *, max_results=8, timeout_s=30):
            return list(hits_by_label(query))

    monkeypatch.setattr(adapter, "_shared_sources", lambda: SharedSources())


def test_arxiv_metadata_request_uses_atom_representation(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def read(self):
            return b'<feed xmlns="http://www.w3.org/2005/Atom"/>'

    def fake_urlopen(request, *, timeout):
        captured["accept"] = request.get_header("Accept")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(adapter, "urlopen", fake_urlopen)

    status, body = adapter._http_get(
        "https://export.arxiv.org/api/query?search_query=all%3ASimCSE"
    )

    assert (status, body) == (200, '<feed xmlns="http://www.w3.org/2005/Atom"/>')
    assert captured == {
        "accept": "application/atom+xml",
        "timeout": adapter._HTTP_TIMEOUT_SECONDS,
    }


def test_resolves_a_unique_model_card_code_link(monkeypatch):
    _install_shared_sources(monkeypatch, lambda _query: [])

    def fake_http(url: str):
        if "api/models?" in url:
            return 200, '[{"id":"princeton-nlp/sup-simcse-bert-base-uncased"}]'
        assert url == "https://huggingface.co/princeton-nlp/sup-simcse-bert-base-uncased/raw/main/README.md"
        return 200, "Official implementation: https://github.com/princeton-nlp/SimCSE"

    monkeypatch.setattr(adapter, "_http_get", fake_http)
    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "我想复现 SimCSE 官方的 sup-simcse-bert-base-uncased 在 STS 上的结果。",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "succeeded"
    assert result["repository"] == "https://github.com/princeton-nlp/SimCSE.git"
    assert result["requested_revision"] == "HEAD"
    assert result["source_evidence"]["selection_basis"] == "unique_code_link_in_checkpoint_model_card"


def test_does_not_pick_between_multiple_code_links(monkeypatch):
    _install_shared_sources(monkeypatch, lambda _query: [])

    def fake_http(url: str):
        if "api/models?" in url:
            return 200, '[{"id":"owner/exact-checkpoint"}]'
        return 200, "https://github.com/a/one https://github.com/b/two"

    monkeypatch.setattr(adapter, "_http_get", fake_http)
    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 exact-checkpoint 的结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "incomplete"
    assert result["resolution_status"] == "code_link_ambiguous"
    assert result["repository"] is None


def test_resolves_author_declared_code_from_unique_primary_paper(monkeypatch):
    _install_shared_sources(
        monkeypatch,
        lambda query: [
            _arxiv_hit(
                title="ExampleNet: Reproducible benchmark model",
                comment="Our code is available at https://github.com/example-org/example-net",
            ),
            _arxiv_hit(
                arxiv_id="2345.67890v1",
                title="Using ExampleNet in practice",
                abs_url="https://arxiv.org/abs/2345.67890v1",
                pdf_url="https://arxiv.org/pdf/2345.67890v1",
            ),
        ] if "ExampleNet" in query or query == "ExampleNet" else [],
    )
    pdf_calls: list[str] = []
    monkeypatch.setattr(adapter, "_http_get_bytes", lambda url: pdf_calls.append(url) or (200, b"pdf"))
    monkeypatch.setattr(adapter, "_pdf_text", lambda _bytes: "should not be required")

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 ExampleNet-base 在公开基准上的论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "succeeded"
    assert result["repository"] == "https://github.com/example-org/example-net.git"
    assert result["source_evidence"]["kind"] == "primary_paper_author_declared_code_link"
    assert result["source_evidence"]["selection_basis"] == "unique_author_declared_code_link_in_primary_paper"
    assert result["source_evidence"]["content_form"] == "metadata_only"
    assert pdf_calls == []


def test_pdf_406_does_not_fail_when_metadata_declares_official_repo(monkeypatch):
    """Known-bad: arXiv PDF route must not decide source-resolution failure.

    SimCSE author-declared repository is already present in shared discovery
    metadata (arXiv comment). Structured/metadata-first must succeed without
    depending on the PDF bytes route.
    """
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(
                arxiv_id="2104.08821v4",
                title="SimCSE: Simple Contrastive Learning of Sentence Embeddings",
                abs_url="https://arxiv.org/abs/2104.08821v4",
                pdf_url="https://arxiv.org/pdf/2104.08821v4",
                comment=(
                    "Accepted to EMNLP 2021. The code and pre-trained models are "
                    "available at https://github.com/princeton-nlp/simcse"
                ),
            )
        ],
    )
    pdf_calls: list[str] = []

    def boom_pdf(url: str):
        pdf_calls.append(url)
        raise HTTPError(url, 406, "Not Acceptable", hdrs=None, fp=None)

    monkeypatch.setattr(adapter, "_http_get_bytes", boom_pdf)

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 SimCSE 论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "succeeded"
    assert result["repository"].casefold() == "https://github.com/princeton-nlp/simcse.git"
    assert result["source_evidence"]["content_form"] == "metadata_only"
    assert result["source_evidence"]["paper_identity"]["arxiv_id"] == "2104.08821v4"
    assert pdf_calls == []
    ops = result["execution_evidence"]["operations"]
    assert any(
        op["operation"] == "literature_metadata_search" and op["outcome"] == "succeeded"
        for op in ops
    )
    assert any(
        op["operation"] == "primary_paper_content_read" and op["outcome"] == "succeeded"
        for op in ops
    )
    assert not any(str(op.get("operation") or "").startswith("shared_") for op in ops)


def test_landing_html_resolves_without_forcing_pdf(monkeypatch):
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(
                title="ExampleNet: Reproducible benchmark model",
                comment=None,
                abstract="Method description without a repository URL.",
            )
        ],
    )
    pdf_calls: list[str] = []

    def fake_http(url: str):
        assert "/abs/" in url
        return 200, "Our code is available at https://github.com/example-org/example-net"

    monkeypatch.setattr(adapter, "_http_get", fake_http)
    monkeypatch.setattr(adapter, "_http_get_bytes", lambda url: pdf_calls.append(url) or (200, b"pdf"))

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 ExampleNet-base 在公开基准上的论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "succeeded"
    assert result["repository"] == "https://github.com/example-org/example-net.git"
    assert result["source_evidence"]["content_form"] in {"landing_page", "html"}
    assert pdf_calls == []


def test_partial_route_failure_is_isolated_with_provenance(monkeypatch):
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(
                title="ExampleNet: Reproducible benchmark model",
                comment=None,
                abstract="No code here.",
            )
        ],
    )

    def fake_http(url: str):
        if "/abs/" in url:
            raise HTTPError(url, 429, "Too Many Requests", hdrs=None, fp=None)
        raise AssertionError(url)

    def boom_pdf(url: str):
        raise HTTPError(url, 406, "Not Acceptable", hdrs=None, fp=None)

    monkeypatch.setattr(adapter, "_http_get", fake_http)
    monkeypatch.setattr(adapter, "_http_get_bytes", boom_pdf)

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 ExampleNet-base 在公开基准上的论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "incomplete"
    assert result["resolution_status"] == "code_link_missing"
    assert "failure_code" not in result
    ops = result["execution_evidence"]["operations"]
    assert any(op.get("http_status") == 429 and op["outcome"] == "failed" for op in ops)
    assert any(op.get("http_status") == 406 and op["outcome"] == "failed" for op in ops)

def test_does_not_treat_third_party_fork_as_official_code(monkeypatch):
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(
                title="ExampleNet: Reproducible benchmark model",
                comment="Related work discusses https://github.com/third-party/example-net-fork",
                abstract="We compare against prior systems.",
            )
        ],
    )
    monkeypatch.setattr(
        adapter,
        "_http_get_bytes",
        lambda url: (200, b"pdf"),
    )
    monkeypatch.setattr(
        adapter,
        "_pdf_text",
        lambda _bytes: "Related work is available at https://github.com/third-party/example-net-fork.",
    )
    monkeypatch.setattr(
        adapter,
        "_http_get",
        lambda url: (200, "mirror https://github.com/third-party/example-net-fork"),
    )

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 ExampleNet-base 在公开基准上的论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "incomplete"
    assert result["resolution_status"] == "code_link_missing"
    assert result["repository"] is None


def test_does_not_treat_a_non_author_github_citation_as_paper_code(monkeypatch):
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(
                title="ExampleNet: Reproducible benchmark model",
                comment=None,
                abstract="Background only.",
            )
        ],
    )
    monkeypatch.setattr(adapter, "_http_get", lambda _url: (200, "no code"))
    monkeypatch.setattr(adapter, "_http_get_bytes", lambda _url: (200, b"pdf"))
    monkeypatch.setattr(adapter, "_pdf_text", lambda _bytes: "Related work is available at https://github.com/other/implementation.")

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 ExampleNet-base 在公开基准上的论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "incomplete"
    assert result["resolution_status"] == "code_link_missing"


def test_does_not_pick_between_multiple_primary_papers(monkeypatch):
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(title="ExampleNet: First report", arxiv_id="1234.56789v2"),
            _arxiv_hit(
                title="ExampleNet: Revised report",
                arxiv_id="2345.67890v1",
                abs_url="https://arxiv.org/abs/2345.67890v1",
                pdf_url="https://arxiv.org/pdf/2345.67890v1",
            ),
        ],
    )

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 ExampleNet-base 在公开基准上的论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "incomplete"
    assert result["resolution_status"] == "paper_ambiguous"


def test_resolves_a_user_declared_academic_source_page_without_checkpoint_pattern(monkeypatch):
    page = "https://aclanthology.org/2021.emnlp-main.552/"

    def fake_http(url: str):
        assert url == page
        return 200, "Code: https://github.com/princeton-nlp/SimCSE"

    monkeypatch.setattr(adapter, "_http_get", fake_http)
    result = adapter.run_cs_paper_source_resolution({
        "research_goal": f"复现该论文的 STS 结果，论文页面是 {page}",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "succeeded"
    assert result["source_evidence"]["kind"] == "declared_source_page_code_link"


def _install_reachable_repository_probe(monkeypatch):
    class Completed:
        returncode = 0

    monkeypatch.setattr(adapter.shutil, "which", lambda _: "git")
    monkeypatch.setattr(adapter.subprocess, "run", lambda *args, **kwargs: Completed())


def test_user_repository_is_preserved_after_real_remote_probe(monkeypatch):
    _install_reachable_repository_probe(monkeypatch)
    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现这个实现",
        "repository": "https://example.org/group/project.git",
        "revision": "v1.0.0",
    })

    assert result["state"] == "succeeded"
    assert result["repository"] == "https://example.org/group/project.git"
    assert result["requested_revision"] == "v1.0.0"
    assert result["source_evidence"]["selection_basis"] == "user_provided"


def test_user_commit_sha_passes_source_resolution_without_ref_pattern_probe(monkeypatch):
    """Known-bad → good: commit SHA must not be used as ls-remote ref pattern."""
    sha = "235f89e0244b23673b69fbbc4a1cd9868c5a5c24"
    repository = "https://github.com/princeton-nlp/SimCSE.git"
    _install_reachable_repository_probe(monkeypatch)

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 SimCSE",
        "repository": repository,
        "revision": sha,
    })

    assert result["state"] == "succeeded"
    assert result["repository"] == repository
    assert result["requested_revision"] == sha


def test_user_repository_head_branch_and_tag_revisions_are_preserved(monkeypatch):
    repository = "https://example.org/group/project.git"
    for revision in ("HEAD", "main", "v1.0.0"):
        _install_reachable_repository_probe(monkeypatch)
        result = adapter.run_cs_paper_source_resolution({
            "research_goal": "复现这个实现",
            "repository": repository,
            "revision": None if revision == "HEAD" else revision,
        })
        assert result["state"] == "succeeded"
        assert result["requested_revision"] == revision


def test_reachable_empty_bare_repository_without_head_or_refs_is_reachable(tmp_path):
    """Repository transport succeeds even when no revision can resolve yet."""
    git = shutil.which("git")
    if git is None:
        import pytest

        pytest.skip("git unavailable")
    repository = tmp_path / "empty.git"
    subprocess.run(
        [git, "init", "--bare", str(repository)],
        check=True,
        capture_output=True,
        text=True,
    )
    refs = subprocess.run(
        [git, "--git-dir", str(repository), "show-ref"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert refs.returncode == 1

    revisions = (
        "235f89e0244b23673b69fbbc4a1cd9868c5a5c24",
        "main",
        "v1.0.0",
        "HEAD",
    )
    for revision in revisions:
        result = adapter._probe_user_repository(str(repository), revision)
        assert result["state"] == "succeeded"
        assert result["repository"] == str(repository)
        assert result["requested_revision"] == revision


def test_unreachable_user_repository_still_fails_probe(monkeypatch):
    class Completed:
        returncode = 2

    monkeypatch.setattr(adapter.shutil, "which", lambda _: "git")
    monkeypatch.setattr(adapter.subprocess, "run", lambda *args, **kwargs: Completed())
    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现这个实现",
        "repository": "https://example.invalid/missing/repo.git",
        "revision": "235f89e0244b23673b69fbbc4a1cd9868c5a5c24",
    })

    assert result["state"] == "failed"
    assert result["failure_code"] == "repository_unavailable"
    assert result["failed_stage"] == "repository_probe"


def test_accessible_repository_with_invalid_revision_does_not_claim_revision_verified(monkeypatch):
    """Boundary: source resolution only probes reachability; revision stays requested."""
    repository = "https://example.org/group/project.git"
    bogus = "definitely-not-a-real-revision-zzz"
    _install_reachable_repository_probe(monkeypatch)

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现这个实现",
        "repository": repository,
        "revision": bogus,
    })

    assert result["state"] == "succeeded"
    assert result["requested_revision"] == bogus
    assert "resolved_commit_sha" not in result
    assert "verified_head" not in result


def _assert_matches_formal_schema(payload: dict) -> None:
    import json

    from jsonschema import validate

    schema = json.loads(
        (Path(__file__).parents[1] / "output.schema.json").read_text(encoding="utf-8")
    )
    validate(instance=payload, schema=schema)


def test_simcse_shared_discovery_output_matches_formal_schema(monkeypatch):
    """Acceptance 1: Adapter shared-discovery output must pass Formal Schema."""
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(
                arxiv_id="2104.08821v4",
                title="SimCSE: Simple Contrastive Learning of Sentence Embeddings",
                abs_url="https://arxiv.org/abs/2104.08821v4",
                pdf_url="https://arxiv.org/pdf/2104.08821v4",
                comment=(
                    "Accepted to EMNLP 2021. The code and pre-trained models are "
                    "available at https://github.com/princeton-nlp/simcse"
                ),
            )
        ],
    )
    monkeypatch.setattr(
        adapter,
        "_http_get_bytes",
        lambda url: (_ for _ in ()).throw(HTTPError(url, 406, "Not Acceptable", hdrs=None, fp=None)),
    )

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 SimCSE 论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "succeeded"
    assert result["source_evidence"]["paper_identity"]["arxiv_id"] == "2104.08821v4"
    assert result["source_evidence"]["content_form"] == "metadata_only"
    ops = {op["operation"] for op in result["execution_evidence"]["operations"]}
    assert "literature_metadata_search" in ops
    assert "primary_paper_content_read" in ops
    assert not any(name.startswith("shared_") for name in ops)
    _assert_matches_formal_schema(result)


def test_incomplete_shared_discovery_output_matches_formal_schema(monkeypatch):
    _install_shared_sources(
        monkeypatch,
        lambda _query: [
            _arxiv_hit(
                title="ExampleNet: Reproducible benchmark model",
                comment=None,
                abstract="No code here.",
            )
        ],
    )
    monkeypatch.setattr(adapter, "_http_get", lambda _url: (200, "no repository"))
    monkeypatch.setattr(adapter, "_http_get_bytes", lambda _url: (200, b"pdf"))
    monkeypatch.setattr(adapter, "_pdf_text", lambda _bytes: "No author code declaration.")

    result = adapter.run_cs_paper_source_resolution({
        "research_goal": "复现 ExampleNet-base 在公开基准上的论文结果",
        "repository": None,
        "revision": None,
    })

    assert result["state"] == "incomplete"
    assert result["resolution_status"] == "code_link_missing"
    _assert_matches_formal_schema(result)
