# openair-cli

OpenAIR 平台的命令行启动器：一条命令拉起全栈（Docker）并打开工作台。

```bash
pipx install openair-cli        # 或 pip install --user openair-cli

openair up                      # 克隆/更新仓库 → 生成 .env（安全项随机化+管理员账号）→ 启动 → 打开浏览器
openair up --mode replay        # 零密钥回放演示（推荐第一次使用）
openair status                  # 服务与 API 健康状态
openair seed                    # 灌入演示任务数据
openair down                    # 停止（--volumes 连数据卷一起清空）
```

- 首次 `openair up` 会自动生成随机密码并**仅打印一次**登录凭据（同时写入 `.env` 的 `OPENAIR_BOOTSTRAP_ADMIN_*`）
- 常用覆盖：`--dir <路径>` 指定仓库位置；`--admin-email/--admin-password` 自定管理员；`--no-open` 不弹浏览器；`--no-start` 只做准备不启容器
- 依赖：Python 3.9+、Docker 24+（含 compose v2）。无任何 Python 第三方依赖

本地开发安装（仓库根目录）：

```bash
uv tool install --force ./cli
```
