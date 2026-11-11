# CI 与发布流水线

> 状态：已确认
> 更新时间：2026-11-09
> 用途：说明公开仓 PR 必过检查、完整测试报告与 tag 发布流程。

## 1. 工作流

| 工作流 | 触发 | 作用 |
| --- | --- | --- |
| `ci` | PR、main push、`v*` tag | 前端生产构建、全历史 gitleaks 扫描 |
| `required-checks` | PR、main push | web、python-gates、unit-test、platform、windows、gate-canary、docker-build |
| `release` | `v*` tag | 多架构镜像、CLI 包、校验和、Release Notes、干净 Ubuntu 冒烟 |

## 2. PR 必过检查

分支保护要求以下 context 全部通过：

- `web`：typecheck、生产构建、增量 eslint、Vitest 全量运行与 known-failures 棘轮
- `python-gates`：增量 ruff、增量 mypy、placement guard、指针完整性、测试资产守卫、commit message、PR 证据、issue 关联、风险路径
- `unit-test`：门禁自测（`scripts/gates/tests`）、测试资产守卫自测（`verification/suites/test_asset_architecture`）、`testkit/tests`，以及单个后端产品测试文件 `services/api/tests/test_quota_service.py`。其余后端套件不在必过范围。
- `platform`：变更文件的 CRLF、大小写冲突、可执行位、bash 语法，以及 compose 配置
- `windows`：CRLF 与盘符路径敏感用例
- `gate-canary`：门禁带病样本必红、干净样本必绿
- `docker-build`：api 与 web 的 compose 镜像构建；worker 镜像由 release 工作流构建
- `web-build`：现有前端构建快检
- `secrets-scan`：gitleaks 全历史扫描

完整后端目录选面由 `unit-test-full-informational` 运行。当前基线仍有历史失败与不可收集文件，该 job 只报告、不阻断。known-failures 基线刷新后再升级为必过。

## 3. 本地复现

```bash
pnpm --filter @openair/web typecheck
pnpm --filter @openair/web build
python3 scripts/gates/diff_gate.py --eslint --base origin/main
python3 scripts/gates/check_platform_consistency.py --base origin/main
python3 scripts/gates/check_pointer_integrity.py --base origin/main
python3 scripts/gates/check_known_failures_ratchet.py --base origin/main
services/api/.venv/bin/python -m pytest scripts/gates/canary -q
```

完整选面：

```bash
PYTHONPATH=services/api services/api/.venv/bin/python scripts/gates/select_unit_tests.py --json > selection.json
PYTHONPATH=services/api services/api/.venv/bin/python scripts/gates/run_unit_suite.py run --selection selection.json
python3 scripts/gates/run_unit_suite.py ratchet
```

## 4. 发布

推送 `v*` tag 后，流水线执行：

1. 构建 `openair-cli` 的 wheel 与 sdist，生成 `SHA256SUMS`。
2. 构建 `linux/amd64` 与 `linux/arm64` 的 api、web、worker 镜像，推送 `ghcr.io/<owner>/openair-*`。
3. 创建 GitHub Release，附 CLI 包、校验和、镜像 digest、`docker-compose.yml` 与 `.env.example`。
   含 `-` 的 tag（如 `v0.3.0-rc.1`）标记为 Pre-release，镜像不写 `latest`；正式 tag 才更新 `latest`。
4. 在干净 Ubuntu runner 安装 CLI wheel，执行 `openair up --mode replay` 并检查 API 与工作台。

发布前按 [阶段发布流程](../../04_开发协作/releases/release_process.md) 整理里程碑与验证记录。

## 5. 当前边界

- 上游仓库分支保护需要管理员权限；fork 侧已配置同样的 required contexts。
- 完整后端单测选面尚未转绿，`unit-test-full-informational` 只提供证据。
- release 冒烟依赖 MinIO 镜像替换先合入，因为默认 compose 需要可拉取的对象存储镜像。
- macOS 暂不作为必过平台，只在后续按需增加。

## 6. 公开仓与私有 overlay 的同步

`oss-ci.yml` 的源头是私有仓 `scripts/oss/overlays/oss-ci.yml`。本 PR 在公开仓侧手改了该文件（action 按 commit SHA 固定、gitleaks 下载增加 SHA-256 校验），而私有仓尚未回灌同名改动。过渡期约定：

- **以公开仓为准**：在私有 overlay 回灌前，`oss-ci.yml` 的公开仓版本是事实源，两侧不一致时不回退公开仓改动。
- **回灌方式**：把本 PR 对 `.github/workflows/oss-ci.yml` 的 diff 人工移植到私有仓 `scripts/oss/overlays/oss-ci.yml`，再跑私有仓导出脚本核对产物与本文件一致（`git diff` 为空）。
- **日常改动**：任何后续修改必须两侧同 PR/同批次落地；只改一侧会在下次导出时产生冲突，导出脚本可能用旧 overlay 覆盖公开仓。
- **恢复自动导出后**：删除本节与 `oss-ci.yml` 头部的临时说明，只保留“源头：私有仓 scripts/oss/overlays，公开仓侧勿手改”标记。
- `required-checks.yml`、`release.yml` 是本仓新增、无私有 overlay 对应物，公开仓即源头。

## 7. 发布演练与合并顺序

目标：合入 PR 本身不产生任何 tag，也不运行 `release`；先确认 main 上的必过检查全绿，再打预发布 tag 演练全链路，最后打正式 tag。

**合并顺序**

1. 先合 PR #4（MinIO 镜像替换）。`release` 的干净 Ubuntu 冒烟会 `openair up` 并拉取对象存储镜像，默认 compose 依赖可拉取的 MinIO；#4 未合入时冒烟大概率红。
2. #4 合入后等 main push 的 `ci` 与 `required-checks` 全绿。
3. 再合 PR #5（本 PR）。`release` 只由 `v*` tag push 触发，merge 不会自动发布；main 上 `required-checks` 必须保持全绿，避免必过门禁在主干失效。
4. 维护者在 main 上确认 head commit 的检查全绿后，打预发布 tag `v0.3.0-rc.1` 并推送。

**tag 预发布全链路核对（`-rc.N`）**

| 产物 | 验收点 |
| --- | --- |
| `cli-package` | artifact `cli-dist` 含 `openair_cli-*.whl`、sdist、`SHA256SUMS`；下载后 `sha256sum -c SHA256SUMS` 通过 |
| `image-openair-api` / `-web` / `-worker` | buildx 推送成功；`docker buildx imagetools inspect ghcr.io/<owner>/openair-api:v0.3.0-rc.1` 显示 `linux/amd64` 与 `linux/arm64`；digest artifact 与 Release 附件一致 |
| `release-assets` | Release 标记为 Pre-release；附件含 CLI 包、`SHA256SUMS`、三份 digest、`docker-compose.yml`、`.env.example`；Notes 自动生成 |
| `clean-ubuntu-smoke` | 干净 runner 安装 wheel 后 `openair up --mode replay` 成功，`/health` 与 `:8080/` 均返回 200 |

**失败处理**：tag 不可移动。任一 job 红都修复后用新的 `-rc.N` 重走；预发布全绿后再打正式 tag `v0.3.0`（正式 tag 才会更新镜像 `latest`）。预发布 tag 的 `latest` 已被跳过，演练不会污染正式镜像别名。
