---
name: dev-stack-debug
description: >
  Debug full-stack issues in a running dev stack (docker + API + worker + frontend)
  by reading logs directly, writing API tests, and targeting user stories until
  they pass. Use this skill whenever the user reports a bug in a running local dev
  environment — frontend rendering wrong, button stuck, execution failed, clone
  broken, "暂不可用", blank workbench, 4xx/5xx errors. Covers: starting/monitoring
  the dev stack, reading API/worker logs, writing HTTP API verification scripts,
  identifying known bug magnets (progressProjection, clone fork fields, quota
  exhaustion), and iterating on fixes until user stories pass.
---

# Dev Stack Debug

When the user reports a bug in a running local dev stack, follow this workflow.
The key principle: **you can read logs and run API tests yourself** — never ask
the user to paste logs or error messages. They are your background processes.

## 1. Stack lifecycle

### Starting the stack

```bash
# Terminal 1: infrastructure
docker compose up -d postgres minio

# Terminal 2: API (background)
cd services/api
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 3: Worker (background) — needed for research-start + execution
cd services/api
.venv/Scripts/python.exe -m worker.entrypoint

# Terminal 4: Frontend (background)
pnpm.cmd --filter @openair/web dev
```

All four components must run. Missing the worker → task creation hangs in
"waiting". Missing the API → frontend has nothing to talk to.

### Where logs live

Logs are written to `$CLAUDE_JOB_DIR/tmp/{api,worker,web}.log`. **Read them
directly** with `tail` / `grep` — do not ask the user to copy-paste.

### Health check sequence

```bash
curl -s http://127.0.0.1:8000/health          # API
grep "worker_first_poll_ready" worker.log      # Worker
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5173  # Frontend
```

### Restarting after code changes

After fixing backend code: restart **both API and worker** (the worker imports
the same modules). Frontend changes are picked up by Vite HMR automatically.

After migration changes: the API auto-applies migrations at bootstrap
(`migrations_auto_apply=true`). For immediate testing, apply manually:

```bash
docker compose exec -T postgres psql -U openair -d openair -f - < services/api/migrations/<migration>.sql
```

## 2. Debug methodology

### Step 1: Read the logs first

When a user reports "it failed" or "it's wrong", the logs tell you what happened
before anything else. The sequence:

1. **API log**: `grep -E "<task_id>|→ [45][0-9][0-9]|Traceback|Exception|ERROR" api.log`
2. **Worker log**: `grep -E "<task_id>|dispatch|failed|error|rejected|claimed" worker.log`
3. **Frontend log**: rarely needed (Vite HMR errors only)

Key log patterns:
- `→ 200` with wrong data → **projection/serialization** bug (data correct in DB but API response wrong)
- `→ 404` → scope mismatch or missing field in artifact/runtime data
- `→ 409` → revision conflict or state precondition not met
- `→ 428` → missing required request fields
- `→ 500` → unhandled exception; read the traceback
- `→ 503` → postgres middleware allowlist (new write route not registered in `main.py`)
- `quota_exhausted` → user's daily token quota is spent (not a code bug)
- `artifact_schema_invalid` → model under-produced a complex contract
- `worker_first_poll_ready` absent → worker crashed at startup (read traceback)

### Step 2: Inspect the DB

When logs show the API returning wrong data, compare the **DB row** vs the **API
response** to find where the transformation breaks:

```bash
# Task state
docker compose exec -T postgres psql -U openair -d openair -c \
  "SELECT task_id, status, revision, payload->>'creationStatus',
   jsonb_array_length(payload->'substageRuntimeStates') AS srs,
   payload->'progressProjection' IS NOT NULL AS has_pp
   FROM openair_tasks WHERE task_id='<id>';"

# Artifact storage
docker compose exec -T postgres psql -U openair -d openair -c \
  "SELECT artifact_id, storage_type, source_task_id, content_sha256
   FROM openair_artifacts WHERE source_task_id='<id>';"
```

### Step 3: Write an API test

Write a small Python script (using stdlib `urllib`) that hits the real API and
asserts on the response. This is more reliable than asking the user to click
through the UI and describe what they see.

```python
import json, urllib.request

BASE = "http://127.0.0.1:8000"
H = {"X-OpenAIR-Owner-Id": "<owner>", "X-OpenAIR-Workspace-Id": "<ws>",
     "X-OpenAIR-Created-By": "<owner>", "Content-Type": "application/json"}

def get(path):
    r = urllib.request.Request(BASE + path, headers=H)
    with urllib.request.urlopen(r, timeout=20) as resp:
        return resp.status, json.loads(resp.read().decode())

# Test the failing endpoint
status, body = get("/api/tasks/<task_id>")
print(status, body.get("creationStatus"), body.get("progressProjection") is not None)
```

Save scripts to `$CLAUDE_JOB_DIR/tmp/`. Use ASCII-only request bodies (shell
mangles UTF-8 in `curl -d`).

### Step 4: Fix → restart → re-test

After fixing code:
1. Restart the affected process (API, worker, or both)
2. Re-run your API test script
3. If it passes, tell the user to verify in the UI

Never hand off a fix to the user for UI testing without first verifying it via
API yourself.

## 3. Known bug magnets

These are recurring failure patterns in this codebase. When debugging, check
them first — they account for the majority of user-visible bugs.

### progressProjection is null

**Symptom**: Workbench renders wrong stage count (shows all 4 stages instead of
the design's actual instances), or start button is stuck.

**Root cause**: `progressProjection` is null in the task's payload. The frontend's
`resolveProgressTrust` (workbenchTask.ts:297) returns `{kind: "legacy"}` when
projection is null → legacy mode renders the hardcoded 4-stage `stageDetails`
instead of the canonical `stageProgress`.

**How to verify**: Check if `payload->'progressProjection' IS NULL` in the DB.

**Fix pattern**: Any code path that creates, clones, or resets a task must call
`project_task_progress(aggregate, revision=N)` to generate an initial projection.
Without it, the frontend immediately falls to legacy mode. See the memory note
`feedback_progress_projection_bug_magnet` for the full checklist.

### Clone fork missing carry/reset fields

**Symptom**: Cloned task shows wrong stages, can't start, or artifacts are
invisible/broken.

**Root cause**: `_fork_v12_contract_into` (routes/tasks.py) carries design fields
and resets execution state, but if it misses a field, the clone has an incomplete
payload. Known past issues:
- Missing `creationStatus` → start route rejects ("task not started")
- Missing `substageRuntimeStates` → `_next_ready_instance_id` fails
  (`ready_instance_missing`) because there are no "ready" runtime rows
- Missing `progressProjection` → legacy fallback (see above)

**Fix pattern**: The fork must:
1. Carry: `researchDesignVersions`, `planningAttempts`, `confirmations`,
   `registrySnapshotRef`, `activeResearchDesignRef`, `executionPolicyVersion`,
   `executionMode`, `pendingExecutionMode`, `creationStatus`
2. Reset: `executionDecisions`, `workUnits`, `capabilityCalls`,
   `agentInteractionRecords`, `artifacts`, `progressProjection`, `startedAt`,
   `finishedAt`
3. Generate: `substageRuntimeStates` (one "ready" row per design instance),
   `progressProjection` (via `project_task_progress`)

### Object-storage artifact content 404

**Symptom**: Artifact visible in task list but clicking it shows "当前产物正文
暂时无法打开" (content unavailable).

**Root cause**: `_read_task_scoped_artifact` (routes/tasks.py:255) checks
`has_canonical_storage` — the check only accepted `storage_type == "local"`,
rejecting `"object"`. Cloned/reused artifacts are object-stored → false 404.

**Fix**: Accept `storage_type in ("local", "object")` in the storage check.

Also: `ArtifactContentOut.sourceMode` must include `"object"` in its `Literal`
(schemas.py:500) or the response model rejects the valid `"object"` sourceMode
→ 500.

### Quota exhaustion with no user-visible message

**Symptom**: Task shows "已失败" with no explanation. Worker log shows
`error=quota_exhausted`. DB workUnit has `terminalReasonCode=quota_exhausted`
correctly — yet the API's `progressProjection.substageProgress.statusReasonCode`
reads `execution_failed` (generic).

**Root cause (two layers)**: The reason is set correctly at the *data* layer
(`_task_with_outcome` writes `workUnit.terminalReasonCode` AND
`substageRuntimeState.statusReasonCode`). But the **projection layer** discarded
it: `canonical_progress_reducer._substage_status` hardcoded
`return "failed", "execution_failed"` for the failed branch — ignoring the
recorded reason. Worse, `project_task_progress` (which runs on every CAS write)
then syncs that generic code *back* into `substageRuntimeState.statusReasonCode`,
**overwriting the real reason in the payload**. So the runtime value gets
clobbered too; only `workUnit.terminalReasonCode` survives.

**Fix**: In the reducer's failed branch, read the reason from
`workUnit.terminalReasonCode` first (authoritative — never clobbered by
projection-sync), fall back to `runtime.statusReasonCode`, then the generic
`execution_failed`. The workUnit-first order is essential — it recovers the
reason even for tasks whose runtime was already overwritten by an older projection.

**Frontend**: `workbenchUi.ts` `FAILURE_REASON_LABELS` maps known codes
(`quota_exhausted`, `artifact_schema_invalid`, …) to Chinese headlines/actions.

**Healing existing tasks**: projection only recomputes on the *next* CAS write,
so an already-failed task keeps its stale `execution_failed` until then. To fix
one immediately, patch the stored payload in postgres (both
`substageRuntimeStates[].statusReasonCode` and
`progressProjection.substageProgress[].statusReasonCode`), or trigger any state
transition that re-runs `project_task_progress`.

**General lesson**: when the DB has the right value but the API returns a wrong/
generic one, suspect the **projection/reducer layer** between them — not the
writer. `→ 200` with wrong data = projection bug (see §2 Step 1).

### HUIYAN provider schema rejection

**Symptom**: Planning or execution fails with `400 Invalid schema for
response_format: 'uniqueItems' is not permitted`.

**Root cause**: The HUIYAN/OpenAI-compatible provider rejects certain JSON Schema
keywords in `response_format`. The planner and artifact contracts use Draft-2020-12
with `uniqueItems`, `$ref/$defs`, `if/then/else` — the provider can't enforce
these.

**Fix**: Remove `uniqueItems` from response_format schemas (planner repair schema,
etc.). For artifact contracts, the provider uses `json_object` mode (no strict
schema), so the model must follow the schema from the prompt. When it doesn't
(under-production), the execution-artifact repair mechanism feeds validation
errors back to the model for a retry.

## 4. User story driven testing

When fixing a bug, define the acceptance criteria as a **user story** before
writing code. Share it with the user for alignment, then iterate until the story
passes.

Format:
```
作为<角色>，我想<动作>，以便<目的>。
Given <precondition>
When <user action>
Then <expected outcome>
```

Test the story via API first (you control the test), then hand off to the user
for UI verification. If the API test passes but the user still sees the bug,
the issue is likely:
- **Stale browser state** (hard refresh / clear cache)
- **Old data** (the fix only applies to new records; existing ones have the old
  broken state — tell the user to create a new record, not reuse the old one)
- **Frontend caching** (Vite HMR didn't pick up the change — restart Vite)
