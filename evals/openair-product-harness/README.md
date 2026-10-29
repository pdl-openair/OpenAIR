# OpenAIR Product Harness — P2.1 Minimal Skeleton

## Purpose

This directory is the first product-level browser evidence harness for the external OpenAIR research workbench.
It does not extend Skill Lab and does not modify product runtime code.

The first baseline remains deliberately small:

1. API and task-board preflight.
2. UI draft-task creation, task detail, task board, and refresh recovery.
3. One seeded running-task advance action, followed by refresh and cross-page consistency checks.
4. A degraded-mode transparency gate: if `/api/tasks` fails and the board silently renders front-end Fixture data, the user-visible page must disclose that fact.

## Why the mainline is split into two slices

The current UI creates a draft task with `creationStatus=clarifying`. It cannot immediately advance a research substage until planning and confirmation have completed. The baseline therefore checks:

- draft creation and recovery on the newly created task;
- step advancement and cross-page consistency on an existing seeded task.

This preserves the actual product boundary. It does not fake a smooth flow that the product does not currently implement.

## Isolated runtime boundary

The runner never stops or reuses an existing OpenAIR development server. It also never moves or writes the repository's real `data/tasks` directory.

Each run:

1. selects free loopback ports from dedicated Harness ranges;
2. copies the minimal API runtime into the external run directory;
3. starts a fixture-seeded Replay API from that isolated runtime;
4. starts the web app on its own port and points it to the isolated API;
5. preserves the isolated runtime and generated task data under `RUN_DIR` for review.

The isolated API copy contains only:

```text
services/api/app
fixtures
schemas
skills
mocks
data/tasks                  # new empty isolated persistence directory
```

It does **not** copy `.env`, `.venv`, or the repository's real `data/tasks`.
It also does **not** copy `.claude`, audit worktrees, Agent temporary trees, or
runtime-generated logs/results. Isolated mode is fixture/replay-only; durable
Research Start, worker, PostgreSQL, live provider, Artifact, Skill-production,
and Golden Path specs must use production-stack mode.

## Production-stack mode

Production-path specs connect Playwright to an already-running Web/API/durable-worker/PostgreSQL stack;
the runner never starts, stops, copies, or mutates that stack. Before tests begin it requires
`/health/diagnostics` to report `persistenceBackend=postgres`; the existing durable-worker
service remains the stack's execution authority (the API may correctly report `runtimeType=inline`).

```powershell
npm run eval:production-stack -- -TestSpec tests/11-research-start-draft-workbench.spec.ts
```

The isolated runtime directory is intentionally named `api` rather than a long descriptive folder name. This keeps nested Skill replay paths below the Windows `MAX_PATH` boundary on default Windows configurations.

## Process launch and cleanup safety

The isolated API is started through a dedicated child PowerShell wrapper. The wrapper writes an explicit startup marker before invoking Python with unbuffered output (`-u`). If readiness fails, the runner prints the isolated API log tail immediately.

Cleanup never uses `taskkill /T`. The runner only calls `Stop-Process` for owned child processes and listeners bound to its dedicated isolated ports. Existing OpenAIR services remain untouched.

## Evidence output

The PowerShell runner writes evidence outside the repository:

```text
$HOME\Downloads\openair-product-harness-runs\<timestamp>\
├─ screenshots\
├─ test-results\              # Playwright traces
├─ html-report\
├─ api-snapshots\
├─ gates\
├─ network-console\
├─ server-logs\
├─ api\
├─ playwright.stdout.log
├─ playwright.stderr.log
└─ REPORT.txt
```

## Run on Windows PowerShell

```powershell
cd C:\path\to\OpenAIR\evals\openair-product-harness
powershell.exe -ExecutionPolicy Bypass -NoProfile -File .\scripts\run-baseline.ps1
```

Equivalent npm script:

```powershell
npm run eval:baseline
```

Reuse installed dependencies and Chromium after the first run:

```powershell
powershell.exe -ExecutionPolicy Bypass -NoProfile -File .\scripts\run-baseline.ps1 -SkipInstall
```

Equivalent npm script:

```powershell
npm run eval:baseline:skip-install
```

## Current expected baseline risks

The harness intentionally contains deterministic gates that may fail on the current product baseline:

- `DG08`: if the UI reports “所有步骤已完成”, the persisted task status must also become `completed`.
- `DG11`: if `/api/tasks` fails and the board renders Fixture data, ordinary users must see an explicit Fixture / Replay / degraded-mode disclosure.

A non-zero test exit code can therefore represent a correctly detected product issue rather than a broken Harness.
