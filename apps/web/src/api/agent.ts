import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";
import type { UserFacingNarrative } from "@openair/shared";

export { getApiBaseUrl };

export type ConfirmationOption = {
  id: string;
  label: string;
  value?: string | null;
  requiresNote?: boolean;
  style?: "primary" | "default" | "danger";
};

export type ConfirmationRequest = {
  id: string;
  title?: string | null;
  prompt: string;
  kind: string;
  status: string;
  blocking?: boolean;
  recommendedOptionId?: string | null;
  options: ConfirmationOption[];
  artifactRefs?: string[];
  relatedSkillId?: string | null;
  relatedCallId?: string | null;
  resolvedAction?: string | null;
  selectedOption?: string | null;
  note?: string | null;
  resolvedAt?: string | null;
  userFacingNarrative?: UserFacingNarrative | null;
};

export type AgentMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  at: string;
  runtime?: string;
  messageType?: string;
  authorLabel?: string | null;
  controllerCommandId?: string | null;
  capabilityCallId?: string | null;
  artifactRefs?: string[];
  confirmationRequests?: ConfirmationRequest[];
  userFacingNarrative?: UserFacingNarrative | null;
};

export type AppliedCollaborationAction = {
  type: string;
  applied?: boolean;
  sessionId?: string;
  reason?: string;
  requestId?: string;
};

export type AgentChatResult = {
  reply: string;
  runtime: string;
  messages: AgentMessage[];
  capabilityCallId?: string;
  createdArtifactIds?: string[];
  targetAgent?: string | null;
  error?: string;
  appliedAction?: AppliedCollaborationAction | null;
};

export type SystemAgentId =
  | "openair_summary_agent"
  | "openair_review_agent"
  | "openair_presentation_guidance_agent";

export type SystemAgentRunInput = {
  agentId: SystemAgentId;
  sourceText?: string;
  artifactId?: string;
  criteria?: string;
  audience?: string;
  outputFormat?: string;
  reviewDecision?: "approved" | "needs_revision";
};

export type SystemAgentRunResult = {
  callId: string;
  status: string;
  summary: string;
  runtime: string;
  artifactIds: string[];
  reviewedArtifactId?: string | null;
  error?: string | null;
};

export type AgentChatStreamEvent =
  | {
      type: "started";
      runtime: string;
      capabilityCallId?: string;
      skillId?: string;
      stageKey?: string;
      substageKey?: string;
      subStageInstanceId?: string;
      substageLabel?: string;
      requestedAt?: string;
      targetAgent?: string | null;
    }
  | {
      type: "capability_call_started";
      capabilityCallId: string;
      skillId: string;
      runtime: string;
      stageKey?: string;
      substageKey?: string;
      subStageInstanceId?: string;
      substageLabel?: string;
      requestedAt?: string;
      targetAgent?: string | null;
    }
  | {
      type: "capability_call_updated";
      capabilityCallId: string;
      status: string;
      runtime?: string;
      resultSummary?: string | null;
      userFacingNarrative?: UserFacingNarrative | null;
      artifactRefs?: string[];
      inputArtifactRefs?: string[];
      outputArtifactRefs?: string[];
      stageKey?: string;
      subStageInstanceId?: string;
      endedAt?: string | null;
      targetAgent?: string | null;
    }
  | {
      type: "substage_bound";
      stageKey?: string;
      substageKey?: string;
      subStageInstanceId?: string;
      substageLabel?: string;
    }
  | {
      type: "artifact_created";
      capabilityCallId?: string;
      artifactId: string;
      title?: string;
      format?: string;
    }
  | {
      type: "confirmation_requested";
      capabilityCallId?: string;
      confirmation: ConfirmationRequest;
    }
  | { type: "assistant_delta"; text: string; capabilityCallId?: string }
  | { type: "progress"; text: string; capabilityCallId?: string }
  | {
      type: "tool_use";
      toolUseId: string;
      toolName: string;
      input: Record<string, unknown>;
      capabilityCallId?: string;
    }
  | {
      type: "tool_result";
      toolUseId: string;
      content: string;
      isError: boolean;
      capabilityCallId?: string;
    }
  | ({
      type: "l3_plan_created";
      capabilityCallId?: string;
      subStageInstanceId?: string;
      plan: {
        status: string;
        steps: Array<{
          skill_id: string;
          display_name: string;
          purpose: string;
          status: string;
          result?: string | null;
        }>;
      };
    })
  | {
      type: "l3_progress";
      capabilityCallId?: string;
      subStageInstanceId?: string;
      skillId: string;
      status: "running" | "completed" | "failed" | "skipped";
      result?: string | null;
    }
  | ({ type: "done" } & AgentChatResult)
  | { type: "error"; error: string };

export type TaskProfileDraft = {
  taskId: string;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
  title: string;
  goalSummary?: string | null;
  domain?: string | null;
  currentStage: string;
  status: string;
};

export type TaskProfileState = {
  needsConfirmation: boolean;
  confirmedAt?: string | null;
  draft: TaskProfileDraft;
};

export async function fetchAgentMessages(taskId: string): Promise<{
  messages: AgentMessage[];
  runtime: string;
  sessionId?: string;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
}> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/agent/messages`, {
    headers: buildAuthHeaders(),
  });
  return parseJson(res);
}

export async function postAgentChat(
  taskId: string,
  message: string,
  skillId?: string,
  substageKey?: string,
  inputPolicy?: "queue" | "merge" | "cancel" | "wait",
): Promise<AgentChatResult> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/agent/chat`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, skillId, substageKey, inputPolicy }),
  });
  return parseJson(res);
}

export async function postAgentChatStream(
  taskId: string,
  message: string,
  skillId: string | undefined,
  onEvent: (event: AgentChatStreamEvent) => void,
  substageKey?: string,
  inputPolicy?: "queue" | "merge" | "cancel" | "wait",
): Promise<AgentChatResult> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/agent/chat/stream`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, skillId, substageKey, inputPolicy }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  if (!res.body) {
    throw new Error("流式响应不可用");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: AgentChatResult | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const event = JSON.parse(trimmed) as AgentChatStreamEvent;
      onEvent(event);
      if (event.type === "error") {
        throw new Error(event.error || "流式调用失败");
      }
      if (event.type === "done") {
        finalResult = chatResultFromDone(event);
      }
    }
  }

  const tail = buffer.trim();
  if (tail) {
    const event = JSON.parse(tail) as AgentChatStreamEvent;
    onEvent(event);
    if (event.type === "error") {
      throw new Error(event.error || "流式调用失败");
    }
    if (event.type === "done") {
      finalResult = chatResultFromDone(event);
    }
  }

  if (!finalResult) {
    throw new Error("流式响应提前结束");
  }
  // Soft runtime failures may still carry a user-visible reply. Only hard-fail
  // when there is nothing to show; callers decide toast vs clear-input.
  if (finalResult.error && !(finalResult.reply || "").trim()) {
    throw new Error(finalResult.error);
  }
  return finalResult;
}

function chatResultFromDone(event: Extract<AgentChatStreamEvent, { type: "done" }>): AgentChatResult {
  return {
    reply: event.reply,
    runtime: event.runtime,
    messages: event.messages,
    capabilityCallId: event.capabilityCallId,
    targetAgent: event.targetAgent,
    error: event.error,
    appliedAction: event.appliedAction,
  };
}

export async function resolveConfirmation(
  taskId: string,
  confirmationId: string,
  body: { action: string; note?: string; selectedOption?: string },
): Promise<ConfirmationRequest> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/agent/confirmations/${encodeURIComponent(confirmationId)}/resolve`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    },
  );
  return parseJson<ConfirmationRequest>(res);
}

export async function postExecuteSkill(taskId: string, skillId?: string): Promise<{
  callId: string;
  status: string;
  summary: string;
  runtime: string;
  error?: string;
}> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/execute`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ skillId }),
  });
  return parseJson(res);
}

export async function postRunSystemAgent(taskId: string, input: SystemAgentRunInput): Promise<SystemAgentRunResult> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/system-agents/run`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  return parseJson<SystemAgentRunResult>(res);
}

export async function fetchTaskProfile(taskId: string): Promise<TaskProfileState> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/profile`, {
    headers: buildAuthHeaders(),
  });
  return parseJson(res);
}

export async function summarizeTaskProfile(taskId: string): Promise<TaskProfileState> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/profile/summary`, {
    method: "POST",
    headers: buildAuthHeaders(),
  });
  return parseJson(res);
}

export async function confirmTaskProfile(
  taskId: string,
  draft: Pick<TaskProfileDraft, "taskId" | "title" | "goalSummary" | "domain">,
): Promise<{ taskId: string }> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/profile/confirm`, {
    method: "PATCH",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(draft),
  });
  return parseJson(res);
}
