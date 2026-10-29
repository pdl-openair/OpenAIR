#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云实验平台（flux）试验任务 CLI。

子命令：
  probe     连通性 + 鉴权预检
  validate  离线校验方案 JSON（字段完整性 + 物理一致性）
  create    创建试验任务（默认 --dry-run，不发请求）
  start     启动试验任务（默认 --dry-run，不发请求）
  status    查询任务状态（只读，直接发请求）
  result    回收任务结果（只读；未完成返回 pending，平台侧暂不保证产出节奏）
  stop      停止任务（默认 --dry-run，不发请求）

仅使用标准库，Python 3.8+。真实请求必须显式加 --execute。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://<flux-base-url>"
TEMPLATE_ID = "2043611007682150400"
PARAM_NAME = "recommend_recipes_str"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.local.json")

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VALIDATION = 2
EXIT_NETWORK = 3
EXIT_API = 4


# ---------------------------------------------------------------- 配置


def load_config() -> dict:
    cfg = {}
    try:
        with open(os.path.abspath(CONFIG_PATH), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        pass
    return cfg


def get_base_url() -> str:
    return (os.environ.get("FLUX_BASE_URL") or load_config().get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")


def get_api_key() -> str:
    return os.environ.get("FLUX_API_KEY") or load_config().get("apiKey") or ""


def get_timeout() -> int:
    raw = os.environ.get("FLUX_TIMEOUT") or load_config().get("timeout") or 30
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 30


# ---------------------------------------------------------------- 输出


def emit(payload: dict, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(human)


# ---------------------------------------------------------------- HTTP


def request(method: str, path: str, body: dict | None = None) -> dict:
    url = get_base_url() + path
    key = get_api_key()
    headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
    if key:
        headers["X-Api-Key"] = key
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=get_timeout()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(str(exc)) from exc

    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except ValueError:
        parsed = {"_raw": raw}
    if not isinstance(parsed, dict):
        parsed = {"_raw": parsed}
    parsed["_httpStatus"] = status
    return parsed


# create 接口成功码为 2000，start 接口为 200，两者都视为成功
SUCCESS_CODES = {200, 2000, "200", "2000"}


def is_success(resp: dict) -> bool:
    code = resp.get("code")
    return code in SUCCESS_CODES or (code is None and resp.get("_httpStatus") == 200)


# ---------------------------------------------------------------- 方案读取与校验


def load_recipes(path: str) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        # 容忍 {"推荐实验方案列表": [...]} 这种包装写法
        for key in ("推荐实验方案列表", "recipes", "方案列表"):
            if key in data:
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list) or not data:
        raise ValueError("方案文件必须是非空数组，或包含 推荐实验方案列表 字段的对象")
    return data


def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


REQUIRED_RECIPE_KEYS = ["方案ID", "方案类型", "工艺参数"]
REQUIRED_PROCESS_KEYS = ["原料信息", "原料标准化", "助熔剂信息", "助熔剂标准化", "容器"]
REQUIRED_TEMP_KEYS = [
    "低温段保温温度_摄氏",
    "最高温段保温温度_摄氏",
    "最高温段保温时间_h",
    "降温时间_主降温_h",
]
TRACE_KEYS = ["主要参考配方ID", "参考方案类型ID-名称", "参考材料ID-化学式"]
RISK_KEYS = ["预期晶体尺寸", "预期风险水平", "风险来源简述"]


def validate_recipes(recipes: list) -> tuple:
    errors, warnings = [], []
    seen_ids = set()

    for idx, recipe in enumerate(recipes):
        tag = "recipes[%d]" % idx
        if not isinstance(recipe, dict):
            errors.append("%s 不是对象" % tag)
            continue

        rid = recipe.get("方案ID")
        tag = "方案[%s]" % (rid if rid else idx)
        if not rid:
            errors.append("%s 缺少 方案ID" % tag)
        elif rid in seen_ids:
            errors.append("%s 方案ID 重复：%s" % (tag, rid))
        else:
            seen_ids.add(rid)
            tag = "方案[%s]" % rid

        for key in REQUIRED_RECIPE_KEYS:
            if key not in recipe:
                errors.append("%s 缺少字段 %s" % (tag, key))
        if not str(recipe.get("方案类型", "")).strip():
            errors.append("%s 方案类型 为空" % tag)
        if not str(recipe.get("方案给人的一句话说明", "")).strip():
            warnings.append("%s 缺少 方案给人的一句话说明（平台前端可能显示为空）" % tag)

        params = recipe.get("工艺参数")
        if not isinstance(params, dict):
            errors.append("%s.工艺参数 缺失或不是对象" % tag)
            continue

        for key in TRACE_KEYS:
            if key not in (recipe.get("溯源信息") or {}):
                warnings.append("%s.溯源信息 缺少 %s" % (tag, key))
        for key in RISK_KEYS:
            if key not in (recipe.get("预期结果标签") or {}):
                warnings.append("%s.预期结果标签 缺少 %s" % (tag, key))

        for key in ("原料信息", "原料标准化", "助熔剂信息", "助熔剂标准化", "容器"):
            if key not in params:
                warnings.append("%s.工艺参数 缺少 %s" % (tag, key))
            elif not str(params.get(key) or "").strip():
                warnings.append("%s.工艺参数.%s 为空" % (tag, key))

        temp = params.get("温度程序")
        if not isinstance(temp, dict):
            errors.append("%s.工艺参数.温度程序 缺失或不是对象" % tag)
            continue
        for key in REQUIRED_TEMP_KEYS:
            if key not in temp:
                errors.append("%s.工艺参数.温度程序 缺少 %s" % (tag, key))

        low_t = _num(temp.get("低温段保温温度_摄氏"))
        mid_t = _num(temp.get("次高温段温度_摄氏"))
        high_t = _num(temp.get("最高温段保温温度_摄氏"))

        if low_t is not None and high_t is not None and high_t <= low_t:
            errors.append("%s 最高温(%.0f) 未高于低温段(%.0f)" % (tag, high_t, low_t))
        if mid_t is not None and high_t is not None and high_t <= mid_t:
            errors.append("%s 最高温(%.0f) 未高于次高温(%.0f)" % (tag, high_t, mid_t))
        if mid_t is not None and low_t is not None and mid_t < low_t:
            warnings.append("%s 次高温(%.0f) 低于低温段(%.0f)，确认是否有意为之" % (tag, mid_t, low_t))

        has_mid = str(temp.get("是否存在次高温预反应段", "")).strip()
        mid_hold = _num(temp.get("次高温段保温时间_h"))
        if has_mid in ("否", "no", "No", "false") and mid_hold:
            warnings.append(
                "%s 声明无次高温预反应段，但 次高温段保温时间_h=%.4g，请确认平台是否仍会执行该段" % (tag, mid_hold)
            )

        rate = _num(temp.get("降温速率_主降温_℃每小时"))
        cool_h = _num(temp.get("降温时间_主降温_h"))
        if rate and cool_h and high_t is not None:
            end_t = high_t - rate * cool_h
            if low_t is not None and abs(end_t - low_t) > 150:
                warnings.append(
                    "%s 降温速率%.2f℃/h × %.1fh = 从最高温%.0f℃降到约%.0f℃，与低温段%.0f℃相差%.0f℃"
                    % (tag, rate, cool_h, high_t, end_t, low_t, end_t - low_t)
                )
            if cool_h > 72:
                warnings.append("%s 主降温 %.1fh 超过 72h，注意平台单任务时长上限" % (tag, cool_h))

        sep = params.get("分离与后处理")
        if isinstance(sep, dict):
            if _num(sep.get("分离温度_摄氏")) is None:
                warnings.append("%s.分离与后处理.分离温度_摄氏 非数值" % tag)
        elif sep is not None:
            warnings.append("%s.分离与后处理 不是对象" % tag)

        std = str(params.get("助熔剂标准化", ""))
        if std and ":" in std:
            body = std.split("=")[-1]
            if not any(ch.isdigit() for ch in body):
                warnings.append("%s.助熔剂标准化 未解析出数值比例：%s" % (tag, std))

    return errors, warnings


# ---------------------------------------------------------------- 子命令


def cmd_probe(args) -> int:
    url = get_base_url()
    key = get_api_key()
    result = {"baseUrl": url, "apiKeyPresent": bool(key), "reachable": False, "authOk": None, "detail": ""}
    try:
        resp = request("GET", "/open/api/v1/tasks")
    except ConnectionError as exc:
        result["detail"] = "无法连接 %s —— %s" % (url, exc)
        emit(result, args.json, "✗ 连通性失败：%s\n  提示：该地址为内网服务，请确认已连内网/VPN，脚本不会自动重试。" % result["detail"])
        return EXIT_NETWORK

    result["reachable"] = True
    status = resp.get("_httpStatus")
    if status == 401:
        result["detail"] = "已连通但鉴权失败（401），X-Api-Key 无效或已过期"
    elif status == 403:
        result["detail"] = "已连通但无权限（403）"
    elif status == 404:
        result["authOk"] = True
        result["detail"] = "已连通；GET /tasks 未开放或路径不同，不影响 POST 创建接口"
    elif status == 405:
        result["authOk"] = True
        result["detail"] = "已连通；该资源不支持 GET（405），说明服务在线"
    elif isinstance(status, int) and status >= 500:
        # 有网关响应但上游平台没起来——不要误报成"连通可用"
        result["reachable"] = False
        result["detail"] = "收到 HTTP %s（服务端错误）：网关可达但上游平台未正常响应，暂不可下发任务" % status
    else:
        result["authOk"] = is_success(resp) or status == 200
        result["detail"] = "已连通（HTTP %s）" % status
        result["response"] = {k: v for k, v in resp.items() if not k.startswith("_raw")}

    mark = "✓" if result["reachable"] else "✗"
    human = "%s %s\n  baseUrl: %s\n  key: %s" % (
        mark,
        result["detail"],
        url,
        "已配置" if key else "缺失（设置 FLUX_API_KEY）",
    )
    emit(result, args.json, human)
    return EXIT_OK if result["reachable"] else EXIT_NETWORK


def cmd_validate(args) -> int:
    try:
        recipes = load_recipes(args.recipes_file)
    except (OSError, ValueError) as exc:
        msg = "读取方案文件失败：%s" % exc
        emit({"ok": False, "errors": [msg]}, args.json, "✗ " + msg)
        return EXIT_USAGE

    errors, warnings = validate_recipes(recipes)
    payload = {
        "ok": not errors,
        "recipeCount": len(recipes),
        "recipeIds": [r.get("方案ID") for r in recipes if isinstance(r, dict)],
        "errors": errors,
        "warnings": warnings,
    }
    lines = ["方案校验：%d 条方案" % len(recipes)]
    for w in warnings:
        lines.append("  WARN  %s" % w)
    for e in errors:
        lines.append("  ERROR %s" % e)
    lines.append("结果：%s" % ("通过" if not errors else "存在 %d 个错误" % len(errors)))
    emit(payload, args.json, "\n".join(lines))
    return EXIT_OK if not errors else EXIT_VALIDATION


def build_create_body(task_name: str, recipes: list, template_id=None) -> dict:
    return {
        "templateId": TEMPLATE_ID if template_id is None else template_id,
        "taskName": task_name,
        "params": [
            {
                "paramName": PARAM_NAME,
                "paramValue": json.dumps(recipes, ensure_ascii=False),
            }
        ],
    }


def cmd_create(args) -> int:
    if not str(args.task_name).strip():
        print("✗ task-name 不能为空", file=sys.stderr)
        return EXIT_USAGE
    try:
        recipes = load_recipes(args.recipes_file)
    except (OSError, ValueError) as exc:
        print("✗ 读取方案文件失败：%s" % exc, file=sys.stderr)
        return EXIT_USAGE

    errors, warnings = validate_recipes(recipes)
    if errors and not args.force:
        print("✗ 方案校验未通过，已中止（加 --force 可跳过）：", file=sys.stderr)
        for e in errors:
            print("    ERROR %s" % e, file=sys.stderr)
        return EXIT_VALIDATION

    body = build_create_body(args.task_name, recipes, args.template_id)
    if args.dry_run or not args.execute:
        human = [
            "— dry-run，未发送请求 —",
            "POST %s/open/api/v1/tasks" % get_base_url(),
            "taskName : %s" % args.task_name,
            "templateId: %s (%s)" % (body["templateId"], "固定值" if args.template_id is None else "已覆盖"),
            "paramName : %s (固定)" % PARAM_NAME,
            "方案条数  : %d" % len(recipes),
            "paramValue 序列化后长度: %d 字符" % len(body["params"][0]["paramValue"]),
        ]
        for w in warnings:
            human.append("  WARN  %s" % w)
        human.append("确认无误后追加 --execute 真正下发。")
        emit({"dryRun": True, "request": body, "warnings": warnings}, args.json, "\n".join(human))
        return EXIT_OK

    try:
        resp = request("POST", "/open/api/v1/tasks", body)
    except ConnectionError as exc:
        print("✗ 无法连接 %s：%s" % (get_base_url(), exc), file=sys.stderr)
        return EXIT_NETWORK

    if not is_success(resp):
        print("✗ 创建失败：HTTP %s code=%s msg=%s" % (resp.get("_httpStatus"), resp.get("code"), resp.get("msg")), file=sys.stderr)
        print(json.dumps(resp, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_API

    data = resp.get("data") or {}
    task_id = data.get("taskId")
    result = {
        "ok": True,
        "taskId": task_id,
        "taskCode": data.get("taskCode"),
        "code": resp.get("code"),
        "msg": resp.get("msg", ""),
    }
    if not task_id:
        result["ok"] = False
        result["warning"] = "code=%s 但 data.taskId 为空，平台侧可能未回填；重试一次，仍失败请人工到平台确认" % resp.get("code")
    human = "✓ 创建成功\n  taskId  : %s\n  taskCode: %s" % (task_id, data.get("taskCode"))
    if not task_id:
        human += "\n  ⚠ %s" % result["warning"]
    human += "\n下一步：python scripts/flux_api.py start --task-id %s --execute" % task_id
    emit(result, args.json, human)
    return EXIT_OK if result["ok"] else EXIT_API


def cmd_start(args) -> int:
    task_id = str(args.task_id).strip()
    if not task_id:
        print("✗ task-id 不能为空", file=sys.stderr)
        return EXIT_USAGE

    path = "/open/api/v1/tasks/%s/start" % task_id
    if args.dry_run or not args.execute:
        human = "— dry-run，未发送请求 —\nPOST %s%s\n确认后追加 --execute。" % (get_base_url(), path)
        emit({"dryRun": True, "path": path}, args.json, human)
        return EXIT_OK

    try:
        resp = request("POST", path, {})
    except ConnectionError as exc:
        print("✗ 无法连接 %s：%s" % (get_base_url(), exc), file=sys.stderr)
        return EXIT_NETWORK

    if not is_success(resp):
        print("✗ 启动失败：HTTP %s code=%s msg=%s" % (resp.get("_httpStatus"), resp.get("code"), resp.get("msg")), file=sys.stderr)
        print(json.dumps(resp, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_API

    result = {"ok": True, "taskId": task_id, "code": resp.get("code"), "msg": resp.get("msg", "")}
    emit(result, args.json, "✓ 任务 %s 已启动（code=%s）。start 接口正常不返回业务数据。" % (task_id, resp.get("code")))
    return EXIT_OK


def cmd_status(args) -> int:
    task_id = str(args.task_id).strip()
    if not task_id:
        print("✗ task-id 不能为空", file=sys.stderr)
        return EXIT_USAGE

    try:
        resp = request("GET", "/open/api/v1/tasks/%s/status" % task_id)
    except ConnectionError as exc:
        print("✗ 无法连接 %s：%s" % (get_base_url(), exc), file=sys.stderr)
        return EXIT_NETWORK

    if not is_success(resp):
        print("✗ 查询失败：HTTP %s code=%s msg=%s" % (resp.get("_httpStatus"), resp.get("code"), resp.get("msg")), file=sys.stderr)
        print(json.dumps(resp, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_API

    data = resp.get("data") or {}
    result = {"ok": True, "taskId": task_id, "task": data}
    human = "\n".join(
        [
            "✓ 任务 %s（%s）" % (task_id, data.get("taskCode", "")),
            "  状态    : %s（status=%s）" % (data.get("statusLabel", "未知"), data.get("status")),
            "  任务名  : %s" % data.get("taskName", ""),
            "  开始    : %s" % data.get("startTime", ""),
            "  窗口结束: %s（平台固定 7 天配额，非实际完成时间）" % data.get("endTime", ""),
        ]
    )
    emit(result, args.json, human)
    return EXIT_OK


def cmd_result(args) -> int:
    task_id = str(args.task_id).strip()
    if not task_id:
        print("✗ task-id 不能为空", file=sys.stderr)
        return EXIT_USAGE

    try:
        resp = request("GET", "/open/api/v1/tasks/%s/result" % task_id)
    except ConnectionError as exc:
        print("✗ 无法连接 %s：%s" % (get_base_url(), exc), file=sys.stderr)
        return EXIT_NETWORK

    # 202 是平台的"任务尚未完成"业务码，不是错误
    if resp.get("code") == 202 or str(resp.get("code")) == "202":
        result = {"ok": True, "pending": True, "taskId": task_id, "msg": resp.get("msg", "")}
        emit(result, args.json, "… 任务 %s 尚未完成（202），请稍后重试。" % task_id)
        return EXIT_OK

    if not is_success(resp):
        print("✗ 回收失败：HTTP %s code=%s msg=%s" % (resp.get("_httpStatus"), resp.get("code"), resp.get("msg")), file=sys.stderr)
        print(json.dumps(resp, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_API

    result = {"ok": True, "pending": False, "taskId": task_id, "data": resp.get("data")}
    emit(result, args.json, "✓ 任务 %s 结果已返回：\n%s" % (task_id, json.dumps(resp.get("data"), ensure_ascii=False, indent=2)))
    return EXIT_OK


def cmd_stop(args) -> int:
    task_id = str(args.task_id).strip()
    if not task_id:
        print("✗ task-id 不能为空", file=sys.stderr)
        return EXIT_USAGE

    path = "/open/api/v1/tasks/%s/stop" % task_id
    if args.dry_run or not args.execute:
        human = "— dry-run，未发送请求 —\nPOST %s%s\n确认后追加 --execute。" % (get_base_url(), path)
        emit({"dryRun": True, "path": path}, args.json, human)
        return EXIT_OK

    try:
        resp = request("POST", path, {})
    except ConnectionError as exc:
        print("✗ 无法连接 %s：%s" % (get_base_url(), exc), file=sys.stderr)
        return EXIT_NETWORK

    if not is_success(resp):
        print("✗ 停止失败：HTTP %s code=%s msg=%s" % (resp.get("_httpStatus"), resp.get("code"), resp.get("msg")), file=sys.stderr)
        print(json.dumps(resp, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_API

    result = {"ok": True, "taskId": task_id, "code": resp.get("code"), "msg": resp.get("msg", "")}
    emit(result, args.json, "✓ 任务 %s 已停止（stop 后 status 应变为 4 已结束）。" % task_id)
    return EXIT_OK


# ---------------------------------------------------------------- 入口


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="flux_api", description="云实验平台试验任务 CLI")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON（可放在子命令前后）")

    # 同一组 flag 挂到每个子命令上，让 `flux_api.py --json create` 与
    # `flux_api.py create --json` 都能工作——上层 agent 的参数顺序不可控
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", dest="json", help="输出机器可读 JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", parents=[common], help="连通性与鉴权预检")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("validate", parents=[common], help="离线校验方案 JSON")
    p.add_argument("--recipes-file", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("create", parents=[common], help="创建试验任务")
    p.add_argument("--task-name", required=True)
    p.add_argument("--recipes-file", required=True)
    p.add_argument("--execute", action="store_true", help="真正发送请求（默认 dry-run）")
    p.add_argument("--dry-run", action="store_true", help="显式声明不发送（默认行为）")
    p.add_argument("--force", action="store_true", help="校验有 ERROR 时仍然下发")
    p.add_argument(
        "--template-id",
        default=None,
        help="覆盖 templateId。仅在平台方明确要求时使用；注意本字段默认按字符串下发，"
        "因为 2043611007682150400 超出 IEEE754 双精度安全整数范围，平台若用 JS Number 解析会丢精度。",
    )
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("start", parents=[common], help="启动试验任务")
    p.add_argument("--task-id", required=True)
    p.add_argument("--execute", action="store_true", help="真正发送请求（默认 dry-run）")
    p.add_argument("--dry-run", action="store_true", help="显式声明不发送（默认行为）")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("status", parents=[common], help="查询任务状态（只读）")
    p.add_argument("--task-id", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("result", parents=[common], help="回收任务结果（只读；未完成返回 pending）")
    p.add_argument("--task-id", required=True)
    p.set_defaults(func=cmd_result)

    p = sub.add_parser("stop", parents=[common], help="停止试验任务")
    p.add_argument("--task-id", required=True)
    p.add_argument("--execute", action="store_true", help="真正发送请求（默认 dry-run）")
    p.add_argument("--dry-run", action="store_true", help="显式声明不发送（默认行为）")
    p.set_defaults(func=cmd_stop)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())