import { getSubStageLabels, type UiLanguage } from "@openair/shared";
import type { TaskStage } from "@openair/shared";
import { UNDERLYING_STAGE_ORDER } from "../lib/pagePhases";
import type {
  FixtureArtifactJson,
  FixtureCapabilityCallJson,
  FixtureStageJson,
  FixtureSubStageJson,
  FixtureTaskJson,
} from "./fixtureTypes";

function fallbackEnglishKey(key: string): string {
  return key
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function labelForSubStage(
  subStage: string,
  language: UiLanguage = "zh-CN",
): string {
  const dict = getSubStageLabels(language);
  return (
    dict[subStage] ??
    (language === "en-US" ? fallbackEnglishKey(subStage) : subStage)
  );
}

const SKILL_LABELS_ZH: Record<string, string> = {
  ...getSubStageLabels("zh-CN"),
  workbench_chat: "工作台对话",
  traceable_literature_live_path: "可追溯文献调研",
  ope_meta_execute: "OPE 元执行编排",
  ope_session_execute: "OPE 会话执行",
  experiment_run_monitor: "复现实验运行记录",
  experiment_taskpool: "评测任务队列",
};

const SKILL_LABELS_EN: Record<string, string> = {
  ...getSubStageLabels("en-US"),
  workbench_chat: "Workbench chat",
  traceable_literature_live_path: "Traceable literature research",
  ope_meta_execute: "OPE Meta Execution",
  ope_session_execute: "OPE Session Execution",
  experiment_run_monitor: "Reproduction Run Records",
  experiment_taskpool: "Evaluation Task Queue",
};

export function normalizeSkillKey(skillId: string): string {
  return skillId.trim().replace(/-/g, "_");
}

/** Claude Code skill folder name (kebab-case). */
export function skillTechnicalId(skillId: string): string {
  const trimmed = skillId.trim();
  if (!trimmed || trimmed === "workbench_chat") return trimmed;
  return trimmed.includes("-") ? trimmed : trimmed.replace(/_/g, "-");
}

export function labelForSkill(
  skillId: string,
  language: UiLanguage = "zh-CN",
): string {
  const dict = language === "en-US" ? SKILL_LABELS_EN : SKILL_LABELS_ZH;
  const key = normalizeSkillKey(skillId);
  return dict[key] ?? dict[skillId] ?? labelForSubStage(key, language);
}

/** Converts an existing English presentation label back to its Chinese label. */
export function chineseLabelForSkillPresentation(value: string): string | null {
  const normalized = value.trim();
  const key = Object.entries(SKILL_LABELS_EN).find(
    ([, label]) => label === normalized,
  )?.[0];
  return key ? (SKILL_LABELS_ZH[key] ?? null) : null;
}

export function labelForRuntime(
  runtime: string,
  language: UiLanguage = "zh-CN",
): string {
  const key = runtime.trim().toLowerCase();
  const labels =
    language === "en-US"
      ? {
          huiyan_chat_completions: "HUIYAN",
          specialist_agent_runtime_gateway: "OpenAIR runtime gateway",
          replay: "Historical replay",
          replay_fallback: "Historical replay",
          fixture: "Fixture record",
          python_adapter: "OpenAIR runner",
          claude_code: "Claude Code runtime",
        }
      : {
          huiyan_chat_completions: "HUIYAN",
          specialist_agent_runtime_gateway: "OpenAIR 运行时网关",
          replay: "历史回放",
          replay_fallback: "历史回放",
          fixture: "样例记录",
          python_adapter: "OpenAIR 运行器",
          claude_code: "Claude Code 运行时",
        };
  return (
    labels[key as keyof typeof labels] ??
    (runtime.trim() ||
      (language === "en-US" ? "Unspecified runtime" : "未标注运行时"))
  );
}

export function isUnderlyingStage(s: string): s is TaskStage {
  return (UNDERLYING_STAGE_ORDER as readonly string[]).includes(s);
}

export type ParsedRecordedRun = {
  task: FixtureTaskJson;
  stages: FixtureStageJson[];
  artifacts: FixtureArtifactJson[];
  capabilityCalls: FixtureCapabilityCallJson[];
};

export function parseRecordedRun(
  task: FixtureTaskJson,
  stages: FixtureStageJson[],
  artifacts: FixtureArtifactJson[],
  capabilityCalls: FixtureCapabilityCallJson[],
): ParsedRecordedRun {
  return { task, stages, artifacts, capabilityCalls };
}

export function subStageRowsFromFixture(
  rows: FixtureSubStageJson[],
  stageKey: TaskStage,
  language: UiLanguage = "zh-CN",
) {
  return rows.map((r, idx) => ({
    id: `sub_${stageKey}_${idx}_${r.sub_stage}`,
    stageKey,
    subStage: r.sub_stage,
    label: labelForSubStage(r.sub_stage, language),
    status: r.status,
    artifactRefs: r.artifact_refs ?? [],
    capabilityCallRefs: [],
    summary: undefined,
    startedAt: undefined,
    finishedAt: undefined,
    sequence: idx,
  }));
}
