"""flux-experiment: 云实验平台试验任务下发的 durable_agent 执行入口.

durable_agent 实现：LLM agent 读取 S3 候选与 S7 DFT 结论，为 top 候选
生成助熔剂法实验推荐方案（recipes JSON），经 flux_api.py 离线校验后
调用云实验平台（CrystalFlux）开放 API 创建并启动试验任务，产出
dispatch_report.md。仅完成下发，不做任务处理与结果回收。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径解析（docker 扁平镜像的 /app 没有 services/，只认 skills/）
# ---------------------------------------------------------------------------

_REPO_ROOT: Path | None = None


def _repo_root() -> Path:
    global _REPO_ROOT
    if _REPO_ROOT is None:
        p = Path(__file__).resolve()
        for parent in p.parents:
            if (parent / "skills").is_dir():
                _REPO_ROOT = parent
                break
        if _REPO_ROOT is None:
            raise RuntimeError("repo root not found")
    return _REPO_ROOT


def _ensure_shared_on_path() -> None:
    shared = str(_repo_root() / "skills")
    if shared not in sys.path:
        sys.path.insert(0, shared)


def _flux_api() -> Path:
    return _repo_root() / "skills" / "materials" / "flux-experiment" / "scripts" / "flux_api.py"


def _python() -> str:
    return os.environ.get("MATERIALS_PY") or os.environ.get("EVOLVE_PY") or sys.executable


def _append_progress(run_dir: Path, message: str) -> None:
    """向工作单元的 progress.md 追加里程碑（前端实时轮询渲染，写失败忽略）。"""
    try:
        path = run_dir.parent / "progress.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- **[{time.strftime('%H:%M:%S')}]** {message}\n")
    except OSError:
        pass


def _run_flux_cli(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    """调 flux_api.py 子命令（--json 机器可读输出），返回解析后的 JSON。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [_python(), "-I", str(_flux_api()), *args, "--json"],
        capture_output=True,
        env=env,
        timeout=timeout,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    try:
        payload = json.loads(stdout)
    except ValueError:
        payload = {"ok": False, "parse_error": True, "stdout_tail": stdout[-500:], "stderr_tail": stderr[-500:]}
    if isinstance(payload, dict):
        payload["_returncode"] = proc.returncode
    return payload


# ---------------------------------------------------------------------------
# Tool functions（registered into agent loop）
# ---------------------------------------------------------------------------

def _tool_read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"[文件不存在: {path}]"
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:20000]
    except Exception as exc:  # noqa: BLE001
        return f"[读取失败: {exc}]"


def _tool_list_files(dir_path: str) -> list[str]:
    p = Path(dir_path)
    if not p.exists():
        return [f"[目录不存在: {dir_path}]"]
    return sorted(str(f) for f in p.rglob("*") if f.is_file())


def _tool_probe_platform(run_dir: Path) -> Any:
    def _probe() -> dict[str, Any]:
        result = _run_flux_cli(["probe"], timeout=45)
        reachable = bool(result.get("reachable")) and result.get("_returncode") == 0
        _append_progress(
            run_dir,
            f"云实验平台连通预检：{'通过（' + str(result.get('detail') or '已连通') + '）' if reachable else '不可达，将如实降级不盲试'}",
        )
        return {"reachable": reachable, "detail": result.get("detail") or "", "baseUrl": result.get("baseUrl") or ""}
    return _probe


def _tool_validate_recipes(run_dir: Path, dispatch_state: dict[str, Any]) -> Any:
    def _validate(recipes: list[dict[str, Any]]) -> dict[str, Any]:
        # 方案由 agent 直接以结构化数组传入；落盘成 recipes.json（既是校验
        # 输入，也是可追溯产物），再走 flux_api.py validate 离线校验。
        recipes_path = run_dir / "recipes.json"
        recipes_path.write_text(
            json.dumps(recipes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = _run_flux_cli(["validate", "--recipes-file", str(recipes_path)])
        ok = bool(result.get("ok")) and result.get("_returncode") == 0
        count = int(result.get("recipeCount") or len(recipes) or 0)
        if ok:
            dispatch_state["recipe_count"] = count
            dispatch_state["recipes_validated"] = True
        _append_progress(
            run_dir,
            f"已生成 {count} 份推荐方案（校验{'通过' if ok else '未通过'}）"
            + (f"，警告 {len(result.get('warnings') or [])} 条" if ok and result.get("warnings") else ""),
        )
        return {
            "ok": ok,
            "recipeCount": count,
            "errors": result.get("errors") or [],
            "warnings": result.get("warnings") or [],
        }
    return _validate


def _tool_create_and_start_task(run_dir: Path, dispatch_state: dict[str, Any]) -> Any:
    def _create_and_start(task_name: str, recipes: list[dict[str, Any]]) -> dict[str, Any]:
        recipes_path = run_dir / "recipes.json"
        recipes_path.write_text(
            json.dumps(recipes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        created = _run_flux_cli(
            ["create", "--task-name", task_name, "--recipes-file", str(recipes_path), "--execute"],
            timeout=90,
        )
        if not (created.get("ok") and created.get("taskId")):
            _append_progress(run_dir, f"云实验平台任务创建失败（{created.get('msg') or created.get('warning') or '详见 dispatch_report'}）")
            return {
                "ok": False,
                "stage": "create",
                "detail": created.get("msg") or created.get("warning") or json.dumps(created, ensure_ascii=False)[:500],
            }
        task_id = str(created.get("taskId"))
        task_code = str(created.get("taskCode") or "")
        started = _run_flux_cli(["start", "--task-id", task_id, "--execute"], timeout=60)
        started_ok = bool(started.get("ok")) and started.get("_returncode") == 0
        dispatch_state.update(
            task_id=task_id,
            task_code=task_code,
            task_name=task_name,
            started=started_ok,
            recipe_count=len(recipes),
        )
        _append_progress(
            run_dir,
            f"实验任务已下发到云实验平台（taskId={task_id}"
            + (f"，taskCode={task_code}" if task_code else "")
            + f"{'，已启动' if started_ok else '，启动未确认——以 taskId 为准到平台核对'}）",
        )
        return {
            "ok": True,
            "taskId": task_id,
            "taskCode": task_code,
            "started": started_ok,
            "start_detail": started.get("msg") or "",
        }
    return _create_and_start


def _tool_write_report(run_dir: Path, dispatch_state: dict[str, Any]) -> Any:
    def _write_report(content: str) -> dict[str, Any]:
        out = run_dir / "dispatch_report.md"
        out.write_text(content, encoding="utf-8")
        dispatch_state["report_written"] = True
        return {"ok": True, "path": str(out)}
    return _write_report


def _build_tools(run_dir: Path, dispatch_state: dict[str, Any]) -> list[dict]:
    from shared.durable_llm_agent import make_tool
    return [
        make_tool(
            "read_file",
            "读取工作区或技能产物文件的内容",
            {"path": {"type": "string", "description": "文件绝对路径或相对路径"}},
            ["path"],
            _tool_read_file,
        ),
        make_tool(
            "list_files",
            "列出指定目录下的所有文件（递归）",
            {"dir_path": {"type": "string", "description": "目录路径"}},
            ["dir_path"],
            _tool_list_files,
        ),
        make_tool(
            "probe_platform",
            "云实验平台（CrystalFlux）连通性与鉴权预检；不可达时如实报告，不要反复重试",
            {},
            [],
            _tool_probe_platform(run_dir),
        ),
        make_tool(
            "validate_recipes",
            "离线校验实验推荐方案数组（recipes JSON）并落盘 recipes.json；返回错误/警告清单",
            {
                "recipes": {
                    "type": "array",
                    "description": "推荐方案对象数组，结构见系统提示（方案ID/方案类型/工艺参数/温度程序/溯源信息/预期结果标签）",
                    "items": {"type": "object"},
                },
            },
            ["recipes"],
            _tool_validate_recipes(run_dir, dispatch_state),
        ),
        make_tool(
            "create_and_start_task",
            "把推荐方案数组下发到云实验平台：创建试验任务并启动，返回 taskId/taskCode",
            {
                "task_name": {"type": "string", "description": "试验任务名（含材料体系与日期，便于平台侧辨认）"},
                "recipes": {
                    "type": "array",
                    "description": "已通过 validate_recipes 校验的推荐方案数组",
                    "items": {"type": "object"},
                },
            },
            ["task_name", "recipes"],
            _tool_create_and_start_task(run_dir, dispatch_state),
        ),
        make_tool(
            "write_dispatch_report",
            "将最终下发报告 markdown 内容写入文件",
            {
                "content": {"type": "string", "description": "报告的完整 markdown 内容"},
            },
            ["content"],
            _tool_write_report(run_dir, dispatch_state),
        ),
    ]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
你是新材料发现链路的云实验下发 agent（S8 合成执行）。

任务目标：{goal}
工作区路径：{work_dir}

已收到的上下文摘要：
- 候选材料排名（S3 进化设计）：{candidate_summary}
- DFT 热力学稳定性结论（S7）：{stability_handoff}

执行步骤：
1. 用 probe_platform 预检云实验平台连通性；不可达就如实说明并停止（写报告标注 ⚠️ 平台不可达），不要盲目重试
2. 如上下文不足，用 list_files / read_file 探索工作区（S3 的 leaderboard/pareto、S7 的稳定性报告）
3. 从 top 候选中选 1-2 个（优先 DFT 判定 stable/metastable 的第一名），为每个候选生成一份助熔剂法生长推荐方案（recipes 数组）
4. 用 validate_recipes 校验方案；有 ERROR 必须修正后重新校验
5. 用 create_and_start_task 下发（任务名含材料体系与日期）
6. 用 write_dispatch_report 写 dispatch_report.md

recipes 数组结构（每个元素一份方案，完整字段样例见 {examples_path}）：
- 方案ID：字符串（如 方案_A），方案类型：baseline / optimize / control 等
- 方案给人的一句话说明：一句话讲清该方案定位
- 工艺参数：
  - 原料信息 / 原料标准化（按目标化学式化学计量比）
  - 助熔剂信息 / 助熔剂标准化（格式如 Na:(元素和)=2.7:1，须含数值比例）
  - 容器（如 Alumina crucible）、籽晶
  - 分离与后处理：分离方式 / 分离温度_摄氏 / 晶体的进一步处理
  - 温度程序：是否存在次高温预反应段、低温段保温温度/时间、升温到次高温时间、
    次高温段温度/保温时间、升温到最高温时间、最高温段保温温度/保温时间、
    降温速率_主降温_℃每小时、降温时间_主降温_h、冷却速率_至室温_标签
- 溯源信息：主要参考配方ID / 参考方案类型ID-名称 / 参考材料ID-化学式（无真实来源时如实写本链路内部来源）
- 预期结果标签：预期晶体尺寸 / 预期风险水平 / 风险来源简述

方案设计要求（物理一致性，校验器会查）：
- 最高温段保温温度必须高于低温段与次高温段（结合体系熔点/分解温度合理设定）
- 降温速率 × 主降温时间 ≈ 从最高温降到低温段的温差（偏差过大会告警）
- 主降温时长超过 72h 会告警平台单任务时长上限
- 助熔剂比例须写出具体数值（如 2.7:1）

报告结构（dispatch_report.md，结论先行）：
```
# 云实验平台任务下发报告

## 核心结论
（一句话：为哪些候选下发了实验任务，taskId 是什么）

## 下发任务信息
（taskId / taskCode / 任务名 / 方案数 / 创建与启动状态）

## 候选-方案对照
（表格：候选化学式、DFT 结论、方案类型、助熔剂策略、温度程序要点）

## 后续说明
（平台侧任务处理与结果回收不在本环节；给出到平台核对任务的提示）
```

质量要求：
- 每个参数选择给出依据（化学式、DFT ehull、常见助熔剂实践）
- 平台不可达/创建失败时如实标注 ⚠️，不编造 taskId
- 语言：中文，面向不熟悉晶体生长的用户
"""


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def execute_flux_experiment_durable(
    *,
    task_id: str,
    work_unit_id: str,
    parameters: dict[str, Any],
    owner_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    """主执行函数，由 materials_skill_script_runtime 调用。"""
    _ensure_shared_on_path()
    from shared.durable_llm_agent import run_agent

    safe = lambda v: "".join(c if c.isalnum() or c in "_.-" else "_" for c in v).strip("._") or "run"
    _ws_root = os.environ.get("OPENAIR_WORKSPACE_ROOT", "").strip()
    _out_root = Path(_ws_root) if _ws_root else _repo_root() / "outputs" / "canonical-materials"
    run_dir = _out_root / safe(task_id) / safe(work_unit_id) / "flux-experiment"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "dispatch_report.md"

    goal = str(parameters.get("goal") or "").strip()
    # binding 不传 work_dir 时回退 task 级目录（run_dir 上溯两级），让 agent
    # 能 rglob 到同 task 下 S2/S3/S7 的全部产物。
    work_dir = str(parameters.get("work_dir") or run_dir.parents[1])

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        goal=goal or "新材料发现云实验下发",
        work_dir=work_dir,
        candidate_summary=str(parameters.get("candidate_summary") or "（未注入，请在工作区探索 leaderboard/top_k_table）"),
        stability_handoff=str(parameters.get("stability_handoff") or "（未注入，请在工作区探索 stability_report）"),
        examples_path=str(_repo_root() / "skills" / "materials" / "flux-experiment" / "examples" / "recipes.json"),
    )

    user_message = (
        f"请为材料发现链路的 top 候选生成实验推荐方案并下发云实验平台。\n"
        f"报告输出路径：{report_path}\n"
        f"方案与中间产物目录：{run_dir}"
    )

    dispatch_state: dict[str, Any] = {}
    result = run_agent(
        tools=_build_tools(run_dir, dispatch_state),
        system_prompt=system_prompt,
        user_message=user_message,
        output_path=str(run_dir / "agent_result.json"),
        progress_path=str(run_dir.parent / "progress.md"),
        max_iterations=15,
        meter_context=(
            {"user_id": owner_id, "task_id": task_id, "workspace_id": workspace_id}
            if owner_id
            else None
        ),
    )

    # 所有终态都保证 dispatch_report.md 存在（agent 未写出时落最小失败报告）
    if not report_path.exists():
        reason = dispatch_state.get("failure_hint") or result.get("reason") or "agent_did_not_write_report"
        report_path.write_text(
            "# 云实验平台任务下发报告\n\n"
            f"## 核心结论\n\n⚠️ 本次未能完成实验任务下发（{reason}）。\n\n"
            "- 执行状态：partial_failure\n"
            f"- 工作目录：`{run_dir}`\n"
            f"- 报告生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n",
            encoding="utf-8",
        )
        _append_progress(run_dir, "S8 结束（partial_failure，未完成下发，详见下发报告）")

    task_id_out = str(dispatch_state.get("task_id") or "")
    started = bool(dispatch_state.get("started"))
    dispatched = bool(task_id_out)
    if dispatched:
        handoff = (
            f"云实验下发完成：已为 {dispatch_state.get('recipe_count', '?')} 份推荐方案创建试验任务 "
            f"taskId={task_id_out}"
            + (f"（taskCode={dispatch_state.get('task_code')}）" if dispatch_state.get("task_code") else "")
            + ("并启动。" if started else "；启动未确认，请到平台核对。")
            + "任务处理与结果回收不在本环节。"
        )
    else:
        handoff = "云实验下发未完成（详见 flux-experiment/dispatch_report.md）；链路继续，报告环节将如实标注。"

    return {
        "state": "succeeded" if dispatched else "partial_failure",
        "task_id": task_id_out,
        "task_code": str(dispatch_state.get("task_code") or ""),
        "task_name": str(dispatch_state.get("task_name") or ""),
        "recipe_count": int(dispatch_state.get("recipe_count") or 0) if dispatched else None,
        "dispatch_report_path": str(report_path),
        "failure_reason": None if dispatched else "cloud_experiment_not_dispatched",
        "evidence": [
            {
                "step": "dispatch",
                "taskId": task_id_out or None,
                "started": started,
                "agent_state": result.get("state"),
                "iterations": result.get("iterations"),
            },
        ],
        "handoff_summary": handoff,
    }
