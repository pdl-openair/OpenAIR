"""Worker-side user-skill download — name/path-traversal guards.

The worker package (``services/worker``) has no test runner of its own, so these tests
import it via ``sys.path`` and run under the API's pytest. ``worker.user_skills`` only
depends on stdlib + ``httpx``, which the API env already provides.

Covers Phase-1 review item #3: the worker must validate each manifest skill ``name``
before using it as a path component (``user_dir / name``), so a forged manifest or a
historical dirty object key cannot traverse out of the user-skills directory.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

_WORKER_MODULE_PATH = Path(__file__).resolve().parents[2] / "worker" / "worker" / "user_skills.py"
_WORKER_MODULE_SPEC = importlib.util.spec_from_file_location(
    "openair_container_worker_user_skills",
    _WORKER_MODULE_PATH,
)
assert _WORKER_MODULE_SPEC is not None and _WORKER_MODULE_SPEC.loader is not None
worker_user_skills = importlib.util.module_from_spec(_WORKER_MODULE_SPEC)
_WORKER_MODULE_SPEC.loader.exec_module(worker_user_skills)
download_user_skills = worker_user_skills.download_user_skills


def _http_cm(get_resp: MagicMock):
    """A fake ``httpx.Client(...)`` context manager whose GET returns ``get_resp``."""
    client = MagicMock()
    client.get.return_value = get_resp
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    return cm


def test_worker_rejects_dirty_skill_name(tmp_path, monkeypatch):
    """A dirty ``name`` is skipped before any fetch — never written, never traversing."""
    monkeypatch.setenv("OPENAIR_SKILLS_USER_DIR", str(tmp_path))
    monkeypatch.setenv(
        "OPENAIR_USER_SKILLS_MANIFEST",
        json.dumps([
            {"name": "../escape", "files": [{"path": "SKILL.md", "url": "http://mock/bad"}]},
            {"name": "clean", "files": [{"path": "SKILL.md", "url": "http://mock/clean"}]},
        ]),
    )
    get_resp = MagicMock(status_code=200, content=b"# clean")
    cm = _http_cm(get_resp)
    with patch.object(worker_user_skills.httpx, "Client", return_value=cm):
        result = download_user_skills("ownerA")

    assert result == ["clean"]
    # only the clean skill's URL was fetched; the traversal name was refused outright
    fetched_urls = [c.args[0] for c in cm.__enter__.return_value.get.call_args_list]
    assert fetched_urls == ["http://mock/clean"]
    # the clean skill materialised under the user dir; nothing escaped it
    assert (tmp_path / "clean" / "SKILL.md").exists()
    assert not (tmp_path.parent / "escape").exists()


def test_worker_rejects_leading_dash_name(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAIR_SKILLS_USER_DIR", str(tmp_path))
    monkeypatch.setenv(
        "OPENAIR_USER_SKILLS_MANIFEST",
        json.dumps([{"name": "-evil", "files": [{"path": "SKILL.md", "url": "http://mock/bad"}]}]),
    )
    get_resp = MagicMock(status_code=200, content=b"x")
    with patch.object(worker_user_skills.httpx, "Client", return_value=_http_cm(get_resp)) as patched:
        result = download_user_skills("ownerA")

    assert result == []
    patched.return_value.__enter__.return_value.get.assert_not_called()


def test_worker_path_traversal_inside_file_rejected(tmp_path, monkeypatch):
    """The pre-existing per-file guard still rejects ``..`` segments in file paths."""
    monkeypatch.setenv("OPENAIR_SKILLS_USER_DIR", str(tmp_path))
    monkeypatch.setenv(
        "OPENAIR_USER_SKILLS_MANIFEST",
        json.dumps([{"name": "clean", "files": [{"path": "../../etc/evil", "url": "http://mock/x"}]}]),
    )
    get_resp = MagicMock(status_code=200, content=b"pwned")
    with patch.object(worker_user_skills.httpx, "Client", return_value=_http_cm(get_resp)):
        result = download_user_skills("ownerA")

    # no SKILL.md → skill not usable → not returned; and nothing escaped the user dir
    assert result == []
    assert not (tmp_path.parent / "etc").exists()
