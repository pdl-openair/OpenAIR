"""vasp-stability-durable: 确定性 skill_script 执行入口.

工作流：
  1. 从 pareto_cif_paths[0] 取第一个 CIF
  2. 用 setup_competing_phases.py 生成 VASP 输入
  3. 检查 VASP_PSP_DIR；若缺失则用 jcc hpc vaspkit 生成 POTCAR
  4. jcc hpc create 提交弛豫计算
  5. 轮询 jcc hpc status（每 30s，最多 18 次）
  6. 下载 OUTCAR，提取 energy(sigma->0)
  7. analyze_stability.py 计算 ehull 和形成能
  8. 写 result.json
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """从当前文件向上找到仓库根（含 skills/ 的目录）。

    只要求 skills/：本脚本引用的所有资源（vasp-thermodynamic-stability/
    jcc/ 等）都在 skills/ 下。docker 扁平镜像的 /app 没有 services/，
    双条件判定会让 S7 在容器内必然 RuntimeError（task_feb85da 教训）。
    """
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "skills").is_dir():
            return parent
    raise RuntimeError("repo root not found")


def _skill_scripts_dir() -> Path:
    return _repo_root() / "skills" / "materials" / "vasp-thermodynamic-stability" / "scripts"


def _python() -> str:
    """优先用 MATERIALS_PY / EVOLVE_PY，回退到当前解释器。"""
    return (
        os.environ.get("MATERIALS_PY")
        or os.environ.get("EVOLVE_PY")
        or sys.executable
    )


# ---------------------------------------------------------------------------
# jcc CLI 工具
# ---------------------------------------------------------------------------

def _jcc_bin() -> str:
    """jcc バイナリのフルパスを返す。PATH → OS 别 bundled 资产路径。

    Windows: skills/materials/jcc/jcc.exe（PE32+）
    Linux:   skills/materials/vasp-band-gap/assets/jcc（ELF 静态链接，docker 用）
    """
    import shutil
    found = shutil.which("jcc")
    if found:
        return found
    candidates = [
        _repo_root() / "skills" / "materials" / "jcc" / "jcc.exe",
        _repo_root() / "skills" / "materials" / "vasp-band-gap" / "assets" / "jcc",
    ]
    for bundled in candidates:
        if bundled.exists():
            return str(bundled)
    raise FileNotFoundError(
        "jcc binary not found on PATH, skills/materials/jcc/, or "
        "skills/materials/vasp-band-gap/assets/"
    )


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 120, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, cwd=cwd, timeout=timeout, env=env)
    # Decode stdout/stderr as UTF-8 with fallback, avoid Windows GBK issues
    r = subprocess.CompletedProcess(
        r.args, r.returncode,
        stdout=r.stdout.decode("utf-8", errors="replace") if r.stdout else "",
        stderr=r.stderr.decode("utf-8", errors="replace") if r.stderr else "",
    )
    return r


def _jcc_env() -> dict[str, str]:
    """注入 jcc 凭证环境变量（从进程环境读，不硬编码）。"""
    env = os.environ.copy()
    # 从 skills/materials/jcc/.env 加载（若当前环境未设置）
    dot_env = _repo_root() / "skills" / "materials" / "jcc" / ".env"
    if dot_env.exists():
        for line in dot_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k and k not in env:
                env[k] = v.strip()
    # 直连服务统一绕过系统代理（容器代理 host.docker.internal:7890 在
    # 宿主机直跑时不可达，会让 jcc login / MP API / anthropic 网关全部
    # 卡死或超时）。
    try:
        from urllib.parse import urlparse

        direct_hosts = {
            str(env.get("JCC_BASE_URL") or ""),
            "https://jcc.jointcloud.net",
            # jcc 对象存储上传通道（INCAR/POTCAR 预签名上传走这个域）
            "https://jcs.jointcloud.net",
            "https://api.materialsproject.org",
            # mp_api 经 S3 拉取 phase-diagram delta log
            "https://s3.us-east-1.amazonaws.com",
            str(env.get("ANTHROPIC_BASE_URL") or ""),
        }
        for host in direct_hosts:
            parsed_host = urlparse(host).hostname if host else None
            if parsed_host:
                for key in ("NO_PROXY", "no_proxy"):
                    existing = env.get(key, "")
                    if parsed_host not in existing:
                        env[key] = f"{existing},{parsed_host}" if existing else parsed_host
    except Exception:  # noqa: BLE001
        pass
    return env


def _jcc_login(env: dict[str, str]) -> str | None:
    """Login to jcc and return the token, or None on failure."""
    username = env.get("JCC_USERNAME", "")
    password = env.get("JCC_PASSWORD", "")
    if not username or not password:
        return None
    r = _run(
        [_jcc_bin(), "login", "--username", username, "--password", password],
        timeout=60,
        env=env,
    )
    if r.returncode != 0:
        return None
    # jcc login prints "POST https://..." before JSON — extract the JSON block
    import json as _json
    lines = r.stdout.splitlines()
    json_start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), None)
    if json_start is None:
        return None
    try:
        data = _json.loads("\n".join(lines[json_start:]))
        return data.get("token") or data.get("data", {}).get("token")
    except Exception:
        return None


def _jcc_create(
    task_name: str,
    package_name: str,
    cluster_name: str,
    app_type: str,
    ntasks: int,
    env: dict[str, str],
) -> bool:
    r = subprocess.run(
        [
            _jcc_bin(), "hpc", "create",
            "--name", task_name,
            "--cluster-name", cluster_name,
            "--package-name", package_name,
            "--apptype", app_type,
            "--ntasks", str(ntasks),
        ],
        capture_output=True, text=True, env=env, timeout=60,
    )
    return r.returncode == 0


def _jcc_status(task_name: str, env: dict[str, str]) -> str | None:
    """返回任务状态字符串，失败返回 None。"""
    r = subprocess.run(
        [_jcc_bin(), "hpc", "status", "--task-name", task_name, "--output", "text"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    if r.returncode != 0:
        return None
    # 从输出里提取 status 字段（格式依赖 jcc 版本，宽松匹配）
    for line in r.stdout.splitlines():
        low = line.lower()
        if "completed" in low or "success" in low or "succeeded" in low:
            return "completed"
        if "failed" in low or "error" in low:
            return "failed"
        if "running" in low or "pending" in low or "queued" in low:
            return "running"
    return "unknown"


def _jcc_download(task_name: str, output_dir: Path, env: dict[str, str]) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [_jcc_bin(), "hpc", "result", "download",
         "--task-name", task_name,
         "--output-dir", str(output_dir)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    return r.returncode == 0


# ---------------------------------------------------------------------------
# VASP 输入生成
# ---------------------------------------------------------------------------

def _setup_competing_phases(
    formula: str,
    target_poscar: Path,
    workdir: Path,
    env: dict[str, str],
    mp_api_key: str,
) -> tuple[bool, str]:
    """生成竞争相 VASP 输入，返回 (是否成功, stderr 尾部摘要)。"""
    script = _skill_scripts_dir() / "setup_competing_phases.py"
    # text=True 会让 Windows reader 线程按 UTF-8 解码 GBK 输出而崩溃
    # （即使子进程 returncode=0 也抛 UnicodeDecodeError），改用 bytes 手动解码。
    r = subprocess.run(
        [
            _python(), "-I", str(script),
            "--formula", formula,
            "--target-poscar", str(target_poscar),
            "--workdir", str(workdir),
        ],
        capture_output=True,
        env={**env, "MP_API_KEY": mp_api_key, "PYTHONIOENCODING": "utf-8"},
        # JSON 主路径 ~15-60s；若回退 S3 delta 通道，其内部重试梯就要 ~208s，
        # 加上逐相结构下载，300s 会掐断兜底路径。
        timeout=600,
    )
    stderr_tail = r.stderr.decode("utf-8", errors="replace")[-300:].strip() if r.stderr else ""
    return r.returncode == 0, stderr_tail


def _generate_potcar_vaspkit(poscar_path: Path, env: dict[str, str]) -> Path | None:
    """用 jcc hpc vaspkit 生成 POTCAR，返回 POTCAR 路径。"""
    staged = poscar_path.parent / "_vaspkit_stage"
    staged.mkdir(exist_ok=True)
    shutil.copy(poscar_path, staged / "POSCAR")
    r = subprocess.run(
        [_jcc_bin(), "hpc", "vaspkit", "--path", str(staged / "POSCAR")],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if r.returncode != 0:
        return None
    potcar = staged / "POTCAR"
    return potcar if potcar.exists() else None


def _cif_to_poscar(cif_path: Path, work_dir: Path) -> tuple[str, Path] | None:
    """用 pymatgen 将 CIF 转为 POSCAR，返回 (化学式, POSCAR路径)。"""
    try:
        from pymatgen.core import Structure
        struct = Structure.from_file(str(cif_path))
        formula = struct.composition.reduced_formula
        poscar_dir = work_dir / "target"
        poscar_dir.mkdir(parents=True, exist_ok=True)
        poscar_path = poscar_dir / "POSCAR"
        struct.to(fmt="poscar", filename=str(poscar_path))
        return formula, poscar_path
    except Exception:  # noqa: BLE001
        return None


def _natoms_from_poscar(poscar_path: Path) -> int:
    try:
        lines = poscar_path.read_text(encoding="utf-8").splitlines()
        # POSCAR 第 6-7 行是元素符号和数量
        counts_line = lines[6] if len(lines) > 6 else ""
        return sum(int(x) for x in counts_line.split())
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# 稳定性分析
# ---------------------------------------------------------------------------

def _extract_energy_from_outcar(outcar_path: Path) -> float | None:
    """从 OUTCAR 提取 energy(sigma->0)，取最后一次出现。"""
    try:
        last = None
        for line in outcar_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "energy(sigma->0)" in line:
                parts = line.split()
                last = float(parts[-1])
        return last
    except Exception:  # noqa: BLE001
        return None


def _compute_ehull_from_outcar(
    formula: str,
    outcar_path: Path,
    natoms: int,
    mp_api_key: str,
) -> dict[str, Any]:
    """Compute ehull and formation energy using DFT energy from OUTCAR + MP competing phases."""
    try:
        from mp_api.client import MPRester
        from pymatgen.core import Composition
        from pymatgen.analysis.phase_diagram import PhaseDiagram
        from pymatgen.entries.computed_entries import ComputedEntry

        target_energy = _extract_energy_from_outcar(outcar_path)
        if target_energy is None:
            return {}

        comp = Composition(formula)
        chemsys = list({str(el) for el in comp.elements})

        with MPRester(mp_api_key) as mpr:
            mp_entries = mpr.get_entries_in_chemsys(
                chemsys,
                additional_criteria={"thermo_types": ["GGA_GGA+U"]},
            )

        # Scale composition to match cell: formula has comp.num_atoms atoms, cell has natoms
        scale = natoms / comp.num_atoms
        target_entry = ComputedEntry(
            composition={str(el): n * scale for el, n in comp.items()},
            energy=target_energy,
        )

        all_entries = list(mp_entries) + [target_entry]
        pd = PhaseDiagram(all_entries)
        ehull = pd.get_e_above_hull(target_entry)
        eform = pd.get_form_energy_per_atom(target_entry)

        verdict = _stability_verdict(ehull)
        return {
            "ehull_eV": ehull,
            "formation_energy_eV_per_atom": eform,
            "stability_verdict": verdict,
            "dft_energy_eV": target_energy,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _stability_verdict(ehull: float | None) -> str:
    if ehull is None:
        return "calculation_incomplete"
    if ehull <= 0.0:
        return "stable"
    if ehull <= 0.05:
        return "metastable"
    if ehull <= 0.1:
        return "unstable_borderline"
    return "unstable"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _append_progress(run_dir: Path, message: str) -> None:
    """向工作单元的 progress.md 追加一条带时间戳的里程碑（前端实时轮询渲染）。

    写失败静默忽略——进度展示绝不能影响计算本身。文件约定位于
    <OPENAIR_WORKSPACE_ROOT>/<task_id>/<work_unit_id>/progress.md，即
    run_dir 的上一级。
    """
    try:
        line = f"- **[{time.strftime('%H:%M:%S')}]** {message}\n"
        with (run_dir.parent / "progress.md").open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _write_stability_report(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """把最终结果物化为平台可渲染的 stability_report.md（artifact_files 声明注册）。

    所有终态（含 partial_failure）都写，失败路径同样给用户一份可读报告。
    """
    try:
        ehull = payload.get("ehull_eV")
        eform = payload.get("formation_energy_eV_per_atom")
        verdict = payload.get("stability_verdict")
        lines = [
            f"# 热力学稳定性验证报告 · {payload.get('target_formula') or '未知体系'}",
            "",
            f"- **执行状态**：{payload.get('state')}",
            f"- **稳定性判断**：{verdict}"
            + ("（判据：stable ≤ 0 eV/atom，metastable ≤ 0.05 eV/atom）" if ehull is not None else ""),
        ]
        if ehull is not None:
            lines.append(f"- **ehull（凸包上方能量）**：{ehull:.4f} eV/atom")
        if eform is not None:
            lines.append(f"- **形成能**：{eform:.4f} eV/atom")
        if payload.get("jcc_task_name"):
            lines.append(f"- **jcc/HPC 任务名**：`{payload['jcc_task_name']}`")
        if payload.get("failure_reason"):
            lines.append(f"- **失败原因**：`{payload['failure_reason']}`")
        lines += [
            "",
            f"> {payload.get('handoff_summary')}",
            "",
            f"- 工作目录：`{payload.get('work_dir')}`",
            f"- 报告生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        (run_dir / "stability_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return payload


def execute_vasp_stability_durable(
    *,
    task_id: str,
    work_unit_id: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """主执行函数，由 materials_skill_script_runtime 调用。"""
    result = _execute_vasp_stability_durable(
        task_id=task_id, work_unit_id=work_unit_id, parameters=parameters
    )
    # 所有终态统一落报告 + 收尾进度行（work_dir 在每个返回 payload 里都有）
    run_dir = Path(str(result.get("work_dir") or ""))
    if run_dir.name:
        _write_stability_report(run_dir, result)
        if result.get("state") == "succeeded":
            _append_progress(run_dir, f"S7 完成：{result.get('stability_verdict')}（报告已生成）")
        else:
            _append_progress(
                run_dir,
                f"S7 结束（{result.get('state')}，原因：{result.get('failure_reason') or '见报告'}）",
            )
    return result


def _execute_vasp_stability_durable(
    *,
    task_id: str,
    work_unit_id: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """实际执行体；由 execute_vasp_stability_durable 包裹统一落报告。"""
    start_ts = time.monotonic()
    # inline durable worker 对 skill_script 无硬性时长上限（dispatch lease 由独立
    # 线程自动续租），此前 550s 是按不存在的 600s worker 上限自设的。真实 jcc
    # 往返 = 上传 ~1min + DFT 计算 + 结果 zip 传播 2-10min（task_132d28e4 实测
    # 传播 >5.5min），默认放宽到 30min，可用环境变量覆盖。
    _TIMEOUT_SECONDS = int(os.environ.get("VASP_STABILITY_TIMEOUT_SECONDS", "1800"))

    goal = str(parameters.get("goal") or "").strip()
    pareto_cif_paths: list[str] = list(parameters.get("pareto_cif_paths") or [])

    # 工作目录
    safe = lambda v: "".join(c if c.isalnum() or c in "_.-" else "_" for c in v).strip("._") or "run"
    _ws_root = os.environ.get("OPENAIR_WORKSPACE_ROOT", "").strip()
    _out_root = Path(_ws_root) if _ws_root else _repo_root() / "outputs" / "canonical-materials"
    run_dir = _out_root / safe(task_id) / safe(work_unit_id) / "vasp-stability"
    run_dir.mkdir(parents=True, exist_ok=True)

    evidence: list[dict[str, Any]] = []

    def _elapsed() -> float:
        return time.monotonic() - start_ts

    def _timeout_check(label: str) -> bool:
        if _elapsed() > _TIMEOUT_SECONDS:
            return True
        return False

    # 1. 确定 CIF 路径
    if not pareto_cif_paths:
        return {
            "state": "partial_failure",
            "target_formula": "",
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": "",
            "work_dir": str(run_dir),
            "failure_reason": "no_pareto_cif_paths_provided",
            "evidence": evidence,
            "handoff_summary": "S7 未收到候选材料 CIF 路径，热力学稳定性计算未执行。",
        }

    cif_path = Path(pareto_cif_paths[0])
    if not cif_path.exists():
        return {
            "state": "partial_failure",
            "target_formula": "",
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": "",
            "work_dir": str(run_dir),
            "failure_reason": f"cif_not_found:{cif_path}",
            "evidence": evidence,
            "handoff_summary": f"S7 CIF 文件不存在（{cif_path}），热力学稳定性计算未执行。",
        }

    # 2. CIF → POSCAR + 化学式
    parsed = _cif_to_poscar(cif_path, run_dir)
    if parsed is None:
        return {
            "state": "partial_failure",
            "target_formula": "",
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": "",
            "work_dir": str(run_dir),
            "failure_reason": "cif_to_poscar_failed",
            "evidence": evidence,
            "handoff_summary": "S7 CIF 解析失败，热力学稳定性计算未执行。",
        }

    formula, poscar_path = parsed
    natoms = _natoms_from_poscar(poscar_path)
    # task_name 加时间戳后缀，避免重复运行触发 "already exists"
    import time as _time
    task_name = f"{formula}_{natoms}at_relax_{int(_time.time())}"
    evidence.append({"step": "cif_to_poscar", "formula": formula, "natoms": natoms})
    _append_progress(run_dir, f"种子 CIF 已转换：{formula}（{natoms} 原子），任务名 `{task_name}`")

    # 3. 环境变量 & jcc 登录（获取 token 并注入环境变量）
    env = _jcc_env()
    mp_api_key = env.get("MP_API_KEY", "")
    cluster_name = env.get("JCC_CLUSTER_NAME", "HPCSlurmArm")
    app_type = env.get("JCC_APP_TYPE", "vasp")

    token = _jcc_login(env)
    if not token:
        return {
            "state": "partial_failure",
            "target_formula": formula,
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": task_name,
            "work_dir": str(run_dir),
            "failure_reason": "jcc_login_failed",
            "evidence": evidence,
            "handoff_summary": f"S7 jcc 登录失败，{formula} 热力学稳定性计算未执行。",
        }
    # 注入 token 供后续所有 jcc 命令使用
    env["JCC_TOKEN"] = token
    _append_progress(run_dir, f"jcc 登录成功（集群 {cluster_name}），准备竞争相输入")

    # 4. 生成竞争相 VASP 输入（目标相 + 所有竞争相）
    vasp_workdir = run_dir / "competing_phases"
    setup_ok, setup_stderr = _setup_competing_phases(formula, poscar_path, vasp_workdir, env, mp_api_key)
    setup_entry: dict[str, Any] = {"step": "setup_competing_phases", "ok": setup_ok, "workdir": str(vasp_workdir)}
    if not setup_ok and setup_stderr:
        setup_entry["stderr_tail"] = setup_stderr
    evidence.append(setup_entry)
    setup_progress = f"竞争相 VASP 输入{'就绪' if setup_ok else '生成失败'}（工作目录 competing_phases/）"
    if not setup_ok and setup_stderr:
        setup_progress += f"：{setup_stderr}"
    _append_progress(run_dir, setup_progress)

    if not setup_ok:
        return {
            "state": "partial_failure",
            "target_formula": formula,
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": task_name,
            "work_dir": str(run_dir),
            "failure_reason": "setup_competing_phases_failed",
            "evidence": evidence,
            "handoff_summary": f"S7 VASP 输入生成失败，{formula} 热力学稳定性计算未执行。",
        }

    # 5a. 用 jcc hpc vaspkit 生成 POTCAR（jcc hpc create 校验必须有 POTCAR）
    target_relax_dir = vasp_workdir / f"{formula}_{natoms}at" / "relax"
    hpc_output_dir = run_dir / "hpc_results" / f"{formula}_{natoms}at_relax"
    run_hpc_sh = _skill_scripts_dir() / "run_hpc.sh"

    # Windows: 把 Windows 路径转为 MSYS 形式（C:\path\to\file -> /c/path/to/file）
    import re as _re
    def _to_msys(p: str) -> str:
        p = p.replace("\\", "/")
        m = _re.match(r"^([A-Za-z]):(.*)$", p)
        if m:
            return f"/{m.group(1).lower()}{m.group(2)}"
        return p

    # 把 jcc.exe 和 jq.exe 所在目录加入 PATH，让 run_hpc.sh 能找到它们
    jcc_dir = str(Path(_jcc_bin()).parent)
    jq_dir = str(_repo_root() / "skills" / "materials" / "jcc")
    jcc_dir_msys = _to_msys(jcc_dir) if os.name == "nt" else jcc_dir
    jq_dir_msys = _to_msys(jq_dir) if os.name == "nt" else jq_dir
    hpc_env = {**env, "PATH": f"{jcc_dir_msys}:{jq_dir_msys}:{env.get('PATH', '')}"}
    # 结果 zip 传播实测可超 5.5min，脚本默认 30 次 × 20s 不够；放宽到 45 次（15min）
    hpc_env.setdefault("RESULT_MAX_RETRIES", "45")
    # 解析 bash 路径
    import shutil as _shutil
    bash_bin = _shutil.which("bash") or "bash"
    run_hpc_sh_arg = _to_msys(str(run_hpc_sh)) if os.name == "nt" else str(run_hpc_sh)

    potcar_path = target_relax_dir / "POTCAR"
    if not potcar_path.exists():
        # 用 vaspkit 服务生成 POTCAR（binary 模式避免 Windows GBK 编码问题）
        vaspkit_result = subprocess.run(
            [_jcc_bin(), "hpc", "vaspkit", "--path", str(target_relax_dir / "POSCAR")],
            capture_output=True, env=env, timeout=120,
        )
        vaspkit_stdout = vaspkit_result.stdout.decode("utf-8", errors="replace") if vaspkit_result.stdout else ""
        vaspkit_stderr = vaspkit_result.stderr.decode("utf-8", errors="replace") if vaspkit_result.stderr else ""
        if vaspkit_result.returncode == 0:
            if potcar_path.exists():
                evidence.append({"step": "potcar_via_vaspkit", "ok": True})
            else:
                evidence.append({"step": "potcar_via_vaspkit", "ok": False, "note": "POTCAR not found after vaspkit"})
        else:
            vaspkit_stderr = ""
            try:
                if vaspkit_result.stderr:
                    vaspkit_stderr = vaspkit_result.stderr[-300:] if isinstance(vaspkit_result.stderr, str) else vaspkit_result.stderr.decode("utf-8", errors="replace")[-300:]
            except Exception:
                pass
            evidence.append({"step": "potcar_via_vaspkit", "ok": False, "stderr": vaspkit_stderr})
            return {
                "state": "partial_failure",
                "target_formula": formula,
                "ehull_eV": None,
                "formation_energy_eV_per_atom": None,
                "stability_verdict": "calculation_incomplete",
                "jcc_task_name": task_name,
                "work_dir": str(run_dir),
                "failure_reason": "potcar_generation_failed",
                "evidence": evidence,
                "handoff_summary": f"S7 POTCAR 生成失败（{formula}），热力学稳定性计算未执行。",
            }

    if _timeout_check("before_hpc_submit"):
        return {
            "state": "partial_failure",
            "target_formula": formula,
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": task_name,
            "work_dir": str(run_dir),
            "failure_reason": "timeout_before_submit",
            "evidence": evidence,
            "handoff_summary": f"S7 超时（提交前），{formula} 热力学稳定性计算未执行。",
        }

    _append_progress(
        run_dir,
        f"提交 HPC 计算任务 `{task_name}`（上传 + DFT 弛豫 + 结果回传，通常需 5-15 分钟）",
    )
    try:
        hpc_result = subprocess.run(
            [
                str(bash_bin),
                run_hpc_sh_arg,
                "--local-path", _to_msys(str(target_relax_dir)) if os.name == "nt" else str(target_relax_dir),
                "--output-dir", _to_msys(str(hpc_output_dir)) if os.name == "nt" else str(hpc_output_dir),
                "--task-name", task_name,
                "--ntasks", str(4 if natoms >= 30 else 8),
            ],
            capture_output=True,
            env=hpc_env,
            cwd=str(_repo_root()),
            timeout=max(30, _TIMEOUT_SECONDS - int(_elapsed())),
        )
    except subprocess.TimeoutExpired:
        # 本地等待窗口耗尽 ≠ 计算失败：jcc 任务可能已在平台完成，只是结果 zip
        # 传播超窗。优雅降级并保留任务名供人工核对/复下载，不作为本地崩溃。
        evidence.append({
            "step": "run_hpc_sh",
            "task_name": task_name,
            "ok": False,
            "returncode": None,
            "timeout_seconds": int(_elapsed()),
        })
        _append_progress(run_dir, f"本地等待窗口耗尽（{int(_elapsed())}s），任务 `{task_name}` 请到云平台核对")
        return {
            "state": "partial_failure",
            "target_formula": formula,
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": task_name,
            "work_dir": str(run_dir),
            "failure_reason": "hpc_wait_timeout",
            "evidence": evidence,
            "handoff_summary": (
                f"S7 等待超时（{int(_elapsed())}s）：jcc 任务 {task_name} 可能已完成，"
                "但结果 zip 未在窗口内就绪；请到云平台核对并手动下载。"
            ),
        }
    hpc_ok = hpc_result.returncode == 0
    hpc_stdout = hpc_result.stdout.decode("utf-8", errors="replace") if isinstance(hpc_result.stdout, bytes) else hpc_result.stdout
    hpc_stderr = hpc_result.stderr.decode("utf-8", errors="replace") if isinstance(hpc_result.stderr, bytes) else (hpc_result.stderr or "")
    evidence.append({
        "step": "run_hpc_sh",
        "task_name": task_name,
        "ok": hpc_ok,
        "returncode": hpc_result.returncode,
        "stdout_tail": hpc_stdout[-1000:],
        "stderr_tail": hpc_stderr[-1000:],
    })
    _append_progress(
        run_dir,
        f"HPC 任务 `{task_name}` 结束（rc={hpc_result.returncode}，耗时 {int(_elapsed())}s）",
    )

    if not hpc_ok:
        return {
            "state": "partial_failure",
            "target_formula": formula,
            "ehull_eV": None,
            "formation_energy_eV_per_atom": None,
            "stability_verdict": "calculation_incomplete",
            "jcc_task_name": task_name,
            "work_dir": str(run_dir),
            "failure_reason": "run_hpc_sh_failed",
            "evidence": evidence,
            "handoff_summary": f"S7 jcc 计算失败（{formula}），热力学稳定性结果未收到。",
        }

    # download 目录：run_hpc.sh 解压到 <output-dir>/download/workdir/
    download_dir = hpc_output_dir / "download"
    outcar_path = download_dir / "workdir" / "OUTCAR"

    # 8. 稳定性分析：从 OUTCAR 提取能量，调 analyze_stability.py 计算 ehull
    target_energy = _extract_energy_from_outcar(outcar_path) if outcar_path.exists() else None
    evidence.append({"step": "extract_outcar_energy", "energy_eV": target_energy, "outcar": str(outcar_path)})

    # 8. 稳定性分析：从 OUTCAR 提取能量，用 pymatgen + MP 竞争相计算 ehull
    if target_energy is not None:
        stability = _compute_ehull_from_outcar(formula, outcar_path, natoms, mp_api_key)
    else:
        stability = {}

    ehull = stability.get("ehull_eV") or stability.get("e_above_hull")
    eform = stability.get("formation_energy_eV_per_atom") or stability.get("formation_energy")
    verdict = _stability_verdict(ehull)
    evidence.append({"step": "analyze_stability", "ehull_eV": ehull, "verdict": verdict})
    _append_progress(
        run_dir,
        f"稳定性分析完成：ehull={ehull if ehull is None else round(ehull, 4)} eV/atom，判定 {verdict}",
    )

    # 9. 构建 handoff_summary
    if ehull is not None:
        handoff = (
            f"目标材料：{formula}。"
            f"DFT 热力学稳定性计算完成：ehull = {ehull:.4f} eV/atom，"
            f"形成能 = {eform:.4f} eV/atom，"
            f"稳定性判断：{verdict}（stable ≤ 0 eV/atom，metastable ≤ 0.05 eV/atom）。"
        )
    else:
        handoff = f"目标材料：{formula}。DFT 计算完成但稳定性分析未能提取 ehull，请检查 OUTCAR。"

    return {
        "state": "succeeded",
        "target_formula": formula,
        "ehull_eV": ehull,
        "formation_energy_eV_per_atom": eform,
        "stability_verdict": verdict,
        "jcc_task_name": task_name,
        "work_dir": str(run_dir),
        "evidence": evidence,
        "handoff_summary": handoff,
    }
