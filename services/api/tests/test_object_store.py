"""Unit tests for the content-addressed object store (local + minio backends)."""

from __future__ import annotations

import hashlib

import pytest

from app.config import settings
from app.modules import object_store


@pytest.fixture
def local_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    return tmp_path


def test_put_returns_sha256_and_get_roundtrips(local_backend):
    data = b"research artifact content"
    key = object_store.put_content(data)

    assert key == hashlib.sha256(data).hexdigest()
    assert object_store.content_exists(key)
    assert object_store.get_content(key) == data


def test_put_is_idempotent_dedup(local_backend):
    data = b"same bytes stored twice"
    key1 = object_store.put_content(data)
    key2 = object_store.put_content(data)

    assert key1 == key2
    blob = local_backend / "_content" / key1[:2] / key1[2:]
    assert blob.exists()
    assert object_store.get_content(key1) == data


def test_distinct_content_gets_distinct_keys(local_backend):
    a = object_store.put_content(b"aaa")
    b = object_store.put_content(b"bbb")

    assert a != b
    assert object_store.content_exists(a)
    assert object_store.content_exists(b)
    assert not object_store.content_exists("deadbeef" * 8)


def test_remove_content_is_idempotent(local_backend):
    key = object_store.put_content(b"to be removed")
    assert object_store.content_exists(key)

    object_store.remove_content(key)
    assert not object_store.content_exists(key)

    # second removal must not raise
    object_store.remove_content(key)


def test_invalid_hash_is_rejected(local_backend):
    assert not object_store.content_exists("not-a-hash")
    with pytest.raises(ValueError):
        object_store.get_content("not-a-hash")
    # remove on invalid hash is a no-op, not an error
    object_store.remove_content("not-a-hash")


def test_minio_backend_delegates_to_minio_client(monkeypatch):
    """MinIO backend keys by content/{sha256[:2]}/{sha256[2:]} and dedups."""
    monkeypatch.setattr(settings, "artifact_storage", "minio")

    existing: set[str] = set()
    calls: dict[str, object] = {}

    def fake_exists(key):
        return key in existing

    class _FakeMinio:
        def put_object(self, bucket, key, stream, length, content_type):
            existing.add(key)
            calls["put_count"] = calls.get("put_count", 0) + 1
            calls["put_key"] = key
            calls["put_bytes"] = stream.read()
            calls["bucket"] = bucket

    # minio_client is imported lazily inside the functions; patch the real module
    from app.modules import minio_client
    monkeypatch.setattr(minio_client, "object_exists", fake_exists)
    monkeypatch.setattr(minio_client, "get_minio_client", lambda: _FakeMinio())
    monkeypatch.setattr(minio_client, "download_bytes", lambda key: b"downloaded:" + key.encode())
    monkeypatch.setattr(minio_client, "remove_object", lambda key: existing.discard(key))

    data = b"minio-stored"
    key = object_store.put_content(data)
    assert key == hashlib.sha256(data).hexdigest()
    expected_key = f"content/{key[:2]}/{key[2:]}"
    assert calls["put_key"] == expected_key
    assert calls["put_bytes"] == data
    assert calls["put_count"] == 1

    # second put dedups (exists -> no re-upload)
    object_store.put_content(data)
    assert calls["put_count"] == 1

    assert object_store.get_content(key).startswith(b"downloaded:")
    object_store.remove_content(key)
    assert expected_key not in existing
