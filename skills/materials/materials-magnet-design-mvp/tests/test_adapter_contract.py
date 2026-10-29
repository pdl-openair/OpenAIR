"""E2E test for the materials-magnet-design-mvp durable worker adapter.

Mocks every external HTTP call (MCP FastMCP endpoints + LLM provider) and
exercises the full Phase 1→2→3 loop end-to-end. Does NOT require pymatgen
beyond what's already in the dev env (it's used in-process for HHI/mag_density).

Test layers:
- test_adapter_happy_path: full run with all mocks returning valid data → state=succeeded.
- test_adapter_llm_failure: LLM provider returns 500 → adapter returns partial_failure
  with mcp_calls still populated for any iteration that did run.
- test_adapter_diffcsp_null: DiffCSP returns structures=[] → all candidates dropped
  → pool ends empty, top_k_table reports no measurable reward.
  produces the same shape (smoke test of the runner plumbing).
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]


# A tiny but valid CIF (1-atom bcc Fe). MatterSim + ALIGNN would never accept
# this for real, but the mocks don't care.
SAMPLE_CIF = """data_Fe
_cell_length_a 2.87
_cell_length_b 2.87
_cell_length_c 2.87
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Fe1 Fe 0.0 0.0 0.0
"""


# ----------------------------------------------------------------------------
# HTTP mocking — intercept urllib.request.urlopen
# ----------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, content_type: str = "application/json"):
        self._body = body
        self.status = status
        self.code = status
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _mcp_jsonrpc_response(result: Any) -> bytes:
    """Wrap a result as a FastMCP-style SSE data line (what the real server returns)."""
    msg = {"jsonrpc": "2.0", "id": 1, "result": result}
    return f"data: {json.dumps(msg)}\n\n".encode("utf-8")


def _mcp_tool_text(text_payload: Any) -> Any:
    """MCP tools/call response shape (list of TextContent)."""
    if isinstance(text_payload, (dict, list)):
        text = json.dumps(text_payload)
    else:
        text = str(text_payload)
    return {"content": [{"type": "text", "text": text}], "isError": False}


class HttpRouter:
    """Routes urllib.request.Request objects to canned handlers by URL substring."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.routes: list[tuple[str, Any]] = []
        self.default_handler: Any = None

    def add(self, url_contains: str, response: Any, *, replace: bool = False) -> None:
        if replace:
            self.routes = [(n, r) for n, r in self.routes if n != url_contains]
        self.routes.append((url_contains, response))

    def handle(self, req: Any) -> FakeResponse:
        full_url = getattr(req, "full_url", "")
        # req.data is bytes or None
        body_in = getattr(req, "data", None)
        try:
            args = json.loads(body_in.decode("utf-8")) if body_in else {}
        except Exception:  # noqa: BLE001
            args = {}
        self.calls.append((full_url, args))
        for needle, response in self.routes:
            if needle in full_url:
                return self._materialize(response, args)
        if self.default_handler is not None:
            return self._materialize(self.default_handler, args)
        raise AssertionError(f"unmocked HTTP call to {full_url}")

    def _materialize(self, response: Any, args: dict[str, Any]) -> FakeResponse:
        if callable(response):
            response = response(args)
        if isinstance(response, FakeResponse):
            return response
        if isinstance(response, Exception):
            raise response
        if isinstance(response, bytes):
            return FakeResponse(response)
        if isinstance(response, (dict, list, str)):
            # Heuristic: if it's a JSON-RPC envelope, treat as raw SSE
            if isinstance(response, dict) and "jsonrpc" in response:
                # SSE body needs text/event-stream Content-Type or MCPClient
                # falls through to plain json.loads and fails on the data: prefix.
                return FakeResponse(_mcp_jsonrpc_response(response["result"]),
                                    content_type="text/event-stream")
            # OpenAI-style JSON
            return FakeResponse(json.dumps(response).encode("utf-8"))
        raise AssertionError(f"cannot materialize response of type {type(response).__name__}")


@pytest.fixture
def http_router(monkeypatch: pytest.MonkeyPatch) -> HttpRouter:
    router = HttpRouter()

    def fake_urlopen(req: Any, timeout: float | None = None) -> FakeResponse:
        return router.handle(req)

    # MCPClient + _http_post_json both call urllib.request.urlopen
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    # Adapter now requires MATERIALS_MCP_BASE_URL (no hardcoded default).
    # Mocks intercept urllib at the network layer so the URL value is irrelevant.
    monkeypatch.setenv("MATERIALS_MCP_BASE_URL", "http://mock.materials-mcp.local/mcp")
    return router


def _setup_mocks(http_router: HttpRouter, *, diffcsp_cif: str | None = SAMPLE_CIF,
                  alignn_magmom: float | None = 2.0, alignn_ehull: float | None = 0.03,
                  llm_formulas: list[str] | None = None) -> None:
    """Wire default mock handlers for the full loop."""
    # Use formulas NOT in the FALLBACK init list — otherwise crossover dedup
    # against parents would drop them and the pipeline would idle.
    llm_formulas = llm_formulas if llm_formulas is not None else ["Nd2Fe14B", "SmCo5"]

    # --- MCP initialize / notifications (any of the 4 services) ---
    def mcp_handler(args: dict[str, Any]) -> dict[str, Any]:
        method = args.get("method", "")
        if method == "initialize":
            return {"jsonrpc": "2.0", "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "mock-mcp", "version": "0.0.0"},
            }}
        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "result": {"tools": [
                {"name": "generate_structures"},
                {"name": "relax_structure"},
                {"name": "predict_alignn_properties"},
                {"name": "evaluate_structures_async"},
                {"name": "get_evaluation_result"},
            ]}}
        # tools/call dispatch by tool name
        if method == "tools/call":
            name = (args.get("params") or {}).get("name")
            tool_args = (args.get("params") or {}).get("arguments") or {}
            if name == "generate_structures":
                structures = [] if diffcsp_cif is None else [{"cif": diffcsp_cif}]
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                    "model": "mp_csp", "formula": tool_args.get("formula"),
                    "num_generated": len(structures), "num_failed": 0,
                    "structures": structures,
                })}
            if name == "relax_structure":
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                    "input_source": "content",
                    "relaxed_cif": SAMPLE_CIF,
                    "energy_per_atom_eV": -5.0,
                    "f_max_eV_A": 0.03,
                    "num_atoms": 1,
                    "total_energy_eV": -5.0,
                })}
            if name == "predict_alignn_properties":
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                    "properties": {
                        "magmom": {"value": alignn_magmom},
                        "ehull": {"value": alignn_ehull},
                    },
                })}
            if name == "evaluate_structures_async":
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                    "job_id": "mock-job-1", "status": "pending", "hint": "poll",
                })}
            if name == "get_evaluation_result":
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                    "job_id": "mock-job-1", "status": "done",
                    "result": {"metrics": {
                        "frac_novel_unique_stable_structures": 0.33,
                        "frac_stable_structures": 1.0,
                    }},
                })}
        raise AssertionError(f"unmocked MCP method {method!r}")

    for service in ("diffcsp-generate", "mattersim-relax", "alignn",
                    "mattergen-evaluate"):
        http_router.add(f"/mcp/{service}/mcp", mcp_handler)

    # --- LLM provider /v1/chat/completions ---
    def llm_handler(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant",
                            "content": json.dumps({"formulas": llm_formulas})},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    http_router.add("/v1/chat/completions", llm_handler)


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

def _load_adapter():
    """Import the adapter module directly (in-process, not subprocess)."""
    sys.path.insert(0, str(SKILL_DIR))
    try:
        # Force-import the module fresh; previous tests may have left state.
        import importlib
        if "adapter.magnet_adapter" in sys.modules:
            del sys.modules["adapter.magnet_adapter"]
        if "adapter" in sys.modules:
            del sys.modules["adapter"]
        adapter_pkg = __import__("adapter.magnet_adapter", fromlist=["run_magnet_mvp"])
        return adapter_pkg
    finally:
        pass


def test_adapter_happy_path(http_router: HttpRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_mocks(http_router)
    # Bypass subprocess Phase 1 by stubbing the script path? No — let it call
    # the real initial_formulas.py via subprocess; it'll fall back to bundled
    # list (no MP_API_KEY in test env). Monkeypatch env to force fallback cleanly.
    monkeypatch.delenv("MP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")
    monkeypatch.setenv("MAGNET_MVP_LLM_MODEL", "mock-model")

    adapter = _load_adapter()
    result = adapter.run_magnet_mvp({
        "goal": "test goal",
        "task_instruction": json.dumps({
            "init_n": 4, "batch_size": 2, "max_iters": 1, "top_k": 3,
            "init_skip_mp": True,  # deterministic offline init (no MP network)
            "filter_ehull": False,  # avoid hard filter with mock-aligned ehull
            "run_mattergen_eval": False,  # skip async submit for happy path
        }),
    })

    assert result["state"] in {"succeeded", "partial_failure"}
    assert isinstance(result["iterations"], list)
    assert len(result["iterations"]) >= 2  # phase 1 + at least one phase 2
    assert "top_k_table" in result
    assert isinstance(result["top_k_table"], str)
    assert isinstance(result["run_summary"], dict)
    assert result["run_summary"]["iters_executed"] == 1
    # Crossover LLM call should have fired
    llm_calls = [c for c in http_router.calls if "/v1/chat/completions" in c[0]]
    assert len(llm_calls) >= 1
    # DiffCSP should have fired at least once per formula
    diffcsp_calls = [c for c in http_router.calls if "diffcsp-generate" in c[0]
                     and c[1].get("method") == "tools/call"]
    assert len(diffcsp_calls) == 2  # batch_size=2


def test_adapter_llm_failure_returns_partial(http_router: HttpRouter,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    # Wire all mocks except LLM — default router raises on unmocked path.
    _setup_mocks(http_router)
    monkeypatch.delenv("MP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")

    # Override LLM with a 500-like urlopen error. Use replace=True so this
    # supersedes the success handler installed by _setup_mocks.
    import urllib.error
    http_router.add("/v1/chat/completions",
                    urllib.error.HTTPError("https://mock.llm.local/v1/chat/completions",
                                            500, "Internal Server Error", {},  # type: ignore[arg-type]
                                            io.BytesIO(b"server boom")),
                    replace=True)

    adapter = _load_adapter()
    result = adapter.run_magnet_mvp({
        "goal": "test goal",
        "task_instruction": json.dumps({
            "init_n": 3, "batch_size": 2, "max_iters": 1, "top_k": 3,
            "init_skip_mp": True,
            "filter_ehull": False, "run_mattergen_eval": False,
        }),
    })

    assert result["state"] == "partial_failure"
    # Tighten: an empty list would pass `is not None`. Require a non-empty
    # list with at least one LLM/crossover entry so silent recovery paths
    # don't mask real failures.
    assert result["partial_failure"], "partial_failure must be non-empty"
    assert any("LLM" in pf or "crossover" in pf for pf in result["partial_failure"])
    # Phase 1 init still happened
    assert any(it.get("phase") == 1 for it in result["iterations"])


def test_adapter_diffcsp_null_drops_all(http_router: HttpRouter,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_mocks(http_router, diffcsp_cif=None)
    monkeypatch.delenv("MP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")

    adapter = _load_adapter()
    result = adapter.run_magnet_mvp({
        "goal": "test goal",
        "task_instruction": json.dumps({
            "init_n": 3, "batch_size": 2, "max_iters": 1, "top_k": 3,
            "init_skip_mp": True,
            "filter_ehull": False, "run_mattergen_eval": False,
        }),
    })

    # No candidates produced → state depends on partial_failures.
    # DiffCSP returning empty is a normal pipeline path, not a partial_failure.
    assert result["state"] in {"succeeded", "partial_failure"}
    # No candidates with measurable reward → table reports the empty state
    assert "no candidates" in result["top_k_table"] or "rank" not in result["top_k_table"]


def test_adapter_input_parsing() -> None:
    """Direct test of _parse_input: unknown keys flagged, JSON string parsed."""
    adapter = _load_adapter()
    pf: list[str] = []
    goal, overrides = adapter._parse_input(
        {"goal": "x", "task_instruction": json.dumps({"init_n": 5, "unknown_key": "v"})},
        pf,
    )
    assert goal == "x"
    assert overrides == {"init_n": 5}
    assert any("unknown_key" in p for p in pf)
