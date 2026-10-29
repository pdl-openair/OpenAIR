import type { UiLanguage, TaskStage, UserFacingNarrative } from "@openair/shared";
import type { AgentChatStreamEvent, ConfirmationRequest } from "../api/agent";
import { labelForSkill } from "../data/recordedRunAdapter";
import { getCapabilityExecutionSubject } from "./agentPresentation";
import { formatBeijingDateTime } from "./beijingTime";
import { isInternalWorkbenchCall, userVisibleWorkbenchCalls } from "./workbenchUi";
import { artifactHumanGate } from "./workbenchUi";
import type {
  WorkbenchCapabilityCall,
  WorkbenchStageRow,
  WorkbenchSubStageRow,
  WorkbenchTaskModel,
} from "../model/workbenchTask";

export type InteractionRole = "user" | "assistant" | "system";

export interface InteractionMessage {
  id: string;
  role: InteractionRole;
  at: string;
  text: string;
  toolCall?: WorkbenchCapabilityCall;
  artifactRefs?: string[];
  confirmationRequests?: ConfirmationRequest[];
  messageType?: string;
  userFacingNarrative?: UserFacingNarrative | null;
}

export interface PendingConfirmationItem {
  id: string;
  prompt: string;
  relatedSkillId?: string;
}

export interface InteractionPanelModel {
  messages: InteractionMessage[];
  judgments: string[];
  pendingItems: PendingConfirmationItem[];
}

export type InteractionTimelineItem =
  | {
      id: string;
      kind: "message";
      at: string;
      role: InteractionRole;
      text: string;
      messageType?: string;
      runtime?: string;
      toolCall?: WorkbenchCapabilityCall;
      artifactRefs?: string[];
      confirmationRequests?: ConfirmationRequest[];
      progress?: string[];
      substageLabel?: string;
      stageLabel?: string;
    }
  | {
      id: string;
      kind: "tool";
      at: string;
      call: WorkbenchCapabilityCall;
      title: string;
      substageLabel?: string;
      stageLabel?: string;
      progress: string[];
      runtimeLabel: string;
      artifactRefs: string[];
      confirmationRequests: ConfirmationRequest[];
    }
  | {
      id: string;
      kind: "approval";
      at: string;
      confirmation: ConfirmationRequest;
      relatedCall?: WorkbenchCapabilityCall;
    };

export interface L3PlanStep {
  skill_id: string;
  display_name: string;
  purpose: string;
  status: string;
  result?: string | null;
}

export interface L3PlanState {
  status: string;
  steps: L3PlanStep[];
}

export interface ToolUseEvent {
  toolUseId: string;
  toolName: string;
  input: Record<string, unknown>;
  status: "pending" | "done";
  content?: string;
  isError?: boolean;
}

export interface StreamTimelineState {
  call?: WorkbenchCapabilityCall;
  progress: string[];
  substageLabel?: string;
  stageKey?: string;
  startedAt?: string;
  artifactRefs: string[];
  confirmations: ConfirmationRequest[];
  l3Plan?: L3PlanState;
  toolEvents: ToolUseEvent[];
}

function formatTime(iso: string): string {
  try {
    return formatBeijingDateTime(iso);
  } catch {
    return iso;
  }
}

export function stageLabelFromKey(model: WorkbenchTaskModel, stageKey?: TaskStage): string | undefined {
  return model.stages.find((stage) => stage.stage === stageKey)?.pagePhaseLabel;
}

/**
 * Parse an explicit skill_id (kebab-case) from user message text.
 * Recognizes markers like "能力: question-formulation" / "skill：xxx" (half/full-width colon).
 * Returns lowercased kebab id, or null when no marker is present.
 */
export function parseSkillIdFromMessage(text: string): string | null {
  if (!text) return null;
  const match = text.match(/(?:Skill|skill|能力)\s*[:：]\s*([a-zA-Z][a-zA-Z0-9_-]*)/);
  if (!match) return null;
  return match[1].toLowerCase();
}

export function substageLabelFromCall(model: WorkbenchTaskModel, call?: WorkbenchCapabilityCall): string | undefined {
  if (!call?.subStageInstanceId) return undefined;
  for (const stage of model.stages) {
    const hit = stage.subStages.find((sub) => sub.id === call.subStageInstanceId);
    if (hit) return hit.label;
  }
  return undefined;
}

function callsForContext(
  model: WorkbenchTaskModel,
  selectedSub: WorkbenchSubStageRow | null,
): WorkbenchCapabilityCall[] {
  const sorted = userVisibleWorkbenchCalls(model.capabilityCalls).sort((a, b) =>
    (a.startedAt ?? "").localeCompare(b.startedAt ?? ""),
  );
  if (!selectedSub) return sorted;
  const related = sorted.filter((c) => c.subStageInstanceId === selectedSub.id);
  if (related.length > 0) return related;
  const selectedSubArtifactRefs = selectedSub.artifactRefs ?? [];
  return sorted.filter((c) => selectedSubArtifactRefs.length === 0 || c.status === "running").slice(-4);
}

function messageForCall(call: WorkbenchCapabilityCall, language: UiLanguage): string {
  const skill = labelForSkill(call.name, language);
  if (call.status === "running") {
    return language === "en-US"
      ? `Running “${skill}”. The center panel will update with outputs; review and confirm or add instructions when it finishes.`
      : `正在执行「${skill}」。中间区会随研究产出更新；完成后请你确认或补充指令。`;
  }
  if (call.status === "failed") {
    return language === "en-US"
      ? `“${skill}” did not complete. Review the failure reason before retrying or adjusting the approach.`
      : `「${skill}」未完成，请查看失败原因后决定重试或调整方案。`;
  }
  return language === "en-US"
    ? `Completed “${skill}”. Key outputs are available for review in the center panel.`
    : `已完成「${skill}」。关键研究产出已可在中间区查看。`;
}

export function buildTimelineItems(
  model: WorkbenchTaskModel,
  messages: InteractionMessage[],
  language: UiLanguage = "zh-CN",
  streamStateByCallId: Record<string, StreamTimelineState> = {},
): InteractionTimelineItem[] {
  const timeline: InteractionTimelineItem[] = [];
  const pushedToolIds = new Set<string>();

  const sortedMessages = [...messages].sort((a, b) => (a.at ?? "").localeCompare(b.at ?? ""));
  for (const msg of sortedMessages) {
    const shouldRenderMessageRow = !(msg.role === "assistant" && msg.messageType === "tool_call" && msg.toolCall);
    const streamState = msg.toolCall ? streamStateByCallId[msg.toolCall.id] : undefined;
    const embedToolInMessage = Boolean(
      shouldRenderMessageRow && msg.role === "assistant" && msg.toolCall,
    );

    if (shouldRenderMessageRow) {
      timeline.push({
        id: msg.id,
        kind: "message",
        at: msg.at,
        role: msg.role,
        text: msg.text,
        messageType: msg.messageType,
        toolCall: embedToolInMessage ? streamState?.call ?? msg.toolCall : msg.toolCall,
        artifactRefs: msg.artifactRefs ?? [],
        confirmationRequests: msg.confirmationRequests ?? [],
        progress: embedToolInMessage ? streamState?.progress ?? [] : undefined,
        substageLabel: embedToolInMessage
          ? streamState?.substageLabel ?? substageLabelFromCall(model, msg.toolCall)
          : undefined,
        stageLabel: embedToolInMessage ? stageLabelFromKey(model, msg.toolCall?.stageKey) : undefined,
      });
    }

    if (msg.toolCall) {
      if (isInternalWorkbenchCall(msg.toolCall)) {
        continue;
      }
      if (embedToolInMessage) {
        pushedToolIds.add(msg.toolCall.id);
        for (const confirmation of msg.confirmationRequests ?? []) {
          timeline.push({
            id: `approval-${confirmation.id}`,
            kind: "approval",
            at: msg.at,
            confirmation,
            relatedCall: msg.toolCall,
          });
        }
        continue;
      }

      const artifactRefs = Array.from(
        new Set([...(msg.toolCall.outputArtifactRefs ?? []), ...(msg.toolCall.artifactRefs ?? []), ...(streamState?.artifactRefs ?? [])]),
      );
      timeline.push({
        id: `tool-${msg.toolCall.id}`,
        kind: "tool",
        at: msg.toolCall.startedAt ?? msg.at,
        call: streamState?.call ?? msg.toolCall,
        title: labelForSkill(msg.toolCall.name, language),
        substageLabel: streamState?.substageLabel ?? substageLabelFromCall(model, msg.toolCall),
        stageLabel: stageLabelFromKey(model, msg.toolCall.stageKey),
        progress: streamState?.progress ?? [],
        runtimeLabel: getCapabilityExecutionSubject(msg.toolCall),
        artifactRefs,
        confirmationRequests: streamState?.confirmations ?? msg.confirmationRequests ?? [],
      });
      pushedToolIds.add(msg.toolCall.id);
    }

    if (!embedToolInMessage) {
      for (const confirmation of msg.confirmationRequests ?? []) {
        timeline.push({
          id: `approval-${confirmation.id}`,
          kind: "approval",
          at: msg.at,
          confirmation,
          relatedCall: msg.toolCall,
        });
      }
    }
  }

  for (const call of userVisibleWorkbenchCalls(model.capabilityCalls)) {
    if (pushedToolIds.has(call.id)) continue;
    const streamState = streamStateByCallId[call.id];
    timeline.push({
      id: `tool-${call.id}`,
      kind: "tool",
      at: call.startedAt ?? model.updatedAt,
      call: streamState?.call ?? call,
      title: labelForSkill(call.name, language),
      substageLabel: streamState?.substageLabel ?? substageLabelFromCall(model, call),
      stageLabel: stageLabelFromKey(model, call.stageKey),
      progress: streamState?.progress ?? [],
      runtimeLabel: getCapabilityExecutionSubject(call),
      artifactRefs: Array.from(new Set([...(call.outputArtifactRefs ?? []), ...(call.artifactRefs ?? []), ...(streamState?.artifactRefs ?? [])])),
      confirmationRequests: streamState?.confirmations ?? [],
    });
  }

  return timeline.sort((a, b) => (a.at ?? "").localeCompare(b.at ?? ""));
}

export function applyStreamEventToState(
  prev: Record<string, StreamTimelineState>,
  event: AgentChatStreamEvent,
): Record<string, StreamTimelineState> {
  const next = { ...prev };

  const ensureState = (callId: string): StreamTimelineState =>
    next[callId] ?? {
      progress: [],
      artifactRefs: [],
      confirmations: [],
      toolEvents: [],
    };

  if (event.type === "started" || event.type === "capability_call_started") {
    const callId = event.capabilityCallId;
    if (!callId) return next;
    const current = ensureState(callId);
    next[callId] = {
      ...current,
      startedAt: event.requestedAt ?? current.startedAt,
      stageKey: event.stageKey ?? current.stageKey,
      substageLabel: event.substageLabel ?? current.substageLabel,
      call: {
        ...(current.call ?? {
          id: callId,
          name: event.type === "capability_call_started" ? event.skillId : event.skillId ?? "workbench_chat",
          status: "running",
        }),
        id: callId,
        name: event.type === "capability_call_started" ? event.skillId : event.skillId ?? current.call?.name ?? "workbench_chat",
        status: "running",
        runtime: event.runtime ?? current.call?.runtime,
        startedAt: event.requestedAt ?? current.call?.startedAt,
        endedAt: null,
        stageKey: (event.stageKey as TaskStage | undefined) ?? current.call?.stageKey,
        subStageInstanceId: event.subStageInstanceId ?? current.call?.subStageInstanceId,
        artifactRefs: current.call?.artifactRefs ?? [],
        inputArtifactRefs: current.call?.inputArtifactRefs ?? [],
        outputArtifactRefs: current.call?.outputArtifactRefs ?? [],
        resultSummary: current.call?.resultSummary ?? null,
        targetAgent: event.targetAgent ?? current.call?.targetAgent ?? null,
        userFacingNarrative: current.call?.userFacingNarrative ?? null,
      },
    };
    return next;
  }

  if (event.type === "progress" && event.capabilityCallId) {
    const current = ensureState(event.capabilityCallId);
    next[event.capabilityCallId] = {
      ...current,
      progress: [...current.progress, event.text],
    };
    return next;
  }

  if (event.type === "capability_call_updated") {
    const current = ensureState(event.capabilityCallId);
    next[event.capabilityCallId] = {
      ...current,
      artifactRefs: Array.from(new Set([...(current.artifactRefs ?? []), ...(event.outputArtifactRefs ?? []), ...(event.artifactRefs ?? [])])),
      call: {
        ...(current.call ?? { id: event.capabilityCallId, name: "workbench_chat", status: event.status }),
        status: event.status,
        runtime: event.runtime ?? current.call?.runtime,
        endedAt: event.endedAt ?? current.call?.endedAt ?? null,
        stageKey: (event.stageKey as TaskStage | undefined) ?? current.call?.stageKey,
        subStageInstanceId: event.subStageInstanceId ?? current.call?.subStageInstanceId,
        artifactRefs: event.artifactRefs ?? current.call?.artifactRefs ?? [],
        inputArtifactRefs: event.inputArtifactRefs ?? current.call?.inputArtifactRefs ?? [],
        outputArtifactRefs: event.outputArtifactRefs ?? current.call?.outputArtifactRefs ?? [],
        resultSummary: event.resultSummary ?? current.call?.resultSummary ?? null,
        targetAgent: event.targetAgent ?? current.call?.targetAgent ?? null,
        userFacingNarrative: event.userFacingNarrative ?? current.call?.userFacingNarrative ?? null,
      },
    };
    return next;
  }

  if (event.type === "artifact_created" && event.capabilityCallId) {
    const current = ensureState(event.capabilityCallId);
    next[event.capabilityCallId] = {
      ...current,
      artifactRefs: Array.from(new Set([...(current.artifactRefs ?? []), event.artifactId])),
    };
    return next;
  }

  if (event.type === "confirmation_requested" && event.capabilityCallId) {
    const current = ensureState(event.capabilityCallId);
    next[event.capabilityCallId] = {
      ...current,
      confirmations: [...current.confirmations, event.confirmation],
    };
    return next;
  }

  if (event.type === "substage_bound" && event.subStageInstanceId) {
    const match = Object.entries(next).find(([, value]) => value.call?.subStageInstanceId === event.subStageInstanceId);
    if (match) {
      const [callId, current] = match;
      next[callId] = {
        ...current,
        substageLabel: event.substageLabel ?? current.substageLabel,
        stageKey: event.stageKey ?? current.stageKey,
      };
    }
  }

  if (event.type === "l3_plan_created" && event.capabilityCallId) {
    const current = ensureState(event.capabilityCallId);
    next[event.capabilityCallId] = {
      ...current,
      l3Plan: event.plan,
    };
    return next;
  }

  if (event.type === "l3_progress" && event.capabilityCallId) {
    const current = next[event.capabilityCallId] ?? ensureState(event.capabilityCallId);
    const existingPlan = current.l3Plan;
    if (existingPlan) {
      const updatedSteps = existingPlan.steps.map((step) =>
        step.skill_id === event.skillId
          ? { ...step, status: event.status, result: event.result ?? step.result }
          : step
      );
      const allDone = updatedSteps.every(
        (s) => s.status === "completed" || s.status === "failed" || s.status === "skipped"
      );
      next[event.capabilityCallId] = {
        ...current,
        l3Plan: {
          ...existingPlan,
          steps: updatedSteps,
          status: allDone ? "completed" : "executing",
        },
      };
    }
    return next;
  }

  if (event.type === "tool_use" && event.capabilityCallId) {
    const current = ensureState(event.capabilityCallId);
    const existingIds = new Set((current.toolEvents ?? []).map((te) => te.toolUseId));
    if (existingIds.has(event.toolUseId)) return next;
    next[event.capabilityCallId] = {
      ...current,
      toolEvents: [
        ...(current.toolEvents ?? []),
        {
          toolUseId: event.toolUseId,
          toolName: event.toolName,
          input: event.input ?? {},
          status: "pending" as const,
        },
      ],
    };
    return next;
  }

  if (event.type === "tool_result" && event.capabilityCallId) {
    const current = ensureState(event.capabilityCallId);
    const existingEvents = (current.toolEvents ?? []).map((te) =>
      te.toolUseId === event.toolUseId
        ? { ...te, status: "done" as const, content: event.content ?? "", isError: event.isError }
        : te,
    );
    // If no matching tool_use was seen, create a done-only entry
    if (!(current.toolEvents ?? []).some((te) => te.toolUseId === event.toolUseId)) {
      existingEvents.push({
        toolUseId: event.toolUseId,
        toolName: "",
        input: {},
        status: "done" as const,
        content: event.content ?? "",
        isError: event.isError,
      });
    }
    next[event.capabilityCallId] = {
      ...current,
      toolEvents: existingEvents,
    };
    return next;
  }

  return next;
}

/** 由 fixture 能力调用序列 + 当前二级节点推导演示态对话时间线（不修改 fixture） */
export function buildInteractionPanelModel(
  model: WorkbenchTaskModel,
  viewStageRow: WorkbenchStageRow | undefined,
  selectedSub: WorkbenchSubStageRow | null,
  language: UiLanguage = "zh-CN",
): InteractionPanelModel {
  const messages: InteractionMessage[] = [];
  const at0 = model.createdAt;

  // messages.push({
  //   id: "sys-start",
  //   role: "system",
  //   at: at0,
  //   text:
  //     language === "en-US"
  //       ? "The task has started. The agent will proceed through Problem → Method → Experiment → Report."
  //       : "任务已启动，Agent 将按 Problem → Method → Experiment → Report 推进。",
  // });

  messages.push({
    id: "user-goal",
    role: "user",
    at: at0,
    text: model.goalSummary,
  });

  const calls = callsForContext(model, selectedSub);
  for (const call of calls) {
    const at = call.startedAt ?? model.updatedAt;
    messages.push({
      id: `asst-${call.id}`,
      role: "assistant",
      at,
      text: messageForCall(call, language),
      toolCall: call,
      artifactRefs: call.outputArtifactRefs ?? call.artifactRefs ?? [],
      confirmationRequests: [],
      messageType: "tool_call",
    });
  }

  if (selectedSub && messages.length <= 2) {
    messages.push({
      id: `user-sub-${selectedSub.id}`,
      role: "user",
      at: model.updatedAt,
      text:
        language === "en-US"
          ? `Please provide interim conclusions for “${selectedSub.label}” and mark the items that need my confirmation or choice.`
          : `请基于「${selectedSub.label}」给出阶段性结论，并标明需要我确认或选择的项。`,
    });
  }

  const judgments: string[] = [];
  const running = userVisibleWorkbenchCalls(model.capabilityCalls).filter((c) => c.status === "running");
  if (running.length > 0) {
    judgments.push(
      language === "en-US"
        ? `${running.length} research action(s) are still in progress: ${running.map((c) => labelForSkill(c.name, language)).join(", ")}.`
        : `当前有 ${running.length} 个推进事项正在进行：${running.map((c) => labelForSkill(c.name, language)).join("、")}。`,
    );
  }

  const pendingItems: PendingConfirmationItem[] = [];

  if (model.taskStatus === "awaiting_confirmation") {
    pendingItems.push({
      id: "confirm-stage-output",
      prompt:
        language === "en-US"
          ? "Please confirm whether the current stage output meets the exit criteria so the task can continue."
          : `请确认当前阶段产出是否满足出口条件，以便继续推进。`,
    });
  }

  if (viewStageRow?.risks && (model.taskStatus === "running" || model.taskStatus === "awaiting_confirmation")) {
    pendingItems.push({
      id: "confirm-risk",
      prompt:
        language === "en-US"
          ? `Risk: ${viewStageRow.risks} Continue with the current plan?`
          : `风险：${viewStageRow.risks} 是否仍按现方案继续？`,
    });
  }

  for (const call of running.filter((c) => !selectedSub || c.subStageInstanceId === selectedSub.id)) {
    pendingItems.push({
      id: `confirm-${call.id}`,
      prompt:
        language === "en-US"
          ? `“${labelForSkill(call.name, language)}” is still running. Should it continue in the background, or pause for your confirmation first?`
          : `「${labelForSkill(call.name, language)}」仍在运行。是否允许其在后台继续，还是先暂停等待你的确认？`,
      relatedSkillId: call.name,
    });
  }

  const selectedSubArtifacts = selectedSub
    ? model.artifacts.filter((artifact) => (selectedSub.artifactRefs ?? []).includes(artifact.id))
    : [];
  const selectedSubHasConfirmableArtifact = selectedSubArtifacts.some((artifact) =>
    artifactHumanGate(artifact).canConfirmPass,
  );
  if (selectedSub && selectedSub.artifactRefs.length > 0 && selectedSub.status === "completed" && selectedSubHasConfirmableArtifact) {
    pendingItems.push({
      id: `confirm-artifact-${selectedSub.id}`,
      prompt:
        language === "en-US"
          ? `Confirm adoption of the outputs from “${selectedSub.label}” and move to the next step?`
          : `是否确认采用「${selectedSub.label}」的产物并进入后续步骤？`,
      relatedSkillId: selectedSub.subStage,
    });
  }

  return { messages, judgments, pendingItems };
}

export function formatInteractionTime(at: string): string {
  return formatTime(at);
}
