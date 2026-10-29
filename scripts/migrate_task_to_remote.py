#!/usr/bin/env python3
"""Upload a persisted task JSON (data/tasks/*.json) to a remote OpenAIR API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_token() -> str | None:
    env_path = _repo_root() / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "OPENAIR_MIGRATION_TOKEN":
            return value.strip().strip('"').strip("'") or None
    return None


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    query: dict[str, str] | None = None,
) -> tuple[int, dict | list | str]:
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["X-OpenAIR-Migration-Token"] = token

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise SystemExit(f"request failed: {exc}") from exc

    if not raw:
        return status, {}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate persisted OpenAIR task JSON to remote API")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("OPENAIR_API_URL") or "https://openair-api.zeabur.app",
        help="Remote API base URL (default: OPENAIR_API_URL or Zeabur api)",
    )
    parser.add_argument(
        "--task-file",
        default=str(_repo_root() / "data" / "tasks" / "task_cs_ope_001.json"),
        help="Path to persisted task JSON",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPENAIR_MIGRATION_TOKEN") or _load_dotenv_token(),
        help="Migration token (must match remote OPENAIR_MIGRATION_TOKEN)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace task if it already exists on remote",
    )
    parser.add_argument(
        "--web-url",
        default=os.environ.get("OPENAIR_WEB_URL"),
        help="Workbench base URL for success output (default: OPENAIR_WEB_URL)",
    )
    args = parser.parse_args()

    task_path = Path(args.task_file)
    if not task_path.is_file():
        print(f"task file not found: {task_path}", file=sys.stderr)
        return 1
    if not args.token:
        print(
            "missing migration token: set OPENAIR_MIGRATION_TOKEN in .env or pass --token",
            file=sys.stderr,
        )
        return 1

    api_base = args.api_url.rstrip("/")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task_id = task.get("taskId") or task_path.stem

    health_status, health_body = _request("GET", f"{api_base}/health")
    if health_status != 200 or not isinstance(health_body, dict) or health_body.get("status") != "ok":
        print(f"remote health check failed: HTTP {health_status} {health_body}", file=sys.stderr)
        return 1
    print(f"[ok] health {api_base}/health")

    import_status, import_body = _request(
        "POST",
        f"{api_base}/api/admin/tasks/import",
        token=args.token,
        body=task,
        query={"overwrite": "true" if args.overwrite else "false"},
    )
    if import_status not in (200, 201):
        print(f"import failed: HTTP {import_status} {import_body}", file=sys.stderr)
        if import_status == 404:
            print(
                "hint: remote API may not include /api/admin/tasks/import yet — redeploy api service first",
                file=sys.stderr,
            )
        elif import_status == 503:
            print(
                "hint: set OPENAIR_MIGRATION_TOKEN on remote api service and redeploy",
                file=sys.stderr,
            )
        return 1

    get_status, get_body = _request("GET", f"{api_base}/api/tasks/{urllib.parse.quote(task_id)}")
    if get_status != 200 or not isinstance(get_body, dict):
        print(f"verify failed: HTTP {get_status} {get_body}", file=sys.stderr)
        return 1

    calls = get_body.get("capabilityCalls") or []
    print(f"[ok] imported task {task_id}")
    print(f"     title: {get_body.get('title')}")
    print(f"     status: {get_body.get('status')}  stage: {get_body.get('currentStage')}")
    print(f"     capabilityCalls: {len(calls)}  lastCallId: {get_body.get('lastCallId')}")
    if args.web_url:
        print(f"     workbench: {args.web_url.rstrip('/')}/workbench/{task_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
