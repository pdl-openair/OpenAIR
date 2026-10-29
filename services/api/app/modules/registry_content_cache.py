"""Reuse admission only for byte-identical Registry content."""

import json
from functools import lru_cache
from hashlib import sha256


@lru_cache(maxsize=8)
def snapshot_for_content(serialized: str):
    from app.modules.registry_snapshot_repository import (
        RegistrySnapshot,
        canonicalize_envelope,
    )

    canonical = canonicalize_envelope(json.loads(serialized))
    return RegistrySnapshot(
        schema_version=json.loads(canonical)["schemaVersion"],
        content_hash=f"sha256:{sha256(canonical).hexdigest()}",
        canonical_payload=canonical,
    )
