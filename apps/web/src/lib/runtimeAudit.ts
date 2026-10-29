import type { WorkbenchArtifact, WorkbenchCapabilityCall, WorkbenchTaskModel } from "../model/workbenchTask";

export type RuntimeAuditMode = "live" | "replay" | "fixture" | "unknown";

export interface RuntimeAuditField {
  key: string;
  label: string;
  value: string;
  complete: boolean;
}

export interface RuntimeAuditSummary {
  mode: RuntimeAuditMode;
  label: "Live" | "Replay" | "Fixture" | "Unknown";
  color: "success" | "processing" | "warning" | "default" | "error";
  call: WorkbenchCapabilityCall | null;
  complete: boolean;
  warning: string;
  fields: RuntimeAuditField[];
  missingLabels: string[];
}

function nonEmpty(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value == null) return null;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function metadataValue(call: WorkbenchCapabilityCall | null, keys: string[]): string | null {
  const metadata = call?.sourceMetadata ?? {};
  for (const key of keys) {
    const direct = nonEmpty(metadata[key]);
    if (direct) return direct;
  }
  return null;
}

function durationMs(call: WorkbenchCapabilityCall | null): string | null {
  if (!call?.startedAt || !call.endedAt) return null;
  const start = new Date(call.startedAt).getTime();
  const end = new Date(call.endedAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return `${end - start} ms`;
}

export function findRuntimeAuditCall(
  model: WorkbenchTaskModel | undefined,
  artifact?: WorkbenchArtifact | null,
): WorkbenchCapabilityCall | null {
  if (!model) return null;
  if (artifact?.sourceCapabilityCallId) {
    const direct = model.capabilityCalls.find((call) => call.id === artifact.sourceCapabilityCallId);
    if (direct) return direct;
  }
  if (artifact?.id) {
    const byOutput = model.capabilityCalls.find((call) =>
      call.outputArtifactRefs?.includes(artifact.id) || call.artifactRefs?.includes(artifact.id),
    );
    if (byOutput) return byOutput;
  }
  if (artifact?.relatedSkillId) {
    const bySkill = model.capabilityCalls.find((call) => call.name === artifact.relatedSkillId);
    if (bySkill) return bySkill;
  }
  return null;
}

function normalizeRuntimeMode(
  model: WorkbenchTaskModel | undefined,
  artifact: WorkbenchArtifact | null | undefined,
  call: WorkbenchCapabilityCall | null,
): RuntimeAuditMode {
  const rawValues = [
    call?.sourceMode,
    call?.runMode,
    artifact?.sourceMode,
    model?.resultSource,
  ].map((value) => value?.toLowerCase()).filter(Boolean) as string[];

  if (rawValues.some((value) => value === "fixture" || value === "demo")) return "fixture";
  if (rawValues.some((value) => value === "replay" || value === "replay_fallback")) return "replay";
  if (rawValues.some((value) => value === "live")) return "live";

  return "unknown";
}

function field(key: string, label: string, value: unknown, missingText?: string): RuntimeAuditField {
  const normalized = nonEmpty(value);
  return {
    key,
    label,
    value: normalized ?? missingText ?? `${label}未记录`,
    complete: Boolean(normalized),
  };
}

export function buildRuntimeAuditSummary(
  model: WorkbenchTaskModel | undefined,
  artifact?: WorkbenchArtifact | null,
  explicitCall?: WorkbenchCapabilityCall | null,
): RuntimeAuditSummary {
  const call = explicitCall ?? findRuntimeAuditCall(model, artifact);
  const mode = normalizeRuntimeMode(model, artifact, call);
  const artifactRefs = call?.outputArtifactRefs?.length
    ? call.outputArtifactRefs
    : call?.artifactRefs?.length
      ? call.artifactRefs
      : artifact?.id
        ? [artifact.id]
        : [];
  const inputSnapshot = metadataValue(call, ["input_snapshot", "inputSnapshot", "input_sha256", "inputSha256"]);
  const configSnapshot = metadataValue(call, ["config_snapshot", "configSnapshot", "config_sha256", "configSha256"]);
  const skillHash = metadataValue(call, ["skill_hash", "skillHash", "skill_sha", "skillSha"]);
  const degradedReason = call?.partialFailureImpact
    ?? metadataValue(call, ["fallback_reason", "fallbackReason", "degraded_reason", "degradedReason"]);

  const fields: RuntimeAuditField[] = [
    field("source_mode", "运行模式", mode === "unknown" ? null : mode),
    field("run_id", "run_id", call?.runId),
    field("runtime", "runtime", call?.runtime),
    field("provider_adapter", "provider / adapter", [call?.provider, call?.adapter].filter(Boolean).join(" / ")),
    field("model", "model", call?.model),
    field("skill_hash", "skill_hash", skillHash),
    field("input_snapshot", "input_snapshot", inputSnapshot ?? (call?.inputArtifactRefs?.length ? call.inputArtifactRefs.join(", ") : null)),
    field("config_snapshot", "config_snapshot", configSnapshot),
    field("status", "status", call?.status),
    field("duration", "duration", durationMs(call)),
    field("artifact_refs", "artifact_refs", artifactRefs.length ? artifactRefs.join(", ") : null),
    field("fallback_degraded_reason", "fallback/degraded reason", degradedReason ?? (mode === "replay" ? "Replay result" : mode === "fixture" ? "Fixture/demo result" : null)),
  ];

  const requiredForLive = new Set([
    "run_id",
    "runtime",
    "provider_adapter",
    "model",
    "skill_hash",
    "input_snapshot",
    "config_snapshot",
    "status",
    "duration",
    "artifact_refs",
  ]);
  const missingLabels = fields
    .filter((item) => item.key === "source_mode" || requiredForLive.has(item.key))
    .filter((item) => !item.complete)
    .map((item) => item.label);
  const complete = mode === "live" ? missingLabels.length === 0 : mode !== "unknown" && Boolean(call || artifact);
  const label = mode === "live" ? "Live" : mode === "replay" ? "Replay" : mode === "fixture" ? "Fixture" : "Unknown";
  const color = mode === "live"
    ? complete ? "success" : "warning"
    : mode === "replay" || mode === "fixture"
      ? "warning"
      : "default";
  const warning = mode === "live"
    ? complete ? "Live 运行证明字段完整。" : "运行证明不完整，不能当作已验证 Live 结论。"
    : mode === "replay"
      ? "Replay 结果：来自历史回放，不能包装成本次 Live。"
      : mode === "fixture"
        ? "Fixture 结果：用于样例或联调，不能包装成真实运行。"
        : "运行模式未知：缺少可核查运行记录。";

  return { mode, label, color, call, complete, warning, fields, missingLabels };
}
