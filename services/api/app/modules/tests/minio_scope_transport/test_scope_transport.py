"""Storage identity must survive an explicit host/Docker transport mapping."""

import json
from io import BytesIO

import pytest
from app.config import settings
from app.modules import minio_client

SCOPE = "minio+http://docker-store:9000/historical-bucket"


class _Response(BytesIO):
    def release_conn(self) -> None:
        return None


@pytest.mark.parametrize("scheme,secure", [("http", False), ("https", True)])
def test_exact_mapping_reads_original_bucket_key_and_protocol(
    monkeypatch: pytest.MonkeyPatch, scheme: str, secure: bool
) -> None:
    scope = f"minio+{scheme}://docker-store:9000/historical-bucket"
    monkeypatch.setattr(
        settings, "minio_scope_endpoint_overrides", json.dumps({scope: "host:9000"})
    )
    monkeypatch.setattr(settings, "minio_bucket", "other-current-bucket")
    calls = []

    class _Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def get_object(self, bucket: str, key: str):
            assert bucket == "historical-bucket"
            assert key == "original/object.json"
            return _Response(b"original validated research bytes")

    monkeypatch.setattr(minio_client, "Minio", _Client)
    result = minio_client.download_bytes("original/object.json", storage_scope=scope)
    assert result == b"original validated research bytes"
    assert calls[0]["endpoint"] == "host:9000"
    assert calls[0]["secure"] is secure


def test_unmapped_scope_never_falls_back_to_current_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings, "minio_scope_endpoint_overrides", json.dumps({SCOPE: "host:9000"})
    )
    monkeypatch.setattr(settings, "minio_endpoint", "unrelated-current:9000")
    actual = []

    class _Client:
        def __init__(self, **kwargs):
            actual.append(kwargs)

    monkeypatch.setattr(minio_client, "Minio", _Client)
    _, bucket = minio_client._client_and_bucket_for_scope(
        "minio+http://docker-store:9000/another-bucket"
    )
    assert actual[0]["endpoint"] == "docker-store:9000"
    assert bucket == "another-bucket"


def test_mapping_also_applies_to_current_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "minio_endpoint", "docker-store:9000")
    monkeypatch.setattr(settings, "minio_bucket", "historical-bucket")
    monkeypatch.setattr(settings, "minio_secure", False)
    monkeypatch.setattr(
        settings, "minio_scope_endpoint_overrides", json.dumps({SCOPE: "host:9000"})
    )
    calls = []

    class _Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    def unexpected_singleton():
        pytest.fail("mapped endpoint must not use the unmapped singleton")

    monkeypatch.setattr(minio_client, "Minio", _Client)
    monkeypatch.setattr(minio_client, "get_minio_client", unexpected_singleton)
    _, bucket = minio_client._client_and_bucket_for_scope(SCOPE)
    assert calls[0]["endpoint"] == "host:9000"
    assert bucket == "historical-bucket"


@pytest.mark.parametrize(
    "raw",
    [
        "invalid-json",
        "[]",
        json.dumps({SCOPE: 9000}),
        json.dumps({SCOPE: ""}),
        json.dumps({SCOPE: "https://host:9000"}),
        json.dumps({SCOPE: "host:9000/other-bucket"}),
        json.dumps({SCOPE: "credentials@host:9000"}),
        json.dumps({"not-a-scope": "host:9000"}),
    ],
)
def test_invalid_mapping_cannot_change_storage_identity(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setattr(settings, "minio_scope_endpoint_overrides", raw)
    with pytest.raises(ValueError):
        minio_client._client_and_bucket_for_scope(SCOPE)


def test_empty_configuration_keeps_historical_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "minio_scope_endpoint_overrides", "")
    assert minio_client._scope_transport_endpoint(SCOPE, "docker-store:9000") == "docker-store:9000"
