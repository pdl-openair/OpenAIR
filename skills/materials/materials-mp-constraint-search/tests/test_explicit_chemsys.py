from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


_SCRIPT = Path(__file__).parents[1] / "scripts" / "search_mp_candidates.py"
_SPEC = importlib.util.spec_from_file_location("search_mp_candidates", _SCRIPT)
assert _SPEC and _SPEC.loader
search_mp_candidates = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(search_mp_candidates)


def test_exact_chemsys_admission_rejects_wrong_systems() -> None:
    rows = [
        {"material_id": "mp-lfp", "element_set": ["Li", "Fe", "P", "O"], "sg_number": 2},
        {"material_id": "mp-batio", "element_set": ["Ba", "Ti", "O"], "sg_number": 2},
        {"material_id": "mp-basc", "element_set": ["Ba", "Sc", "O"], "sg_number": 2},
    ]

    admitted = search_mp_candidates.client_filter(
        rows,
        banned_sg=[1],
        required_elements=["Li", "Fe", "P", "O"],
    )

    assert [row["material_id"] for row in admitted] == ["mp-lfp"]


def test_mp_request_carries_exact_chemsys_and_matching_cardinality(monkeypatch) -> None:
    requests: list[dict] = []

    class _Summary:
        def search(self, **kwargs):
            requests.append(kwargs)
            return []

    class _MPRester:
        def __init__(self, _api_key: str) -> None:
            self.materials = types.SimpleNamespace(summary=_Summary())

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    mp_api = types.ModuleType("mp_api")
    mp_client = types.ModuleType("mp_api.client")
    mp_client.MPRester = _MPRester
    monkeypatch.setitem(sys.modules, "mp_api", mp_api)
    monkeypatch.setitem(sys.modules, "mp_api.client", mp_client)

    search_mp_candidates.query_mp(
        "test-key",
        bandgap=(0.2, 3.5),
        formation_energy_max=0.0,
        ehull_max=1.0,
        num_elements=(4, 4),
        banned_elements=[],
        chemsys="Fe-Li-O-P",
    )

    assert requests[0]["chemsys"] == "Fe-Li-O-P"
    assert requests[0]["num_elements"] == (4, 4)
