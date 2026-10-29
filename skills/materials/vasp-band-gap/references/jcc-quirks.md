# jcc CLI Quirks (HPCSlurmArm)

The jcc CLI (`jcc.jointcloud.net`) is the bridge for submitting VASP jobs
to 长沙超算-ARM集群 (HPCSlurmArm, clusterID=1830873578531228942). It has
several non-obvious behaviors that cost significant debugging time.

## 1. Output Routing: stderr vs stdout

jcc writes its progress messages (`[1/4] 查询集群...`, `[4/4] jobSetID=...,
taskID=...`) to **stderr**, not stdout. The JSON status response, however,
goes to **stdout**.

**Implication:** `task_output=$(jcc hpc create ... 2>/dev/null)` produces an
empty `task_output`. The orchestrator cannot parse `jobSetID` from it and
thinks the call failed — even though the task was successfully submitted
on the cluster.

**Fix:** For `jcc hpc create` and `jcc hpc log`, capture both streams:
`task_output=$(jcc hpc create ... 2>&1)`. For `jcc hpc status`, the JSON
is on stdout so `2>/dev/null` is fine (but `2>&1` also works since the awk
JSON extractor ignores non-JSON lines).

## 2. Result Zip Propagation Delay

After task `status` becomes `Completed`, the result zip is **not**
immediately downloadable. Calling `jcc hpc result download` returns:

```
API 错误 code=UnknownError message=query result error: data returan fail, no package found
```

The zip takes 2–10 minutes to propagate from the cluster → storage
backend. The current retry budget in `run_hpc.sh` is 10 minutes
(`RESULT_MAX_RETRIES=30 × RESULT_RETRY_INTERVAL=20s`), env-overridable.

If you've given up on a download and want to retry it later, the
`jcc hpc result --task-name <name>` listing (without `download`) tells
you which files are ready server-side. Once you see files like
`workdir/vasprun.xml`, `workdir/CONTCAR`, the zip download will work.

## 3. Task Name Uniqueness

jcc rejects duplicate task names cluster-wide. If you re-invoke the
orchestrator with the same `--task-name`, you get an "already exists"
error.

**Fix:** The orchestrator appends a 6-char random suffix per attempt:
`task_name="${NAME}-${suffix}-${rand_suffix}"` where `rand_suffix` is
base32-encoded urandom. Even within a single run, retries use a fresh
suffix.

## 4. commonfile Upload Drops Small Files

`jcc commonfile create --path <dir>` uploads all files in the directory
but **intermittently drops small ones** (< 1 KB). INCAR (~246 B) and
KPOINTS (~66 B) are routinely missing from the upload, while POSCAR
(~700 B) and POTCAR (~436 KB) consistently make it.

**Fix:** `run_hpc.sh` has an upload verification loop: lists uploaded
files via `jcc commonfile query`, compares against the expected file list,
and on mismatch deletes the package and re-creates it with a new name
(up to 4 retries). Without this loop, roughly 1 in 3 uploads fails.

## 5. VASP Result Zip Layout

The downloaded zip nests VASP outputs under a `workdir/` subdirectory:

```
MoS2-mp-2815-02_static-LNAXTB.zip
├── hfuklylk72uwx.sh             # slurm script
├── hfuklylk72uwx_5924904.err    # slurm stderr
├── hfuklylk72uwx_5924904.out    # slurm stdout
└── workdir/                     # actual VASP output
    ├── INCAR, POSCAR, KPOINTS, POTCAR
    ├── CONTCAR, OUTCAR, OSZICAR
    ├── vasprun.xml
    ├── CHGCAR, AECCAR0/1/2, LOCPOT
    ├── EIGENVAL, PROCAR, IBZKPT
    └── ...
```

The orchestrator does `mv workdir/* . && rmdir workdir/` after unzip to
flatten the layout, so subsequent lookups can use `$step_dir/download/CONTCAR`
directly.

## 6. Cluster Name Aliasing

CLI flag `--cluster-name HPCSlurmArm` (the jcc app name) maps to
clusterID=1830873578531228942, whose display name in the JSON response is
`长沙超算-ARM集群`. All three refer to the same physical cluster. Use
`HPCSlurmArm` in CLI calls; the response will say `长沙超算-ARM集群`.

## 7. Token Lifetime & Re-login

`JCC_TOKEN` from `jcc login` lives for several hours. If a long-running
orchestrator (multi-step) sees auth failures mid-run, the fix is to
re-login before each step. The current `run_hpc.sh` logs in at the start
of each invocation (one call per step), which sidesteps this.
