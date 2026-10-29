/**
 * Skill 接入适配：统一「请求 / 响应 / 错误」形状。
 * 不放具体业务 Skill 实现，不写死外部能力名称枚举。
 */
export interface SkillInvocationContext {
  taskId: string;
  /** 调用方提供的任意 JSON 可序列化载荷（占位） */
  payload: Record<string, unknown>;
}

export interface SkillInvocationResult {
  ok: boolean;
  /** 人类可读摘要，供任务记录展示 */
  summary?: string;
  /** 结构化细节键值（占位） */
  details?: Record<string, unknown>;
}

export interface SkillAdapter {
  readonly id: string;
  invoke(ctx: SkillInvocationContext): Promise<SkillInvocationResult>;
}
