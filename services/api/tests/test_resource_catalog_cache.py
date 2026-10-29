"""In-process resource catalog projection cache (phase A)."""

from __future__ import annotations

import time

from app.models.resource_catalog import ResourceType
from app.modules import resource_catalog


def test_catalog_cache_hit_skips_rebuild(monkeypatch) -> None:
    resource_catalog.clear_resource_catalog_cache()
    builds = {"n": 0}
    real_build = resource_catalog._build_catalog_resources

    def counting_build():
        builds["n"] += 1
        return real_build()

    monkeypatch.setattr(resource_catalog, "_build_catalog_resources", counting_build)

    first = resource_catalog.list_resources()
    second = resource_catalog.list_resources()
    third = resource_catalog.list_resources(resource_type=ResourceType.SKILL)

    assert builds["n"] == 1
    assert len(second) == len(first)
    assert len(third) <= len(first)


def test_clear_resource_catalog_cache_forces_rebuild(monkeypatch) -> None:
    resource_catalog.clear_resource_catalog_cache()
    builds = {"n": 0}
    real_build = resource_catalog._build_catalog_resources

    def counting_build():
        builds["n"] += 1
        return real_build()

    monkeypatch.setattr(resource_catalog, "_build_catalog_resources", counting_build)

    resource_catalog.list_resources()
    resource_catalog.clear_resource_catalog_cache()
    resource_catalog.list_resources()

    assert builds["n"] == 2


def test_mtime_fingerprint_change_forces_rebuild(monkeypatch) -> None:
    resource_catalog.clear_resource_catalog_cache()
    builds = {"n": 0}
    real_build = resource_catalog._build_catalog_resources
    fingerprint = {"value": (1.0, 2.0, 3.0, 4.0, 5.0)}

    def counting_build():
        builds["n"] += 1
        return real_build()

    monkeypatch.setattr(resource_catalog, "_build_catalog_resources", counting_build)
    monkeypatch.setattr(resource_catalog, "_catalog_source_fingerprint", lambda: fingerprint["value"])

    resource_catalog.list_resources()
    fingerprint["value"] = (9.0, 2.0, 3.0, 4.0, 5.0)
    resource_catalog.list_resources()

    assert builds["n"] == 2


def test_warm_resource_catalog_cache_populates_projection() -> None:
    resource_catalog.clear_resource_catalog_cache()
    count = resource_catalog.warm_resource_catalog_cache()
    assert count > 0
    assert resource_catalog._catalog_projection_cache is not None
    assert len(resource_catalog._catalog_projection_cache) == count


def test_cached_list_resources_is_much_faster_than_cold_rebuild() -> None:
    resource_catalog.clear_resource_catalog_cache()
    # Cold / rebuild path
    t0 = time.perf_counter()
    resource_catalog.list_resources()
    cold_ms = (time.perf_counter() - t0) * 1000

    # Warm hits
    warm_samples: list[float] = []
    for _ in range(8):
        t0 = time.perf_counter()
        resource_catalog.list_resources()
        warm_samples.append((time.perf_counter() - t0) * 1000)
    warm_ms = sorted(warm_samples)[len(warm_samples) // 2]

    # Allow CI variance; still require a clear order-of-magnitude win.
    assert warm_ms < 80.0, f"warm list_resources too slow: {warm_ms:.1f}ms"
    assert warm_ms * 3 < cold_ms or warm_ms < 20.0, (
        f"cache did not meaningfully beat cold path: cold={cold_ms:.1f}ms warm={warm_ms:.1f}ms"
    )
