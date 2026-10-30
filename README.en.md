<p align="center">
  <img src="apps/web/public/brand/openair-logo.png" alt="OpenAIR" width="200"/>
</p>

<h3 align="center">A Research-Agent Platform for Materials Discovery</h3>
<p align="center">
  Modeling the research process — <b>problem modeling → solution design → execution & validation → insight generation</b> — as an <b>orchestrable, verifiable, and traceable</b> pipeline
</p>

<p align="center">
  <a href="https://github.com/pdl-openair/OpenAIR/releases"><img alt="Release" src="https://img.shields.io/github/v/release/pdl-openair/OpenAIR?color=blue"></a>
  <a href="https://github.com/pdl-openair/OpenAIR/actions/workflows/oss-ci.yml"><img alt="CI" src="https://github.com/pdl-openair/OpenAIR/actions/workflows/oss-ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-blue"></a>
  <a href="https://github.com/pdl-openair/OpenAIR/releases/download/v0.1.0/openair-demo-v0.1.0.mp4">Demo Video</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

<p align="center"><a href="README.md">简体中文</a> | <b>English</b></p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-validated-scenarios">Validated Scenarios</a> ·
  <a href="#-deployment-guide">Deployment</a> ·
  <a href="#️-roadmap">Roadmap</a> ·
  <a href="#-contributing">Contributing</a>
</p>

---

## 🗞️ News

- **2026-10-29**　🚀 v0.1.0 — first public release: four-stage orchestration runtime, 200+ research skills, evaluation & replay assets, dual execution mode (live / replay). [Release Notes](https://github.com/pdl-openair/OpenAIR/releases/tag/v0.1.0)

## 📖 Overview

Research suffers from **long cycles, fragmented tooling, untraceable evidence, and underused experimental feedback**. OpenAIR models the research process as a four-stage lifecycle orchestrated by the platform: AI provides capabilities as *skills* at every stage, while researchers confirm plans, track progress, and verify evidence from a unified workbench.

**Highlights**

- 🔁 **Four-stage process model** — problem_modeling → solution_design → execution_validation → insight_generation. An organizational frame, not a rigid pipeline: new computational results or experimental feedback can re-enter earlier stages for supplementary analysis
- 🧩 **200+ Markdown-shaped skills** — SKILL.md + input/output schema contracts, unified registry, independently testable and replayable
- 🔍 **Traceable evidence** — every artifact's provenance chain (which skill, which inputs, which version) can be inspected end-to-end
- 🔬 **Dry/wet-lab synergy** — computational validation and experimental feedback iterate in one closed loop
- 🎭 **Dual execution mode** — `SKILL_EXEC_MODE=live` calls a real model gateway; `replay` replays fixtures so you can **walk the full pipeline with zero API keys**

## ✨ Features

| Capability | Description |
|---|---|
| Natural-language workbench | Start from a one-line research intent; confirm plans, track stage progress, browse artifacts |
| Skill registry & scheduling | Declarative registry + skill-bindings; skills auto-orchestrated per substage |
| Artifacts & evidence chain | Artifacts are first-class citizens with fully traceable provenance |
| Evaluation system | Effect evals (`evals/`), contract suites (`verification/suites/`), replay fixtures (`fixtures/`) |
| Platform integration | HPC (JCC), cloud lab platform, materials MCP, literature retrieval — all via environment variables, vendor-neutral |
| Multi-user & quotas | JWT login, bootstrap admin, user quotas (trial / free / pro / internal) |

## 🏗️ Architecture

```text
┌─────────────┐   ┌────────────────────────────────────────────┐
│  apps/web   │   │  services/api (FastAPI orchestration)       │
│  React workbench │──▶│ four-stage orchestration · artifacts · skills │
└─────────────┘   └──────────────┬─────────────────────────────┘
                                 │ SKILL_EXEC_MODE = live │ replay
                  ┌──────────────▼──────────┐   ┌──────────────────┐
                  │ skills/ + schemas/       │   │ services/worker   │
                  │ 200+ skills · registry   │   │ packages/llm_core │
                  └─────────────────────────┘   └──────────────────┘
```

- **Workbench** (`apps/web`): React 18 + Ant Design — task board / detail / artifact library / resource plaza
- **Orchestration runtime** (`services/api`): FastAPI, four-stage lifecycle, PostgreSQL persistence, durable-worker dispatch
- **Skill layer** (`skills/` + `schemas/`): 12 domain packs (materials / opentrajectory / pvd / cs_research …), `_template` starter kit
- **Contract & adapter layer** (`packages/`): `@openair/shared` type contracts / vendor-neutral `llm_core` transport / MCP · skill adapters

## 🔬 Validated Scenarios

| Scenario | Pipeline | Result |
|---|---|---|
| **Crystalline material discovery** | candidate generation → evolutionary search → computational validation (VASP band gap / stability) | search final evaluation **mSUN 58.0%** |
| **AlN thin-film PVD parameter optimization** | literature evidence → parameter recommendation → wet-lab fabrication → feedback iteration | four feedback rounds, d33 improved from 15.9 to **18.6** |

🎬 Full pipeline demo: [openair-demo-v0.1.0.mp4](https://github.com/pdl-openair/OpenAIR/releases/download/v0.1.0/openair-demo-v0.1.0.mp4)

---

## 🚀 Quick Start

> [!TIP]
> Just want to look around? Skip all key configuration and jump to the [zero-key replay demo](#zero-key-replay-demo-recommended-first-step).

### Prerequisites

- Docker 24+ (Docker Desktop or Engine with compose v2); for a manual setup see the [Deployment Guide](#-deployment-guide)

<details>
<summary><b>Option 1: one command via the openair CLI (recommended)</b></summary>

```bash
pipx install openair-cli        # or: pip install --user openair-cli
openair up                      # clone → generate .env (randomized secrets) → start the stack → open the browser
openair up --mode replay        # zero-key replay demo — recommended first run
```

The first run generates and prints the login credentials **exactly once** (also written to `.env`). Before the PyPI package is published, install locally from this repo with `uv tool install --force ./cli`. More: `openair status / seed / down` (see [cli/README.md](cli/README.md)).

</details>

### Option 2: manual, three steps

### 1. Clone & configure

```bash
git clone https://github.com/pdl-openair/OpenAIR.git
cd OpenAIR
cp .env.example .env
```

The **4 variables compose hard-requires** in `.env` (the template ships local defaults, so it boots out of the box):

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | database password (must match between API and worker) |
| `OPENAIR_MINIO_ROOT_PASSWORD` | MinIO password |
| `OPENAIR_JWT_SECRET_KEY` | login JWT signing key |
| `OPENAIR_WORKER_AUTH_TOKEN` | API ↔ worker mutual auth token |

> [!IMPORTANT]
> For any externally reachable deployment, replace all four with random values (`openssl rand -hex 32`).

**Login account** (compose enforces authentication — this is required):

```bash
OPENAIR_BOOTSTRAP_ADMIN_EMAIL=you@example.com    # admin email
OPENAIR_BOOTSTRAP_ADMIN_PASSWORD=your-password   # at least 8 chars
OPENAIR_BOOTSTRAP_ADMIN_USERNAME=ops             # defaults to ops
```

### 2. Start

```bash
docker compose up -d --build     # first build takes a few minutes; re-run docker compose up -d after .env edits
```

> Slow/failing image pulls on mainland-China networks? See [Deployment Guide · Network configuration](#-deployment-guide).

### 3. Verify

| Service | URL |
|---|---|
| Workbench (Web) | http://localhost:8080 |
| API health | http://localhost:8000/health |
| MinIO console | http://localhost:9001 |

Log in to the workbench with the bootstrap admin account and start your first task.

#### Zero-key replay demo (recommended first step)

Set the following in `.env` and restart (`docker compose up -d`):

```bash
SKILL_EXEC_MODE=replay          # execution replays fixtures; no real LLM calls
OPENAIR_DEV_SEED_FIXTURES=1     # seed demo tasks at startup
```

## 📦 Deployment Guide

<details>
<summary><b>Network configuration (mainland China)</b></summary>

If image pulls are slow or fail, configure Docker registry mirrors:

- **Docker Desktop**: Settings → Docker Engine, edit the JSON, then Apply & Restart
- **Linux**: edit `/etc/docker/daemon.json`, then `sudo systemctl restart docker`

```json
{
  "builder": { "gc": { "defaultKeepStorage": "20GB", "enabled": true } },
  "registry-mirrors": [
    "https://docker.1panel.live",
    "https://hub.rat.dev",
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ]
}
```

> Mirror availability changes over time — replace with currently working mirrors if all fail.

For the manual (no-Docker) path, you can also switch package indexes:

```bash
pnpm config set registry https://registry.npmmirror.com
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
```

</details>

<details>
<summary><b>Configuration groups</b> (full per-variable notes in <code>.env.example</code>)</summary>

| Group | Key variables | When needed |
|---|---|---|
| Execution mode | `SKILL_EXEC_MODE=live \| replay` | default `live`; `replay` for a zero-key walkthrough |
| Demo data | `OPENAIR_DEV_SEED_FIXTURES=1` | great for first-time users; keep 0 for daily use |
| Skill/Agent gateway | `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, `OPENAIR_CLAUDE_MODEL` | required for `live`; any Anthropic-compatible gateway |
| Planner (proposal/solution) | `LLM_PROVIDER_BASE_URL`, `LLM_PROVIDER_API_KEY`, `LLM_PROVIDER_MODEL` | required for `live`; OpenAI-compatible endpoint (avoid `…/api/anthropic` paths — they 404) |
| Literature retrieval | `OT_DEEPXIV_TOKEN`, `SEMANTIC_SCHOLAR_API_KEY` | only for literature skills |
| Materials MCP | `MATERIALS_MCP_BASE_URL`, `MP_API_KEY` | for live crystal-design pipelines |
| HPC (JCC) | `JCC_BASE_URL / JCC_USERNAME / JCC_PASSWORD / JCC_CLUSTER_NAME / JCC_CLI_PATH` | only when connecting HPC; the `jcc` CLI is not distributed in this repo — install it yourself and point `JCC_CLI_PATH` at it |
| Cloud lab platform | `FLUX_BASE_URL`, `FLUX_API_KEY` | requires institutional VPN and a platform account; without it, the resource plaza falls back to a local demo snapshot |

</details>

<details>
<summary><b>Services & ports</b></summary>

| Service | Port | Notes |
|---|---|---|
| web | 8080 (override via `OPENAIR_WEB_PORT`) | nginx same-origin reverse proxy to api |
| api | 8000 | FastAPI |
| postgres | 15432 (host) | database |
| minio | 9000 / 9001 | S3 / console |
| redis | 16379 | queue |

</details>

<details>
<summary><b>Manual deployment (no Docker)</b></summary>

Frontend (Node 20+ / pnpm 9):

```bash
pnpm install
pnpm --filter @openair/web dev
```

Backend (Python 3.12+, dependencies managed with [uv](https://docs.astral.sh/uv/); keep `.env` at the repo root):

```bash
cd services/api
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# → http://localhost:8000/health returns {"status":"ok"}
```

Windows note: if `uv sync` fails with an SSL error, rebuild the venv with a signed Python (`uv venv --python <path-to-python.exe>`). See the [local development doc](docs/03_工程/待专项更新/LOCAL_DEVELOPMENT.md).

</details>

## 🗺️ Roadmap

| Version | Scope |
|---|---|
| **v0.2** | polish materials-domain skills; public benchmark table with one-command reproduction; python lint ratchet & backend tests in public CI |
| **v0.3** | `openair` CLI enhancements (initial `up/status/seed/down` already shipped with v0.1.x, see [cli/](cli/)); domestic-accelerator validation (MXMACA / Ascend) |
| **v1.0** | multi-user & production deployment guide |

## 🤝 Contributing

Issues and PRs are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev environment, commit conventions, and PR requirements.

- Never commit real secrets, intranet addresses, or personal data (CI runs a full gitleaks scan)
- New or modified skills must update `skills/registry.yaml` and the matching schemas, and stay replayable

## 📄 License

Released under the [Apache-2.0](LICENSE) license. Third-party components (e.g., externally sourced skills) follow their own notices.
