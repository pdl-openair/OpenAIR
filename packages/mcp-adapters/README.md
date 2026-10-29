# packages/mcp-adapters

**MCP 适配层占位**：`src/adapter.types.ts` 中定义 `McpAdapter` / `McpToolDescriptor`。

- 未来在此编写 MCP 客户端封装与错误映射。  
- **当前不启动**真实 MCP 服务；配置不入仓（见根 `.gitignore`、`.env.example`）。  
- 本包为**类型与适配边界**；不把外部 MCP 清单写死为主线路径，避免与 `skills/` 执行模型混淆。

## 相关文档

- `docs/03_工程/待专项更新/architecture.md`
- `docs/03_工程/待专项更新/CODE_README.md`
