#!/usr/bin/env python3
"""Drive the CI unit-test suite: group runner, ratchet set-diff, ci-parity probe.

Split out of the unit-test job's inline python blocks (AI4SE harness refactor,
sync batch M2 / design §3.4). The three subcommands consume the same inputs
and emit the same output formats the inline blocks did, so the workflow keeps
only thin call lines and every decision rule becomes unit-testable here:

* ``run``    - one pytest process per selector run group, tee'd into
  ``unit-test-output.log`` with ``[group-start]/[group-boundary]`` markers,
  plus ``unit-test-groups.json`` driver evidence. Exit 1 when any group
  fails (the workflow marks this step continue-on-error; the verdict comes
  from ``ratchet``). With ``--shard i/k`` (debt-ledger ⑧) the selector
  groups are distributed across k shards by a deterministic plan; group
  indices stay globally unique across shards so a later ``merge`` can
  reconstruct one evidence stream. Big groups are split into whole-file
  chunks (files are never cut in half), everything else runs intact.
* ``ratchet``- D-15 option B set-diff: parse the driver log into per-group
  FAILED/ERROR node sets, diff against ``.github/unit-test-known-failures.json``.
  New failing node -> exit 1 (RED). All failures known -> exit 0. Known nodes
  that passed -> non-blocking shrink notice. Incomplete driver evidence
  (missing groups json, nonzero group exit with no node lines) -> exit 1
  (fail closed).
* ``merge``  - shard evidence aggregator (debt ⑧): concatenate k shards'
  ``unit-test-output.log`` / ``unit-test-groups.json`` artifacts into the
  single pair ``ratchet`` consumes. Any missing shard, malformed groups
  json, or gap/duplicate in the global group numbering -> exit 1
  (fail closed: incomplete evidence must read RED, not green-by-omission).
* ``probe``  - ci-parity re-admission probe (non-blocking in CI): rerun the
  files whose deselect/exclude rationale marks them as CI-parity suspects
  and report whether they are green in the CI environment.

Exit codes are the contract; keep them byte-compatible with the inline
blocks this file replaces (ledger sync.md keeps the before/after diff).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

COVERAGE_ARGS = [
    "--cov=services/api/app",
    "--cov=services/worker",
    "--cov=packages",
    "--cov-report=",
    "--cov-append",
]

NODE_LINE = re.compile(
    r"^(FAILED|ERROR) (\S+?::\S+?(?:\[[^\]]*\])?)(?:\s+-\s+.*)?$"
)

GROUP_START = "[group-start] index="
GROUP_BOUNDARY = "[group-boundary] index="


def _read_json(path: str) -> object:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _append_step_summary(text: str) -> None:
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _ceil_div(value: int, divisor: int) -> int:
    return -(-value // divisor)


def _parse_shard(spec: str | None) -> tuple[int, int]:
    """Parse ``--shard i/k`` (1-based); None/absent means the legacy single run."""
    if spec is None:
        return 1, 1
    try:
        raw_index, _, raw_count = spec.partition("/")
        index, count = int(raw_index), int(raw_count)
    except ValueError:
        raise SystemExit(f"run: --shard must look like 2/6, got {spec!r}") from None
    if count < 1 or not 1 <= index <= count:
        raise SystemExit(f"run: --shard out of range: {spec!r}")
    return index, count


def _owns_file(files: list[str], path: str) -> bool:
    """Does a chunk own ``path``? Selector deselect ids are rootdir-relative
    (the domain prefix is stripped for skills/services groups) while group
    files are repo-relative - match exactly or on a segment boundary."""
    return any(f == path or f.endswith("/" + path) for f in files)


def build_shard_units(
    runs: list[list[str]], deselects: list[list[str]], shard_count: int
) -> list[dict[str, object]]:
    """Explode selector groups into whole-file units, carrying their deselects.

    A group larger than the fair per-share share of the whole run is split
    into chunks of ceil(size / shard_count) files; every other group stays a
    single unit so its intra-group pytest semantics are untouched. Deselect
    nodes follow the chunk that owns their file, matched on the segment
    boundary to bridge the selector's rootdir-relative node ids (review
    finding 2); fail-closed if a node's file cannot be located - that would
    silently drop a deselect otherwise.
    """
    total = sum(len(group) for group in runs)
    fair = total if shard_count == 1 else max(1, _ceil_div(total, shard_count))
    units: list[dict[str, object]] = []
    for group, nodes in zip(runs, deselects, strict=True):
        if shard_count > 1 and len(group) > fair:
            chunk_size = max(1, _ceil_div(len(group), shard_count))
            chunks = [group[i : i + chunk_size] for i in range(0, len(group), chunk_size)]
            fresh = [{"files": chunk, "deselects": []} for chunk in chunks]
            units.extend(fresh)
            for node in nodes:
                path = node.split("::", 1)[0].replace("\\", "/")
                target = next(
                    (u for u in fresh if _owns_file(u["files"], path)), None
                )
                if target is None:  # deselect node without a home chunk
                    raise SystemExit(
                        f"run: deselect node {node!r} has no chunk in its group "
                        "- refusing to drop it silently"
                    )
                target["deselects"].append(node)
        else:
            units.append({"files": list(group), "deselects": list(nodes)})
    return units


def plan_shards(
    units: list[dict[str, object]], shard_count: int
) -> list[list[dict[str, object]]]:
    """Deterministic LPT distribution of units across shards.

    Biggest unit first onto the currently least-loaded shard (ties -> lowest
    index). Pure function of (units, shard_count): every shard computes the
    same plan locally, so global group numbering matches without any
    shard-to-shard communication. shard_count == 1 keeps unit order as-is.
    """
    if shard_count == 1:
        return [list(units)]
    loads = [0] * shard_count
    plan: list[list[dict[str, object]]] = [[] for _ in range(shard_count)]
    for unit in sorted(units, key=lambda u: len(u["files"]), reverse=True):
        target = min(range(shard_count), key=lambda s: (loads[s], s))
        plan[target].append(unit)
        loads[target] += len(unit["files"])
    return plan


def cmd_run(args: argparse.Namespace) -> int:
    """One pytest process per unit, tee'd with globally-numbered group markers."""
    selection = _read_json(args.selection)
    runs = selection["runs"]
    deselects = selection["run_deselects"]
    shard_index, shard_count = _parse_shard(args.shard)
    units = build_shard_units(runs, deselects, shard_count)
    plan = plan_shards(units, shard_count)
    # Global numbering across ALL shards (shard-major): each shard numbers
    # only its own groups but skips over the others' indices, so a merge of
    # every shard's evidence reconstructs one dense 1..N sequence.
    assignments: list[tuple[int, int, dict[str, object]]] = []
    index = 0
    for s_no, shard_groups in enumerate(plan, start=1):
        for unit in shard_groups:
            index += 1
            if s_no == shard_index:
                assignments.append((index, s_no, unit))
    failures = []
    groups_meta = []
    out_log = open(args.output_log, "w", encoding="utf-8", newline="\n")
    started = time.monotonic()
    if not assignments:
        print(
            f"[unit-test] shard {shard_index}/{shard_count}: no groups in the "
            "plan for this shard (selection smaller than shard count)"
        )
    for index, s_no, unit in assignments:
        group = unit["files"]
        argv = [sys.executable, "-m", "pytest", "-q", *group, *COVERAGE_ARGS]
        for node in unit["deselects"]:
            argv += ["--deselect", node]
        mark = time.monotonic()
        # Start marker first so the ratchet step can attribute the
        # child's output lines to this group index (the exit code is
        # only known afterwards, hence a separate end boundary).
        out_log.write(f"{GROUP_START}{index} files={len(group)}\n")
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        # Tee: stream child output live AND persist it for the
        # ratchet set-diff step (child is unbuffered via inherited env).
        child_out = proc.stdout
        if child_out is None:  # unreachable with stdout=PIPE; keep fail-closed
            raise RuntimeError("child stdout pipe missing")
        for line in child_out:
            sys.stdout.write(line)
            out_log.write(line)
            sys.stdout.flush()
            out_log.flush()
        proc.wait()
        elapsed = time.monotonic() - mark
        boundary = f"{GROUP_BOUNDARY}{index} files={len(group)} exit={proc.returncode}"
        print(
            f"[unit-test] group {index}/{len(units)}: {len(group)} file(s), "
            f"{elapsed:.1f}s, exit {proc.returncode} (shard {s_no}/{shard_count})"
        )
        out_log.write(boundary + "\n")
        if proc.returncode != 0:
            failures.append(index)
        groups_meta.append(
            {
                "index": index,
                "files": len(group),
                "exit": proc.returncode,
                "seconds": round(elapsed, 1),
                "shard": s_no,
            }
        )
    out_log.close()
    with open(args.groups_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(groups_meta, fh, indent=1)
    total = time.monotonic() - started
    summary = (
        f"unit-test: shard {shard_index}/{shard_count} ran "
        f"{len(assignments)} of {len(units)} groups / "
        f"{sum(len(unit[2]['files']) for unit in assignments)} files / "
        f"{total:.0f}s; failed groups: {failures or 'none'}"
    )
    print(f"[unit-test] {summary}")
    _append_step_summary(summary)
    return 1 if failures else 0


def normalize_node(node: str) -> str:
    """Normalize a pytest node id for comparison across invocation styles.

    pytest echoes the file part the way it was invoked: repo-relative on CI,
    possibly absolute on local reruns. Normalize separators and strip the cwd
    prefix so both forms compare.
    """
    file_part, sep, rest = node.partition("::")
    file_part = file_part.replace("\\", "/")
    cwd = os.getcwd().replace("\\", "/").rstrip("/") + "/"
    if file_part.startswith(cwd):
        file_part = file_part[len(cwd):]
    return file_part + sep + rest


def parse_group_buckets(log_path: str) -> dict[int, list[str]]:
    """Attribute each log line to the group-start marker it follows."""
    buckets: dict[int, list[str]] = {}
    current: int | None = None
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if line.startswith(GROUP_START):
                current = int(line.rsplit("index=", 1)[1].split()[0])
                buckets.setdefault(current, [])
                continue
            if line.startswith(GROUP_BOUNDARY):
                current = None
                continue
            if current is not None:
                buckets[current].append(line)
    return buckets


def collect_actual_nodes(
    groups: list[dict[str, object]], buckets: dict[int, list[str]]
) -> tuple[set[str], str | None]:
    """Failing node set across groups; or a fail-closed reason string."""
    actual: set[str] = set()
    for group in groups:
        nodes = {
            normalize_node(match.group(2))
            for text in buckets.get(int(group["index"]), [])
            if (match := NODE_LINE.match(text))
        }
        if group["exit"] != 0 and not nodes:
            return (
                set(),
                (
                    f"group {group['index']}: nonzero exit with no "
                    "node-level FAILED/ERROR summary (crash, collection "
                    "error, or truncated output) - failing closed"
                ),
            )
        actual |= nodes
    return actual, None


def cmd_ratchet(args: argparse.Namespace) -> int:
    """D-15 set-diff: red only on NEW failing nodes vs the known list."""
    def die(message: str) -> int:
        print(f"[ratchet] {message}")
        return 1

    try:
        groups = _read_json(args.groups_json)
    except OSError:
        return die(
            "driver evidence incomplete: unit-test-groups.json missing "
            "(driver crashed or was killed before finishing) - cannot "
            "judge the ratchet, failing closed"
        )
    if not isinstance(groups, list):
        return die("unit-test-groups.json is malformed (not a list) - failing closed")
    buckets = parse_group_buckets(args.output_log)
    actual, fail_closed = collect_actual_nodes(groups, buckets)
    if fail_closed is not None:
        return die(fail_closed)
    known_doc = _read_json(args.known_failures)
    if not isinstance(known_doc, dict):
        return die("known-failures manifest is malformed (not an object) - failing closed")
    known = {normalize_node(entry["id"]) for entry in known_doc["nodes"]}
    new = sorted(actual - known)
    healed = sorted(known - actual)
    print(
        f"[ratchet] actual failing nodes: {len(actual)}; "
        f"known: {len(known)}; new: {len(new)}; healed: {len(healed)}"
    )
    if new:
        print("[ratchet] NEW failing nodes (not in known-failures list):")
        for node in new[:50]:
            print(f"[ratchet]   {node}")
        if len(new) > 50:
            print(f"[ratchet]   ... and {len(new) - 50} more")
        return die(
            f"{len(new)} new failing node(s) - unit-test RED under "
            "ratchet semantics (D-15)"
        )
    if healed:
        notice = (
            f"ratchet can move down: {len(healed)} known-failure "
            "node(s) passed this run - remove them from "
            ".github/unit-test-known-failures.json"
        )
        print(f"[ratchet] {notice}")
        for node in healed[:20]:
            print(f"[ratchet]   healed: {node}")
        _append_step_summary(notice)
    print(
        "[ratchet] zero new failures - unit-test GREEN under "
        "ratchet semantics (D-15)"
    )
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """Concatenate shard evidence into the single pair ``ratchet`` consumes."""
    def die(message: str) -> int:
        print(f"[merge] {message}")
        return 1

    shards_dir = Path(args.shards_dir)
    group_docs = sorted(shards_dir.glob("shard-*/unit-test-groups.json"))
    log_paths = sorted(shards_dir.glob("shard-*/unit-test-output.log"))
    if len(group_docs) != args.expect or len(log_paths) != args.expect:
        found = sorted({p.parent.name for p in group_docs + log_paths})
        return die(
            f"incomplete shard evidence: expected {args.expect} shard(s), "
            f"found groups-json x{len(group_docs)} / output-log x{len(log_paths)} "
            f"in {shards_dir} ({', '.join(found) or 'none'}) - failing closed"
        )
    merged_groups: list[dict[str, object]] = []
    for group_doc in group_docs:
        try:
            doc = _read_json(str(group_doc))
        except (OSError, ValueError):
            return die(f"{group_doc} is unreadable or malformed JSON - failing closed")
        if not isinstance(doc, list):
            return die(f"{group_doc} is malformed (not a list) - failing closed")
        merged_groups.extend(doc)
    if not merged_groups:
        # Review finding 1: an all-empty merge would pass the density check
        # vacuously (range(1,1) == []) and ratchet would read GREEN on zero
        # evidence - green-by-omission. The current CI wiring blocks this by
        # accident (an empty shard uploads no .coverage, so mv fails and the
        # artifact never arrives); make the guard intrinsic, not accidental.
        return die(
            "merged evidence has zero groups - every shard reported an empty "
            "plan (selector regression?) - failing closed"
        )
    indices = sorted(int(group["index"]) for group in merged_groups)
    if indices != list(range(1, len(merged_groups) + 1)):
        return die(
            "global group numbering is not dense 1..N across shards "
            f"({len(merged_groups)} entries, indices {indices[:5]}...) - "
            "a shard ran a different plan - failing closed"
        )
    merged_groups.sort(key=lambda group: int(group["index"]))
    with open(args.groups_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(merged_groups, fh, indent=1)
    # Log order is irrelevant: every line is attributed by its group marker.
    with open(args.output_log, "wb") as out_log:
        for log_path in log_paths:
            out_log.write(log_path.read_bytes())
    print(
        f"[merge] {args.expect} shard(s) -> {len(merged_groups)} groups / "
        f"{sum(int(g['files']) for g in merged_groups)} files; "
        "evidence stream is dense and complete"
    )
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Non-blocking ci-parity re-admission probe for deselected/excluded files."""
    selection = _read_json(args.selection)
    if not isinstance(selection, dict):
        print("[probe] selection.json is malformed (not an object) - failing closed")
        return 1
    node_marker = "B5 re-admit on CI evidence"
    file_marker = "flagged for B5 CI verification"
    probed = {
        entry["node"].rsplit("::", 1)[0]
        for entry in selection["deselected_nodes_rationale"]
        if node_marker in entry["reason"]
    }
    probed.update(
        entry["path"]
        for entry in selection["excluded"]
        if file_marker in entry["reason"]
    )
    if not probed:
        print("[re-admission] nothing marked for CI re-admission; done")
        return 0
    red = []
    for path in sorted(probed):
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", path])
        state = "GREEN" if proc.returncode == 0 else "RED"
        print(f"[re-admission] {path}: {state} (exit {proc.returncode})")
        if proc.returncode != 0:
            red.append(path)
    if red:
        print(
            "[re-admission] still red in the CI env; the ci-parity "
            "debt entry keeps these deselected/excluded: " + ", ".join(red)
        )
    else:
        print(
            "[re-admission] all probed files green in the CI env; the "
            "ci-parity deselect/exclude entries are eligible for retraction"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run selector groups, one pytest per group")
    run_parser.add_argument("--selection", default="selection.json")
    run_parser.add_argument(
        "--shard",
        default=None,
        help="run only shard i of k (debt-ledger ⑧), e.g. --shard 2/6; "
        "omit for the legacy single-process run",
    )
    run_parser.add_argument("--output-log", default="unit-test-output.log")
    run_parser.add_argument("--groups-json", default="unit-test-groups.json")
    run_parser.set_defaults(func=cmd_run)

    ratchet_parser = sub.add_parser("ratchet", help="set-diff actual vs known failures")
    ratchet_parser.add_argument("--groups-json", default="unit-test-groups.json")
    ratchet_parser.add_argument("--output-log", default="unit-test-output.log")
    ratchet_parser.add_argument(
        "--known-failures", default=".github/unit-test-known-failures.json"
    )
    ratchet_parser.set_defaults(func=cmd_ratchet)

    merge_parser = sub.add_parser(
        "merge", help="concatenate shard evidence for ratchet (fail-closed)"
    )
    merge_parser.add_argument("--shards-dir", default="dist")
    merge_parser.add_argument(
        "--expect", type=int, default=6, help="expected shard count"
    )
    merge_parser.add_argument("--output-log", default="unit-test-output.log")
    merge_parser.add_argument("--groups-json", default="unit-test-groups.json")
    merge_parser.set_defaults(func=cmd_merge)

    probe_parser = sub.add_parser("probe", help="ci-parity re-admission probe")
    probe_parser.add_argument("--selection", default="selection.json")
    probe_parser.set_defaults(func=cmd_probe)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
