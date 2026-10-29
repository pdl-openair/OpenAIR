#!/usr/bin/env python3
"""Poll autonomous evolution task; log every poll; on completion dump full result."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_MCP_BASE = os.environ.get("MATERIALS_MCP_BASE_URL", "").rstrip("/")
MATTERGEN_URL = f"{_MCP_BASE}/mattergen/mcp" if _MCP_BASE else ""


def http_post_json(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}")
    if body.lstrip().startswith("event:") or "\ndata:" in body:
        lines = [ln for ln in body.splitlines() if ln.startswith("data:")]
        return json.loads(lines[-1][len("data:"):].strip())
    return json.loads(body)


def call_tool(name: str, args: dict, timeout: float = 120.0) -> dict:
    resp = http_post_json(MATTERGEN_URL, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }, timeout)
    if "error" in resp:
        raise RuntimeError(f"MCP error: {resp['error']}")
    result = resp.get("result", {})
    text = ""
    for item in result.get("content", []):
        if item.get("type") == "text":
            text = item.get("text", "")
            break
    if not text:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--max-wait", type=float, default=7200.0)  # 2h cap
    ap.add_argument("--log", required=True)
    ap.add_argument("--result", required=True)
    args = ap.parse_args()

    log = Path(args.log).open("a")
    start = time.time()
    last_status = None
    last_gen = None

    while True:
        elapsed = time.time() - start
        if elapsed > args.max_wait:
            log.write(f"[{elapsed:.0f}s] MAX_WAIT exceeded, exiting\n")
            log.flush()
            return 2
        try:
            r = call_tool("get_autonomous_evolution_result",
                          {"task_id": args.task_id, "top_pareto": 15}, timeout=90)
            status = r.get("status", "?")
            gen = r.get("current_generation")
            n_qual = r.get("n_qualified_so_far")
            pop_size = r.get("population_size")
            msg_bits = []
            if gen is not None: msg_bits.append(f"gen={gen}")
            if n_qual is not None: msg_bits.append(f"qual={n_qual}")
            if pop_size is not None: msg_bits.append(f"pop={pop_size}")
            line = f"[{elapsed:.0f}s] status={status} " + " ".join(msg_bits)
            # also dump a compact full snapshot every poll
            line += " | keys=" + ",".join(sorted(r.keys()))
            if status != last_status or gen != last_gen:
                log.write(line + "\n")
                log.flush()
                print(line, flush=True)
                last_status = status
                last_gen = gen
            if status == "completed":
                Path(args.result).write_text(
                    json.dumps(r, indent=2, ensure_ascii=False)
                )
                log.write(f"[{elapsed:.0f}s] COMPLETED, result saved to {args.result}\n")
                log.flush()
                print(f"COMPLETED in {elapsed:.0f}s", flush=True)
                return 0
            if status in ("failed", "error", "cancelled"):
                Path(args.result).write_text(
                    json.dumps(r, indent=2, ensure_ascii=False)
                )
                log.write(f"[{elapsed:.0f}s] {status}, result saved\n")
                log.flush()
                return 3
        except Exception as e:
            log.write(f"[{elapsed:.0f}s] poll error: {e}\n")
            log.flush()
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
