# Task Spec: Provider 协议适配（三协议统一）

上游 issue：pdl-openair/OpenAIR#6。fork 分诊记录：ouyangjiahong26/OpenAIR#3（agent brief 为权威要求）。

说明：本仓库当前没有 `docs/task_specs/TEMPLATE.md`，按 `docs/dev_skills/dev-task-spec/SKILL.md` 约定的结构（背景、目标、非目标、影响范围、验收标准）组织。

## 背景

Provider 层当前固定 OpenAI 兼容 chat completions：

- `schemas/providers/provider-registry.yaml` 只有一个 provider，`adapter: huiyan_chat_completions`；`provider_registry.VALID_ADAPTERS` 只接受这个值。
- `resolve_model_binding` 要求 `OPENAIR_RESEARCH_START_PLANNER_LLM_PROVIDER` 与 adapter 完全相等，选其它协议直接 `ProviderRegistryError`。
- OpenAI Responses 已有分散实现（规划器、结构化 runner、llm_core `post_responses`），但凭证走独立的 `OPENAI_API_KEY` 与 `OPENAI_API_BASE_URL`，这两个变量在 `.env.example`、docker compose、worker 透传三处都没有放行，等于配置空洞。
- durable 编排的 `openai_responses_provider.py` 名含 Responses，实际发 chat completions 请求体。
- Anthropic Messages 协议没有任何传输实现。
- 结构化输出封装只接受 `huiyan_chat_completions` 与 `openai_responses`。

## 目标

1. 协议维度枚举：`openai_chat_completions`、`openai_responses`、`anthropic_messages`。`huiyan_chat_completions` 保留为 chat completions 的兼容别名（全仓 533 处引用不重命名）。
2. provider registry 按 provider 声明 adapter 与 model/baseUrl/apiKey 环境变量映射；校验按协议检查必填项、未知 adapter、协议不匹配（如声称不支持的 submission safety 能力）。
3. 新增唯一的协议适配层 `app/execution/provider_protocol.py`，集中处理：端点路径、认证头、对话请求体构造、响应文本提取、结构化输出封装、finish reason、usage 归一化入口。三条链路（规划器、durable 编排、LLM 结构化 Skill）只调用这一层，不再各自按协议字符串分支。
4. Anthropic Messages 传输进 `packages/llm_core/llm_core/anthropic_client.py`：`/v1/messages`、`x-api-key`、`anthropic-version`、顶层 `system`、必填 `max_tokens`、content blocks 解析、tool use 结构化输出。
5. 移除 `OPENAI_API_KEY`、`OPENAI_API_BASE_URL` 依赖：凭证统一来自 provider registry 的环境映射（默认 provider 仍是 `LLM_PROVIDER_*` 一套，切协议只改 yaml 的 adapter 字段）。
6. 三类协议的结构化输出失败、网络失败、HTTP 4xx/5xx、拒答进入各链路既有的错误分类；usage 输出统一 `{"input_tokens","output_tokens","total_tokens","estimated","raw"}` 形状（`normalize_openai_usage` 已兼容 Anthropic 的 `input_tokens`/`output_tokens`，补测试锁定）。
7. 文档：`.env.example`、README、README.en、docker compose、`/health/agent` 字段给出协议维度信息与三协议配置示例。

## 非目标

- 不改容器内 Claude Agent SDK 的 Anthropic 调用方式（`container_runtime._CLAUDE_ENV_PASSTHROUGH` 不动）。
- 不新增前端模型管理页。
- 不支持 OpenAI 的 files、batch、assistants 等托管 API。
- 不改变默认 provider（`openai_primary`，`huiyan_chat_completions` 别名）与 agent 路由策略。
- 不做 `huiyan_chat_completions` 全量重命名。
- 不为 Anthropic 实现流式（SSE）与 thinking 预算配置，保留后续透传位置。
- durable 编排对 Responses 原生 `json_schema` 尝试失败时回退 json_object + prompt 内 schema（与 chat 行为对齐），不因 schema 严格性拒收冻结契约。

## 设计决策

- 协议常量与别名解析放 `provider_registry.py`（modules 层，无 execution 依赖）；`app/execution/provider_protocol.py` 与 `structured_output_provider_adapter.py` 引用它，避免循环导入。llm_core 不依赖 app。
- Anthropic 结构化输出：`tools: [{name, description, input_schema}]` + `tool_choice: {type: "tool", name}`；schema 复用 Responses 的投影与无损降级管线，跳过 OpenAI 专属的 strict 断言；解析取 `tool_use.input`。
- 规划器流式 thinking writer 仅对 chat 协议启用（现状即如此，显式化为 `protocol_supports_stream`）。
- registry 探活（`discover_provider_models`、`_preflight_model`）按协议取端点、认证头与最小请求体。
- 协议选择器与 registry adapter 必须一致：`provider_protocol.reconcile_provider_protocol` 在发出任何 Provider I/O 前比较两者归一后的协议，不一致即失败。校验放在执行路径而非仅启动时的 `resolve_model_binding`，因此 `validate=False` 绑定或直接读环境变量的链路（`llm_structured_runner`、`research_design_agent_revision._agent_revision_provider`）无法绕过；registry 解析不出凭证的旧式回退路径上，选择器仍是唯一权威。
- reasoning effort 只发给推理模型（`_is_reasoning_model` 门控，与 `_planner_reasoning_effort` 一致）：非推理模型收到 `reasoning`/`reasoning_effort` 会 4xx，因此改走 temperature；Responses 请求体里 effort 与 temperature 互斥（设 effort 即丢弃 temperature）。
- Anthropic `cache_creation_input_tokens` / `cache_read_input_tokens` 只保留在用量原始字段，不计入计费 token（与全仓既有约定一致），本地用量会略低于供应商账单；README 已注明。

## 影响范围

- `packages/llm_core/llm_core/anthropic_client.py`（新增）
- `services/api/app/modules/provider_registry.py`、`schemas/providers/provider-registry.yaml`（不改正文，仅 schema 放宽）、`schemas/providers/provider-registry.schema.json`
- `services/api/app/execution/provider_protocol.py`（新增）
- `services/api/app/execution/structured_output_provider_adapter.py`
- `services/api/app/execution/llm_structured_runner.py`
- `services/api/app/execution/openai_responses_provider.py`、`durable_skill_runtime.py`、`durable_llm_execution_policy.py`、`research_dispatch_huiyan_provider.py`、`research_dispatch_provider.py`
- `services/api/app/execution/research_start_provider_adapter.py`
- `services/api/app/modules/research_start_planner.py`、`research_design_agent_revision.py`、`research_design_freeform_semantic_provider.py`、`research_design_workflow_semantic_provider.py`、`provider_transport_evidence.py`
- `services/api/app/routes/health.py`、`.env.example`、`docker-compose.yml`、`README.md`、`README.en.md`

## 验收标准

与 fork issue 3 的 agent brief 验收标准一致，测试接口约定如下：

- `llm_core.anthropic_client`：URL 解析、认证头、文本/tool_use 提取、usage（mock httpx）。
- `app.execution.provider_protocol`：别名归一、三协议端点/头/请求体/提取，参数化契约测试。
- `app.modules.provider_registry`：adapter 枚举、按 provider 环境校验、`resolve_model_binding` 接受协议名与别名、探活按协议。
- `structured_output_provider_adapter`：anthropic 工具信封与降级管线。
- 链路级：`llm_structured_runner`、durable provider、规划器 prepared attempt 在三种协议下的请求体与解析（mock transport）。
