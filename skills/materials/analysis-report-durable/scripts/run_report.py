"""analysis-report-durable: durable_agent 执行入口.

durable_agent 实现：LLM agent 读取全链路前序产物，自主规划图表，
内联调用 figure_renderer.py 生成流程 SVG，用 matplotlib 生成数据分析 PNG，
产出 analysis_report.md（结论先行、证据支撑、用户友好）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# 把 skills/ 根加入 sys.path 以便 import shared
_REPO_ROOT: Path | None = None


def _repo_root() -> Path:
    # 只要求 skills/：docker 扁平镜像的 /app 没有 services/，双条件判定
    # 会在容器内必然 RuntimeError（同 run_stability 的 task_feb85da 教训）。
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


def _figure_renderer_path() -> Path:
    return _repo_root() / "skills" / "materials" / "figure-spec" / "scripts" / "figure_renderer.py"


def _python() -> str:
    return os.environ.get("MATERIALS_PY") or os.environ.get("EVOLVE_PY") or sys.executable


# ---------------------------------------------------------------------------
# Tool functions (registered into agent loop)
# ---------------------------------------------------------------------------

def _tool_read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"[文件不存在: {path}]"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"[读取失败: {exc}]"


def _tool_list_files(dir_path: str) -> list[str]:
    p = Path(dir_path)
    if not p.exists():
        return [f"[目录不存在: {dir_path}]"]
    return sorted(str(f) for f in p.rglob("*") if f.is_file())


def _tool_generate_pipeline_svg(spec_json: dict, output_path: str) -> dict[str, Any]:
    """调用 figure_renderer.py 将 FigureSpec JSON 渲染为 SVG。"""
    import tempfile
    renderer = _figure_renderer_path()
    if not renderer.exists():
        return {"ok": False, "error": f"figure_renderer.py not found at {renderer}"}
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(spec_json, f, ensure_ascii=False)
        spec_path = f.name
    try:
        r = subprocess.run(
            [_python(), "-I", str(renderer), "render", spec_path, "--output", str(out)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0 and out.exists():
            return {"ok": True, "path": str(out)}
        return {"ok": False, "error": r.stderr[-500:] or r.stdout[-500:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            Path(spec_path).unlink()
        except OSError:
            pass


def _tool_generate_data_figure(
    figure_type: str,
    data: dict,
    output_path: str,
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
) -> dict[str, Any]:
    """用 matplotlib 生成数据分析 PNG（scatter / bar / line）。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        # 图内中文标注：容器镜像装了 fonts-noto-cjk，宿主机直跑时有雅黑/黑体。
        # matplotlib 默认 DejaVu Sans 无 CJK 字形，会渲染成方框。
        matplotlib.rcParams["font.sans-serif"] = [
            "Noto Sans CJK SC", "WenQuanYi Zen Hei", "Microsoft YaHei",
            "SimHei", "PingFang SC", "DejaVu Sans",
        ]
        matplotlib.rcParams["axes.unicode_minus"] = False
        import matplotlib.pyplot as plt

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)

        if figure_type == "scatter":
            xs = data.get("x", [])
            ys = data.get("y", [])
            labels = data.get("labels", [])
            ax.scatter(xs, ys, s=60, alpha=0.8)
            for i, label in enumerate(labels):
                if i < len(xs):
                    ax.annotate(label, (xs[i], ys[i]), fontsize=8, xytext=(4, 4),
                                textcoords="offset points")

        elif figure_type == "bar":
            keys = data.get("labels", list(data.get("x", [])))
            vals = data.get("values", list(data.get("y", [])))
            bars = ax.bar(keys, vals, color="#4f86c6", alpha=0.85)
            for bar, v in zip(bars, vals):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() * 1.01,
                    f"{v:.3g}",
                    ha="center", va="bottom", fontsize=8,
                )
            ax.tick_params(axis="x", rotation=20)

        elif figure_type == "line":
            xs = data.get("x", [])
            ys = data.get("y", [])
            ax.plot(xs, ys, marker="o", linewidth=1.5, markersize=5)

        else:
            return {"ok": False, "error": f"unknown figure_type {figure_type!r}"}

        if title:
            ax.set_title(title, fontsize=10)
        if xlabel:
            ax.set_xlabel(xlabel, fontsize=9)
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=9)

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.tight_layout()
        fig.savefig(str(out), bbox_inches="tight")
        plt.close(fig)
        return {"ok": True, "path": str(out)}

    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _tool_write_report(content: str, output_path: str) -> dict[str, Any]:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(out)}


# ---------------------------------------------------------------------------
# Tool definitions for run_agent
# ---------------------------------------------------------------------------

def _build_tools() -> list[dict]:
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
            "generate_pipeline_svg",
            "将 FigureSpec JSON 渲染为材料发现链路流程 SVG 图",
            {
                "spec_json": {
                    "type": "object",
                    "description": "符合 FigureSpec schema 的 JSON（nodes/edges/canvas）"
                },
                "output_path": {
                    "type": "string",
                    "description": "输出 SVG 文件的绝对路径"
                },
            },
            ["spec_json", "output_path"],
            _tool_generate_pipeline_svg,
        ),
        make_tool(
            "generate_data_figure",
            "用 matplotlib 生成数据分析图（scatter / bar / line），输出 PNG",
            {
                "figure_type": {
                    "type": "string",
                    "enum": ["scatter", "bar", "line"],
                    "description": "图表类型"
                },
                "data": {
                    "type": "object",
                    "description": "scatter: {x, y, labels}；bar: {labels, values}；line: {x, y}"
                },
                "output_path": {"type": "string", "description": "输出 PNG 文件的绝对路径"},
                "title": {"type": "string", "description": "图表标题（可选）"},
                "xlabel": {"type": "string", "description": "X 轴标签（可选）"},
                "ylabel": {"type": "string", "description": "Y 轴标签（可选）"},
            },
            ["figure_type", "data", "output_path"],
            _tool_generate_data_figure,
        ),
        make_tool(
            "write_report",
            "将最终报告 markdown 内容写入文件",
            {
                "content": {"type": "string", "description": "报告的完整 markdown 内容"},
                "output_path": {"type": "string", "description": "输出文件绝对路径"},
            },
            ["content", "output_path"],
            _tool_write_report,
        ),
    ]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
你是新材料发现分析报告生成 agent。

任务目标：{goal}
工作区路径：{work_dir}

已收到的上下文摘要：
- MP 数据库检索结果：{mp_search_report}
- 候选材料排名（S3 进化设计）：{candidate_summary}
- DFT 热力学稳定性结论（S7）：{stability_handoff}
- 云实验平台任务下发（S8）：{experiment_handoff}

执行步骤：
1. 用 list_files 探索工作区，找到所有前序产物文件（candidates.json、leaderboard、DFT 结果等）
2. 用 read_file 读取关键数据文件，提取实际数值
3. 规划图表清单（至少：材料发现链路流程图 + 候选材料性质对比图）
4. 调 generate_pipeline_svg 生成链路流程 SVG（S2→S3→S7→S9 四步，含关键产物标注）
5. 调 generate_data_figure 生成数据分析图（根据实际数据选择类型）
6. 调 write_report 写入 analysis_report.md

报告结构（必须结论先行）：
```
# 新材料发现分析报告

## 核心结论
（一句话结论：找到了什么材料、稳定性如何、建议是否值得进一步研究）

## 发现的候选材料
（表格：材料、带隙、ehull、稳定性判断）

## 研究方法与链路
（嵌入流程图 SVG，描述四步链路）

## 数据库检索（S2）
（MP 检索条件、候选池规模、关键候选）

## 进化设计（S3）
（进化参数、Pareto 前沿、最终候选）

## 热力学稳定性验证（S7）
（DFT 计算结论，ehull 数值，稳定性判断）

## 云实验平台下发（S8）
（下发状态、taskId、方案概要；未完成时如实标注 ⚠️，不删 section）

## 数据图表
（嵌入数据分析图，使用 <img> 标签控制宽度）

## 局限性与下一步
（缺失环节标注 ⚠️，建议后续实验）
```

质量要求：
- 每个数值必须有来源文件（括注文件名）
- 缺失的环节显式标注 ⚠️ 数据缺失，不删 section
- 语言：中文，面向不熟悉计算材料学的用户
- SVG 图使用 <img src="..." width="900" alt="..."> 嵌入
- PNG 图使用 <img src="..." width="600" alt="..."> 嵌入
- matplotlib 图内的轴标签/标题/图例可直接用中文（已配置 CJK 字体，
  Noto Sans CJK/微软雅黑），与报告语言保持一致
"""


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def execute_analysis_report_durable(
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
    run_dir = _out_root / safe(task_id) / safe(work_unit_id) / "analysis-report"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = str(run_dir / "analysis_report.md")

    goal = str(parameters.get("goal") or "").strip()
    # 生产里每个 substage 是独立 work_unit 子目录；binding 不传 work_dir 时
    # fallback 到 task 级目录（run_dir = <task>/<wu>/analysis-report，上溯两级），
    # 让 agent 能 rglob 到同 task 下 S2/S3/S7 的全部产物。
    work_dir = str(parameters.get("work_dir") or run_dir.parents[1])

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        goal=goal or "新材料发现分析",
        work_dir=work_dir,
        mp_search_report=str(parameters.get("mp_search_report") or "（未注入，请在工作区探索 report.md）"),
        candidate_summary=str(parameters.get("candidate_summary") or "（未注入，请在工作区探索 top_k_table）"),
        stability_handoff=str(parameters.get("stability_handoff") or "（未注入，DFT 结果未知）"),
        experiment_handoff=str(parameters.get("experiment_handoff") or "（未注入，请在工作区探索 flux-experiment/dispatch_report.md）"),
    )

    user_message = (
        f"请分析材料发现研究结果并生成分析报告。\n"
        f"报告输出路径：{report_path}\n"
        f"图表输出目录：{run_dir / 'figures'}"
    )

    result = run_agent(
        tools=_build_tools(),
        system_prompt=system_prompt,
        user_message=user_message,
        output_path=str(run_dir / "agent_result.json"),
        progress_path=str(run_dir.parent / "progress.md"),
        max_iterations=25,
        meter_context=(
            {"user_id": owner_id, "task_id": task_id, "workspace_id": workspace_id}
            if owner_id
            else None
        ),
    )

    # 检查报告文件是否真正产出
    if Path(report_path).exists():
        figures = [
            {"name": f.name, "path": str(f), "type": f.suffix.lstrip(".")}
            for f in (run_dir / "figures").glob("*")
            if f.suffix in {".svg", ".png"}
        ] if (run_dir / "figures").exists() else []
        return {
            "state": result.get("state", "succeeded"),
            "report_path": report_path,
            "figures": figures,
        }

    return {
        "state": "partial_failure",
        "report_path": report_path,
        "figures": [],
        "failure_reason": "report_file_not_written",
    }
