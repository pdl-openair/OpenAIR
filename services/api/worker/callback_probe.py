"""One-shot HTTP-only worker callback probe for lease acceptance evidence."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main() -> int:
    api = os.environ["OPENAIR_API_BASE_URL"].rstrip("/")
    run_id = os.environ["OPENAIR_DURABLE_PROBE_RUN_ID"]
    payload = {
        "workerId": os.environ.get("OPENAIR_DURABLE_WORKER_ID", "probe"),
        "leaseEpoch": int(os.environ.get("OPENAIR_DURABLE_PROBE_LEASE_EPOCH", "0")),
        "type": os.environ.get("OPENAIR_DURABLE_PROBE_TYPE", "succeeded"),
        "payload": {},
    }
    request = Request(
        f"{api}/internal/durable-runs/{run_id}/callback",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-OpenAIR-Worker-Token": os.environ["OPENAIR_WORKER_AUTH_TOKEN"]},
    )
    try:
        with urlopen(request, timeout=15) as response:  # nosec B310: local test API
            print(json.dumps({"httpStatus": response.status, "body": response.read().decode("utf-8")}))
            return 0
    except HTTPError as exc:
        print(json.dumps({"httpStatus": exc.code, "body": exc.read().decode("utf-8")}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
