# openair-cli

OpenAIR 平台的命令行启动器：一条命令拉起全栈（Docker）并打开工作台。零第三方依赖，只需 Python 3.9+ 与 Docker 24+。

```bash
pipx install openair-cli        # 或 pip install --user openair-cli

openair up                      # 体检 → 克隆/更新仓库 → 生成 .env → 环境配置引导 → 构建启动 → 打开浏览器
openair up --mode replay        # 非交互指定执行模式（零密钥回放，推荐第一次）
openair config                  # 交互式重配 .env：执行模式 / Skill 网关 / 规划器 / MP key
openair status                  # 容器状态 + API 健康 + 工作台地址
openair logs [api|web|db|…]     # 跟踪容器日志（--no-follow 只看尾部，--tail N 控制行数）
openair seed                    # 灌入演示任务数据
openair down [--volumes]        # 停止（--volumes 连数据卷一起清空）
openair doctor                  # 环境体检：Docker / compose / 端口 / 磁盘 / 镜像加速
```

## 首次启动会发生什么

1. **环境体检**：docker / 守护进程 / compose 逐项检查，失败给人话提示（缺什么、装哪、装完跑 `openair doctor` 复查）
2. **环境配置引导**（交互终端）：执行模式二选一——
   - `replay`：零密钥回放，仓库内置 fixtures 跑通全流程（推荐第一次）
   - `live`：真实执行，继续引导填 Skill/Agent 网关（`ANTHROPIC_*`，Anthropic 兼容）与开题规划器（`LLM_PROVIDER_*`，OpenAI 兼容），密钥输入不回显、只写入本地 `.env`
   - 可选项：`MP_API_KEY`（Materials Project 免费申请）。其余可选项见 `.env.example` 分组注释
3. **登录账号**：自动生成随机密码并**仅打印一次**（同时写入 `.env` 的 `OPENAIR_BOOTSTRAP_ADMIN_*`）
4. **实时进度**：Docker 构建/拉取进度逐行透传（`BUILDKIT_PROGRESS=plain`），等待 API 就绪有秒级计时与排障指引；镜像拉取失败自动给出国内镜像加速配置模板

非交互环境（CI/管道）会跳过向导，用 `.env` 现值；之后随时 `openair config` 重配，或直接编辑 `.env`。

## 常用覆盖参数

`--dir <路径>` 指定仓库位置；`--admin-email/--admin-password` 自定管理员；`--no-open` 不弹浏览器；`--no-start` 只做准备不启容器；`--yes` 跳过交互向导。

本地开发安装（仓库根目录）：

```bash
uv tool install --force ./cli
```
