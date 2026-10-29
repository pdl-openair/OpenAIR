"""Backfill strict Final Result authority chains for legacy completed tasks."""

from __future__ import annotations

import argparse
import json

from app.modules.legacy_final_result_backfill import (
    run_completed_progress_projection_repair,
    run_legacy_completed_final_result_backfill,
)
from app.modules.task_repository import TaskRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate and report eligible rows without writing.")
    parser.add_argument(
        "--repair-progress-projection",
        action="store_true",
        help="Repair completed authority rows whose durable progress projection is missing or stale.",
    )
    args = parser.parse_args()
    repository = TaskRepository()
    summary = (
        run_completed_progress_projection_repair(repository=repository, dry_run=args.dry_run)
        if args.repair_progress_projection
        else run_legacy_completed_final_result_backfill(repository=repository, dry_run=args.dry_run)
    )
    print(json.dumps({
        "dryRun": args.dry_run,
        "scanned": summary.scanned,
        "eligible": summary.eligible,
        "written": summary.written,
        "taskIds": list(summary.task_ids),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
