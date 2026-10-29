"""E2E tests for the crystal-design-formula-crossover durable worker adapter.

Mocks every external HTTP call (Phase 0.5 RunPlan LLM + Phase 2b crossover
LLM + all 4 model MCPs) and exercises the full Phase 0.5 → 1 → 2 → 3 loop
end-to-end. No pymatgen network calls; HHI / mag_density compute in-process.

Test layers:
- test_explicit_runplan_without_planner_llm: an already-formed structured
  RunPlan may bypass Phase 0.5 LLM without inventing scientific semantics.
- test_phase_05_runplan_llm: provide a semiconductor RunPlan via mock LLM and
  verify it shapes the executed property models.
- test_phase_05_llm_http_500_stops_before_init: planner failure is local and
  fail-closed; no keyword/default scientific plan starts the driver.
- test_skip_runplan_llm_without_explicit_plan_stops_before_init: bypass cannot
  be used as a semantic fallback.
- test_diffcsp_null: downstream driver behavior remains covered with an
  explicit RunPlan.
  RunPlan semantics.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]


# Tiny but valid CIF (1-atom bcc Fe). Mocks don't care about realism.
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
    msg = {"jsonrpc": "2.0", "id": 1, "result": result}
    return f"data: {json.dumps(msg)}\n\n".encode("utf-8")


def _mcp_tool_text(text_payload: Any) -> Any:
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
            if isinstance(response, dict) and "jsonrpc" in response:
                return FakeResponse(_mcp_jsonrpc_response(response["result"]),
                                    content_type="text/event-stream")
            return FakeResponse(json.dumps(response).encode("utf-8"))
        raise AssertionError(f"cannot materialize response of type {type(response).__name__}")


@pytest.fixture
def http_router(monkeypatch: pytest.MonkeyPatch) -> HttpRouter:
    router = HttpRouter()

    def fake_urlopen(req: Any, timeout: float | None = None) -> FakeResponse:
        return router.handle(req)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    # Adapter now requires MATERIALS_MCP_BASE_URL (no hardcoded default).
    # Mocks intercept urllib at the network layer so the URL value is irrelevant.
    monkeypatch.setenv("MATERIALS_MCP_BASE_URL", "http://mock.materials-mcp.local/mcp")
    return router


def _setup_mcp_mocks(
    http_router: HttpRouter,
    *,
    diffcsp_cif: str | None = SAMPLE_CIF,
    alignn_models_values: dict[str, Any] | None = None,
    mattergen_eval_pending: bool = True,
) -> None:
    """Wire default MCP handlers. alignn_models_values maps model name → value."""

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
                requested = tool_args.get("models") or []
                defaults = {
                    "magmom": 2.0,
                    "ehull": 0.03,
                    "bandgap": 1.5,
                    "bulk_modulus": 250.0,
                    "shear_modulus": 120.0,
                    "exfoliation_energy": 100.0,
                    "supercon_tc": 15.0,
                    "dielectric": 50.0,
                    "slme": 18.0,
                }
                values = {m: (alignn_models_values or {}).get(m, defaults.get(m))
                          for m in requested}
                props = {m: {"value": v} for m, v in values.items()}
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({"properties": props})}
            if name == "evaluate_structures_async":
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                    "job_id": "mock-job-1", "status": "pending", "hint": "poll",
                })}
            if name == "get_evaluation_result":
                if mattergen_eval_pending:
                    return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                        "job_id": "mock-job-1", "status": "pending",
                    })}
                return {"jsonrpc": "2.0", "result": _mcp_tool_text({
                    "job_id": "mock-job-1", "status": "done",
                    "result": {"metrics": {
                        "frac_novel_unique_stable_structures": 0.33,
                        "frac_stable_structures": 1.0,
                    }},
                })}
        raise AssertionError(f"unmocked MCP method {method!r}")

    for service in ("diffcsp-generate", "mattersim-relax", "alignn", "mattergen-evaluate"):
        http_router.add(f"/mcp/{service}/mcp", mcp_handler)


def _setup_llm_crossover_mock(http_router: HttpRouter, formulas: list[str]) -> None:
    """Wire /v1/chat/completions to return JSON {formulas: [...]}."""
    def llm_handler(args: dict[str, Any]) -> dict[str, Any]:
        # Peek at the system message to tell RunPlan vs crossover calls apart.
        sys_msg = ""
        try:
            sys_msg = (args.get("messages") or [{}])[0].get("content", "") or ""
        except (IndexError, AttributeError, TypeError):
            pass
        if "RunPlan planner" in sys_msg:
            # RunPlan LLM response — return a small valid plan
            return {
                "id": "chatcmpl-runplan",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({
                            "goal": args.get("messages", [{}])[-1].get("content", ""),
                            "objectives": [
                                {"name": "bandgap", "source": "alignn:bandgap",
                                 "direction": "max", "weight": 1.0,
                                 "alignn_deps": ["bandgap"], "scorer": None},
                                {"name": "hhi", "source": "scorer:hhi.py",
                                 "direction": "min", "weight": 1.0,
                                 "alignn_deps": [], "scorer": "scripts/hhi.py"},
                            ],
                            "hard_filter": [
                                {"name": "ehull", "op": "<", "value": 0.1},
                            ],
                            "crossover_hints": {
                                "swap_groups": [["Ga", "In", "Al"], ["N", "P", "As"]],
                                "prototypes": ["zincblende", "wurtzite"],
                                "avoid": ["noble gases"],
                            },
                            "init_elements": ["Ga", "In", "Al", "N", "P", "As"],
                            "fallback_formulas": ["GaN", "GaP", "InP", "AlN"],
                            "config": {"init_n": 4, "batch_size": 2, "max_iters": 1, "top_k": 3},
                        }),
                    },
                    "finish_reason": "stop",
                }],
            }
        # Crossover call
        return {
            "id": "chatcmpl-crossover",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant",
                            "content": json.dumps({"formulas": formulas})},
                "finish_reason": "stop",
            }],
        }

    http_router.add("/v1/chat/completions", llm_handler)


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

def _load_adapter():
    sys.path.insert(0, str(SKILL_DIR))
    if "adapter.crossover_adapter" in sys.modules:
        del sys.modules["adapter.crossover_adapter"]
    if "adapter" in sys.modules:
        del sys.modules["adapter"]
    return __import__("adapter.crossover_adapter", fromlist=["run_crossover_mvp"])


def _explicit_magnet_instruction(**runtime: Any) -> dict[str, Any]:
    instruction: dict[str, Any] = {
        "skip_runplan_llm": True,
        "objectives": [
            {
                "name": "mag_density",
                "source": "alignn:magmom + scorer:mag_density.py",
                "direction": "max",
                "weight": 1.0,
                "alignn_deps": ["magmom"],
                "scorer": "scripts/mag_density.py",
                "report_threshold": 0.2,
            },
            {
                "name": "hhi",
                "source": "scorer:hhi.py",
                "direction": "min",
                "weight": 1.0,
                "alignn_deps": [],
                "scorer": "scripts/hhi.py",
                "report_threshold": 1500,
            },
        ],
        "hard_filter": [{"name": "ehull", "op": "<", "value": 0.1}],
        "alignn_models": ["magmom", "ehull"],
        "crossover_hints": {
            "swap_groups": [["Fe", "Co", "Ni", "Cr", "Mn"], ["Te", "Se", "S", "O"]],
            "prototypes": ["Heusler", "ThMn12", "YCo5", "spinel", "perovskite"],
            "avoid": ["noble gases", "actinides > U", "monatomic formulas"],
        },
        "init_elements": ["Fe", "Co", "Ni", "Cr", "Mn"],
        "fallback_formulas": ["Fe3O4", "CoFe2O4", "NiFe2O4"],
    }
    instruction.update(runtime)
    return instruction


def test_run_driver_decodes_non_utf8_stderr_without_locale_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driver diagnostics stay textual even when a child emits invalid UTF-8 bytes."""
    adapter = _load_adapter()
    real_run = adapter.subprocess.run
    monkeypatch.setattr(adapter, "_RUN_SH", Path(sys.executable))

    def _run_invalid_utf8_child(_cmd: list[str], **kwargs: Any) -> Any:
        assert kwargs.get("text") is True
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"
        return real_run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.buffer.write(bytes([255])); sys.exit(7)",
            ],
            **kwargs,
        )

    monkeypatch.setattr(adapter.subprocess, "run", _run_invalid_utf8_child)

    code, stdout, stderr = adapter._run_driver("init")

    assert code == 7
    assert stdout == ""
    assert stderr == "\ufffd"

def test_explicit_runplan_without_planner_llm(
    http_router: HttpRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete explicit RunPlan may bypass planner LLM without guessing."""
    _setup_mcp_mocks(http_router)
    _setup_llm_crossover_mock(http_router, formulas=["Nd2Fe14B", "SmCo5"])
    monkeypatch.delenv("MP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")
    monkeypatch.setenv("CROSSOVER_LLM_MODEL", "mock-model")

    adapter = _load_adapter()
    result = adapter.run_crossover_mvp({
        "goal": "find high-mag-density low-HHI magnets",
        "task_instruction": json.dumps(_explicit_magnet_instruction(
            init_n=4, batch_size=2, max_iters=1, top_k=3,
            run_mattergen_eval=False,
        )),
    })

    # The subprocess MCP environment is intentionally unavailable here, so
    # driver init may end as partial_failure. The important contract is that
    # Phase 0.5 came from explicit scientific input, not a keyword fallback.
    assert result["state"] in {"succeeded", "partial_failure"}
    assert any(it.get("phase") == 0.5 for it in result["iterations"])
    assert isinstance(result["run_plan"], dict)
    obj_names = [o["name"] for o in result["run_plan"]["objectives"]]
    assert obj_names == ["mag_density", "hhi"]
    assert set(result["run_plan"]["alignn_models"]) == {"magmom", "ehull"}


def test_skip_runplan_llm_without_explicit_plan_stops_before_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skip_runplan_llm is not permission for code to invent a RunPlan."""
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://unused.llm.local")
    adapter = _load_adapter()
    monkeypatch.setattr(
        adapter,
        "_driver_init",
        lambda *args, **kwargs: pytest.fail("driver must not start without a RunPlan"),
    )

    result = adapter.run_crossover_mvp({
        "goal": "find semiconductor candidates",
        "task_instruction": {
            "skip_runplan_llm": True,
            "init_n": 3,
            "max_iters": 1,
        },
    })

    assert result["state"] == "partial_failure"
    assert result["run_plan"] == {}
    assert result["iterations"] == []
    assert result["top_k_table"] == ""
    assert any("requires an explicit scientific RunPlan" in item
               for item in result["partial_failure"] or [])


def test_phase_05_runplan_llm(http_router: HttpRouter,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 0.5 LLM returns a semiconductor RunPlan → ALIGNN models + rewards
    follow that plan (bandgap objective, no magmom)."""
    _setup_mcp_mocks(http_router)
    # Use semiconductor-style crossover formulas not in any fallback list.
    _setup_llm_crossover_mock(http_router, formulas=["GaP", "AlN"])
    monkeypatch.delenv("MP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")

    adapter = _load_adapter()
    result = adapter.run_crossover_mvp({
        "goal": "find wide-bandgap semiconductors",
        "task_instruction": json.dumps({
            "init_n": 3, "batch_size": 2, "max_iters": 1, "top_k": 3,
            "init_skip_mp": True,
            "run_mattergen_eval": False,
        }),
    })

    assert result["state"] in {"succeeded", "partial_failure"}
    run_plan = result["run_plan"]
    obj_names = [o["name"] for o in run_plan["objectives"]]
    # Phase 0.5 LLM should have produced bandgap + hhi
    assert "bandgap" in obj_names
    assert "hhi" in obj_names
    assert "magmom" not in obj_names  # default magnet plan should NOT be used
    # ALIGNN models should include bandgap (and ehull from hard filter)
    assert "bandgap" in run_plan["alignn_models"]
    assert "ehull" in run_plan["alignn_models"]


def test_phase_05_llm_http_500_stops_before_init(
    http_router: HttpRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner LLM failure stays local; no keyword/default plan is executed."""
    _setup_llm_crossover_mock(http_router, formulas=["GaP", "AlN"])

    import urllib.error
    http_router.add(
        "/v1/chat/completions",
        urllib.error.HTTPError(
            "https://mock.llm.local/v1/chat/completions",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(b"server boom"),
        ),
        replace=True,
    )

    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")

    adapter = _load_adapter()
    monkeypatch.setattr(
        adapter,
        "_driver_init",
        lambda *args, **kwargs: pytest.fail("driver must not start after planner failure"),
    )

    result = adapter.run_crossover_mvp({
        "goal": "find semiconductor candidates",
        "task_instruction": {
            "init_n": 3,
            "batch_size": 2,
            "max_iters": 1,
            "top_k": 3,
            "run_mattergen_eval": False,
        },
    })

    assert result["state"] == "partial_failure"
    assert result["run_plan"] == {}
    assert result["iterations"] == []
    assert result["top_k_table"] == ""
    failures = result["partial_failure"] or []
    assert any("LLM HTTP failed" in item for item in failures)
    assert all("keyword" not in item.lower() for item in failures)


def test_phase_05_invalid_runplan_stops_before_init(
    http_router: HttpRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incomplete planner output is rejected instead of filled from defaults."""
    http_router.add(
        "/v1/chat/completions",
        {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "goal": "find semiconductor candidates",
                        "crossover_hints": {},
                        "init_elements": ["Ga", "N"],
                    })
                }
            }]
        },
    )
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")

    adapter = _load_adapter()
    monkeypatch.setattr(
        adapter,
        "_driver_init",
        lambda *args, **kwargs: pytest.fail("driver must not start with an invalid RunPlan"),
    )
    result = adapter.run_crossover_mvp({
        "goal": "find semiconductor candidates",
        "task_instruction": {},
    })

    assert result["state"] == "partial_failure"
    assert result["run_plan"] == {}
    assert result["iterations"] == []
    failures = result["partial_failure"] or []
    assert any("objectives must be a non-empty list" in item for item in failures)
    assert any("hard_filter must be a list" in item for item in failures)


def test_empty_goal_stops_before_planner_or_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing user goal is a prerequisite gap, not a semantic default."""
    adapter = _load_adapter()
    monkeypatch.setattr(
        adapter,
        "_driver_init",
        lambda *args, **kwargs: pytest.fail("driver must not start without user goal"),
    )
    result = adapter.run_crossover_mvp({"goal": "", "task_instruction": {}})
    assert result["state"] == "partial_failure"
    assert result["run_plan"] == {}
    assert result["iterations"] == []
    assert any("input.goal empty" in item for item in result["partial_failure"] or [])


def test_diffcsp_null_drops_all(http_router: HttpRouter,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    """DiffCSP returns empty structures for every formula → all candidates
    dropped → top_k_table reports no measurable reward."""
    _setup_mcp_mocks(http_router, diffcsp_cif=None)
    _setup_llm_crossover_mock(http_router, formulas=["Nd2Fe14B", "SmCo5"])
    monkeypatch.delenv("MP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://mock.llm.local")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "mock-key")

    adapter = _load_adapter()
    result = adapter.run_crossover_mvp({
        "goal": "magnet candidates",
        "task_instruction": json.dumps(_explicit_magnet_instruction(
            init_n=3, batch_size=2, max_iters=1, top_k=3,
            run_mattergen_eval=False,
        )),
    })

    assert result["state"] in {"succeeded", "partial_failure"}
    # Empty pool → no measurable reward line
    assert "no candidates" in result["top_k_table"] or "rank" not in result["top_k_table"]


def test_input_parsing() -> None:
    """Unknown task_instruction keys flagged; JSON string parsed."""
    adapter = _load_adapter()
    pf: list[str] = []
    goal, overrides = adapter._parse_input(
        {"goal": "x", "task_instruction": json.dumps({"init_n": 5, "unknown_key": "v"})},
        pf,
    )
    assert goal == "x"
    assert overrides == {"init_n": 5}
    assert any("unknown_key" in p for p in pf)
