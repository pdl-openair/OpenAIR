# 安全（security）——三层 secret 防线

> secret 卫生的纵深防御：任何单一环节失守（漏装钩子、绕过钩子、网页直改）都被下一层兜住。

## 职责

规定密钥/凭证/.env 的提交禁令与三层防线部署方式。

## 适用场景

提交任何含凭据可能的文件前；配置本地钩子时；CI secret 扫描策略维护时。

## 必须

- **[SPEC-SEC-01｜L0+L1｜gitleaks]** 禁止提交密钥/凭证/token/私有接口地址。防线部署：pre-commit gitleaks（workspace 范围）→ CI secret-scan job（gitleaks-action，fetch-depth:0 全历史）→ gitignore/hooks 兜底。gitleaks 二进制缺失时本地**降级为警告不阻断提交**（Windows/SAC 环境现实），CI 兜底为权威。
- **[SPEC-SEC-02｜L0+L1｜gitignore + pre-commit]** 禁提交 `.env*`（`.env.example` 除外）：gitignore 既有规则 + pre-commit `detect-private-key` + gitleaks。
- gitleaks 配置（.gitleaks.toml，B1 交付）必须为 fixtures/测试假凭证建 allowlist，避免假 token 噪音淹没真泄露。
- `.claude/` 入仓采用白名单模式（仅 settings.json 与 agents/，见 `.gitignore`）：`settings.local.json` 保持忽略，且 CI 检查禁其被跟踪。

## 禁止

- 禁止把真实 token 写进 fixtures/测试样例"图省事"——用显式假模式（`test-` 前缀、`EXAMPLE` 占位）。
- 禁止为绕过扫描对可疑字符串做编码混淆。
- 禁止在 issue/PR/文档明文粘贴凭据（脱敏后只留后 4 位）。

## 防什么失效模式

- 漏装钩子/绕过钩子/网页直改，任一环节失守 → 三层纵深：任一层独立成立即可拦截。
- 假凭证淹没扫描结果（狼来了效应）→ allowlist 精确登记假模式。
- 个人本地配置（settings.local.json）误入仓 → 白名单模式结构性排除。

## 同步点

- .gitignore 的 .claude 白名单段与 CI 检查同步维护。
- fixtures 新增假凭证模式 → 同 PR 更新 gitleaks allowlist。

## 不负责什么

- 运行时 secret 注入（env 契约、飞书 bot 身份等）→ 各服务部署文档。
- 权限姿态（agent 能做什么）→ [../governance/ai-collaboration.md](../governance/ai-collaboration.md)
