from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = SKILL_ROOT / "adapter" / "resource_identity_adapter.py"
_spec = importlib.util.spec_from_file_location("resource_identity_adapter", ADAPTER_PATH)
assert _spec is not None and _spec.loader is not None
adapter = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = adapter
_spec.loader.exec_module(adapter)

COMMIT = "1" * 40
OTHER_COMMIT = "2" * 40
FILE_BYTES = b"actual downloaded bytes\n"
FILE_SHA256 = hashlib.sha256(FILE_BYTES).hexdigest()


class FakeApi:
    def __init__(self, sha: str = COMMIT) -> None:
        self.sha = sha
        self.calls: list[dict[str, Any]] = []
        self.manifest_calls: list[dict[str, Any]] = []

    def repo_info(self, repo_id: str, **kwargs: Any) -> Any:
        self.calls.append({"repo_id": repo_id, **kwargs})
        return SimpleNamespace(sha=self.sha)

    def list_repo_files(self, repo_id: str, **kwargs: Any) -> list[str]:
        self.manifest_calls.append({"repo_id": repo_id, **kwargs})
        return ["config.json", "weights.bin"]


class FakeClient:
    def __init__(
        self,
        *,
        sha: str = COMMIT,
        fail_after_partial: bool = False,
        partial_snapshot_without_error: bool = False,
        version: str = "1.26.0",
    ) -> None:
        self.version = version
        self.api = FakeApi(sha)
        self.fail_after_partial = fail_after_partial
        self.partial_snapshot_without_error = partial_snapshot_without_error
        self.snapshot_calls = 0
        self.file_calls = 0
        self.snapshot_local_dirs: list[Path] = []
        self.file_local_dirs: list[Path] = []

    def snapshot_download(self, repo_id: str, **kwargs: Any) -> str:
        self.snapshot_calls += 1
        root = Path(kwargs["local_dir"])
        self.snapshot_local_dirs.append(root)
        root.mkdir(parents=True, exist_ok=True)
        (root / "config.json").write_text('{"ok": true}\n', encoding="utf-8")
        if self.fail_after_partial:
            raise OSError("simulated provider failure")
        if not self.partial_snapshot_without_error:
            (root / "weights.bin").write_bytes(b"weights")
        return str(root)

    def hf_hub_download(self, repo_id: str, filename: str, **kwargs: Any) -> str:
        self.file_calls += 1
        root = Path(kwargs["local_dir"])
        self.file_local_dirs.append(root)
        target = root.joinpath(*filename.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(FILE_BYTES)
        if self.fail_after_partial:
            raise OSError("simulated provider failure")
        return str(target)


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], client: FakeClient) -> dict[str, Any]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(tmp_path))
    monkeypatch.setattr(adapter, "_installed_huggingface_version", lambda: client.version)
    monkeypatch.setattr(adapter, "_load_huggingface_client", lambda: client)
    return adapter.run_resource_identity_acquisition(payload)


def test_snapshot_uses_resolved_commit_and_materializes_only_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
            "expected_commit_sha": COMMIT,
        },
        client,
    )
    assert result["state"] == "succeeded"
    assert result["requested_revision"] == "main"
    assert result["resolved_commit_sha"] == COMMIT
    assert result["actual_identity"] == {"kind": "huggingface_commit", "commit_sha": COMMIT}
    assert result["expected_identity_verification"] == "passed"
    materialized = tmp_path / result["workspace_relative_path"]
    assert materialized.parent == tmp_path / "resource"
    assert (materialized / "config.json").is_file()
    assert (materialized / "weights.bin").is_file()
    assert client.api.manifest_calls[0]["revision"] == COMMIT
    assert client.snapshot_calls == 1


def test_file_sha256_comes_from_materialized_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_file",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "release",
            "filename": "data/sample.bin",
            "expected_sha256": FILE_SHA256,
        },
        client,
    )
    assert result["state"] == "succeeded"
    assert result["actual_identity"]["sha256"] == FILE_SHA256
    assert result["actual_identity"]["commit_sha"] == COMMIT
    assert result["workspace_relative_path"].startswith("resource/")
    assert (tmp_path / result["workspace_relative_path"]).read_bytes() == FILE_BYTES


def test_wrong_expected_commit_fails_before_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
            "expected_commit_sha": OTHER_COMMIT,
        },
        client,
    )
    assert result["failure_code"] == "resource_identity_mismatch"
    assert client.snapshot_calls == 0
    assert not (tmp_path / "resource").exists()


def test_wrong_expected_file_digest_fails_and_cleans_partial_resource(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_file",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "main",
            "filename": "senteval.tar",
            "expected_sha256": "0" * 64,
        },
        client,
    )
    assert result["failure_code"] == "resource_identity_mismatch"
    assert not (tmp_path / "resource").exists()
    assert not list(tmp_path.glob(".ra-*"))


def test_provider_failure_after_partial_write_leaves_no_formal_resource(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(fail_after_partial=True)
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
        },
        client,
    )
    assert result["failure_code"] == "resource_acquisition_failed"
    assert not (tmp_path / "resource").exists()
    assert not list(tmp_path.glob(".ra-*"))


def test_same_resource_target_conflict_fails_without_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    request = {
        "resource_kind": "huggingface_snapshot",
        "repo_id": "example/model",
        "repo_type": "model",
        "revision": "main",
    }
    first = _run(tmp_path, monkeypatch, request, client)
    target = tmp_path / first["workspace_relative_path"]
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    second = _run(tmp_path, monkeypatch, request, client)
    assert second["failure_code"] == "resource_target_conflict"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert client.snapshot_calls == 1


def test_model_and_dataset_materialize_to_independent_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
        },
        FakeClient(),
    )
    dataset = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "main",
        },
        FakeClient(),
    )
    assert model["state"] == dataset["state"] == "succeeded"
    assert model["workspace_relative_path"] != dataset["workspace_relative_path"]
    assert (tmp_path / model["workspace_relative_path"] / "config.json").is_file()
    assert (tmp_path / dataset["workspace_relative_path"] / "config.json").is_file()


def test_snapshot_provider_local_dir_uses_short_internal_staging_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / ("workspace-" + "x" * 80)
    workspace.mkdir()
    client = FakeClient()
    result = _run(
        workspace,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
        },
        client,
    )
    assert result["state"] == "succeeded"
    assert len(client.snapshot_local_dirs) == 1
    relative = client.snapshot_local_dirs[0].relative_to(workspace)
    assert len(relative.parts) == 2
    assert relative.parts[0].startswith(".ra-")
    assert relative.parts[1] == "r"
    assert len(relative.as_posix()) <= 16


@pytest.mark.parametrize(
    "filename",
    ["../escape.bin", "/absolute.bin", "folder/../escape.bin", "folder\\escape.bin"],
)
def test_invalid_file_path_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_file",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "main",
            "filename": filename,
        },
        client,
    )
    assert result["failure_code"] == "skill_input_invalid"
    assert client.file_calls == 0
    assert not (tmp_path / "resource").exists()


def test_workspace_mismatch_fails_before_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(other))
    client = FakeClient()
    monkeypatch.setattr(adapter, "_load_huggingface_client", lambda: client)
    result = adapter.run_resource_identity_acquisition(
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
        }
    )
    assert result["failure_code"] == "workspace_boundary_invalid"
    assert client.snapshot_calls == 0


def test_cleanup_failure_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(fail_after_partial=True)
    monkeypatch.setattr(adapter, "_cleanup_staging", lambda staging: False)
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
        },
        client,
    )
    assert result["failure_code"] == "resource_cleanup_failed"
    assert result["cleanup_triggered_by"] == "resource_acquisition_failed"
    assert not (tmp_path / "resource").exists()


def test_missing_provider_client_fails_preflight_with_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(tmp_path))
    monkeypatch.setattr(adapter, "_installed_huggingface_version", lambda: None)
    result = adapter.run_resource_identity_acquisition(
        {
            "resource_kind": "huggingface_file",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "main",
            "filename": "senteval.tar",
        }
    )
    assert result == {
        "state": "failed",
        "failure_code": "provider_client_not_installed",
        "failed_stage": "provider_preflight",
        "provider_client": "huggingface_hub",
        "required_version": ">=1.26,<2",
    }
    assert not (tmp_path / "resource").exists()


def test_installed_but_unloadable_provider_client_is_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(tmp_path))
    monkeypatch.setattr(adapter, "_installed_huggingface_version", lambda: "1.26.1")
    monkeypatch.setattr(adapter, "_load_huggingface_client", lambda: None)
    result = adapter.run_resource_identity_acquisition(
        {
            "resource_kind": "huggingface_file",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "main",
            "filename": "senteval.tar",
        }
    )
    assert result["failure_code"] == "provider_client_unavailable"
    assert result["failed_stage"] == "provider_preflight"
    assert result["provider_client_version"] == "1.26.1"
    assert result["required_version"] == ">=1.26,<2"
    assert not (tmp_path / "resource").exists()


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(401, "provider_authentication_required"), (403, "provider_access_denied")],
)
def test_provider_authentication_and_access_failures_are_distinct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_code: str,
) -> None:
    client = FakeClient()
    provider_error = type("HfHubHTTPError", (Exception,), {})
    error = provider_error("provider rejected credentials")
    error.response = SimpleNamespace(status_code=status_code)

    def fail_repo_info(*args: Any, **kwargs: Any) -> Any:
        raise error

    client.api.repo_info = fail_repo_info  # type: ignore[method-assign]
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/private",
            "repo_type": "model",
            "revision": "main",
        },
        client,
    )
    assert result["failure_code"] == expected_code
    assert result["failed_stage"] == "resolve_revision"
    assert not (tmp_path / "resource").exists()


def test_gated_repository_is_reported_as_access_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    provider_error = type("GatedRepoError", (Exception,), {})

    def fail_repo_info(*args: Any, **kwargs: Any) -> Any:
        raise provider_error("gated repository")

    client.api.repo_info = fail_repo_info  # type: ignore[method-assign]
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/gated",
            "repo_type": "model",
            "revision": "main",
        },
        client,
    )
    assert result["failure_code"] == "provider_access_denied"
    assert result["failed_stage"] == "resolve_revision"
    assert not (tmp_path / "resource").exists()


def test_network_failure_is_not_misreported_as_bad_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class ConnectError(Exception):
        pass

    client = FakeClient()

    def fail_repo_info(*args: Any, **kwargs: Any) -> Any:
        raise ConnectError("network down")

    client.api.repo_info = fail_repo_info  # type: ignore[method-assign]
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_file",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "main",
            "filename": "senteval.tar",
        },
        client,
    )
    assert result["failure_code"] == "provider_unavailable"
    assert result["failed_stage"] == "resolve_revision"
    assert not (tmp_path / "resource").exists()


@pytest.mark.parametrize(
    ("error_name", "expected_code"),
    [
        ("RepositoryNotFoundError", "resource_unavailable"),
        ("RevisionNotFoundError", "resource_revision_unresolvable"),
    ],
)
def test_invalid_remote_resource_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_name: str,
    expected_code: str,
) -> None:
    client = FakeClient()
    provider_error = type(error_name, (Exception,), {})

    def fail_repo_info(*args: Any, **kwargs: Any) -> Any:
        raise provider_error("provider rejected resource")

    client.api.repo_info = fail_repo_info  # type: ignore[method-assign]
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/missing",
            "repo_type": "model",
            "revision": "missing-revision",
        },
        client,
    )
    assert result["failure_code"] == expected_code
    assert result["failed_stage"] == "resolve_revision"
    assert not (tmp_path / "resource").exists()
    assert not list(tmp_path.glob(".ra-*"))


def test_partial_snapshot_returned_as_success_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(partial_snapshot_without_error=True)
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_snapshot",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "main",
        },
        client,
    )
    assert result["failure_code"] == "resource_incomplete"
    assert result["failed_stage"] == "materialization"
    assert not (tmp_path / "resource").exists()
    assert not list(tmp_path.glob(".ra-*"))


@pytest.mark.parametrize("version", ["1.25.1", "2.0.0"])
def test_unsafe_huggingface_client_version_fails_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    client = FakeClient(version=version)
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resource_kind": "huggingface_file",
            "repo_id": "example/dataset",
            "repo_type": "dataset",
            "revision": "main",
            "filename": "senteval.tar",
        },
        client,
    )
    assert result["failure_code"] == "provider_client_unsupported"
    assert result["failed_stage"] == "provider_preflight"
    assert result["provider_client"] == "huggingface_hub"
    assert result["provider_client_version"] == version
    assert result["required_version"] == ">=1.26,<2"
    assert not client.api.calls
    assert client.file_calls == 0
    assert not (tmp_path / "resource").exists()

def test_batch_acquires_multiple_resources_without_merging_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resources": [
                {
                    "usage": "model",
                    "resource_kind": "huggingface_snapshot",
                    "repo_id": "example/model",
                    "repo_type": "model",
                    "revision": "main",
                },
                {
                    "usage": "dataset",
                    "resource_kind": "huggingface_file",
                    "repo_id": "example/dataset",
                    "repo_type": "dataset",
                    "revision": "release",
                    "filename": "senteval.tar",
                },
            ]
        },
        client,
    )

    assert result["state"] == "succeeded"
    assert result["summary"] == {"total": 2, "succeeded": 2, "failed": 0}
    assert [row["usage"] for row in result["resources"]] == ["model", "dataset"]
    model = result["resources"][0]["result"]
    dataset = result["resources"][1]["result"]
    assert model["actual_identity"] == {"kind": "huggingface_commit", "commit_sha": COMMIT}
    assert dataset["actual_identity"]["kind"] == "huggingface_file_at_commit"
    assert model["workspace_relative_path"] != dataset["workspace_relative_path"]


def test_batch_partial_failure_preserves_successful_resource_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resources": [
                {
                    "usage": "model",
                    "resource_kind": "huggingface_snapshot",
                    "repo_id": "example/model",
                    "repo_type": "model",
                    "revision": "main",
                },
                {
                    "usage": "dataset",
                    "resource_kind": "huggingface_file",
                    "repo_id": "example/dataset",
                    "repo_type": "dataset",
                    "revision": "release",
                    "filename": "senteval.tar",
                    "expected_commit_sha": OTHER_COMMIT,
                },
            ]
        },
        client,
    )

    assert result["state"] == "partial_failure"
    assert result["summary"] == {"total": 2, "succeeded": 1, "failed": 1}
    assert result["resources"][0]["result"]["state"] == "succeeded"
    assert result["resources"][1]["result"]["failure_code"] == "resource_identity_mismatch"
    assert (tmp_path / result["resources"][0]["result"]["workspace_relative_path"]).exists()


def test_invalid_batch_is_rejected_before_any_resource_is_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "resources": [
                {
                    "usage": "model",
                    "resource_kind": "huggingface_snapshot",
                    "repo_id": "example/model",
                    "repo_type": "model",
                    "revision": "main",
                },
                {
                    "usage": "dataset",
                    "resource_kind": "huggingface_file",
                    "repo_id": "example/dataset",
                    "repo_type": "dataset",
                    "revision": "release",
                },
            ]
        },
        client,
    )

    assert result == {
        "state": "failed",
        "failure_code": "skill_input_invalid",
        "failed_stage": "input_validation",
    }
    assert client.snapshot_calls == 0
    assert client.file_calls == 0
    assert not (tmp_path / "resource").exists()
