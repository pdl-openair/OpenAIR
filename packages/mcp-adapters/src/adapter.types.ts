/**
 * MCP 适配层类型占位：未来在此封装工具列表、调用与错误映射。
 * 当前不启动真实 MCP 服务。
 */
export interface McpToolDescriptor {
  name: string;
  description?: string;
}

export interface McpAdapter {
  readonly serverId: string;
  listTools(): Promise<McpToolDescriptor[]>;
}
