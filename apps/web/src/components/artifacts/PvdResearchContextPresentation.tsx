import { Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { researchPresentationParameterName } from "@openair/shared";
import { ScientificResultSourceDetails } from "./scientificResultDisplayValue";

// S1 — pvd_research_request_understanding / pvd_experimental_context_structuring
// Renders the structured research context (objectives, equipment, materials,
// rounds, hard constraints, unknowns, parameter scope, run preferences) as a
// readable Chinese view instead of the generic JSON→Markdown projection.
// Numeric values are projected directly from the canonical artifact; no LLM
// rewording. The full raw structure remains available via the collapsible
// source-details section, consistent with other specialized presentations.

type JsonObject = Record<string, unknown>;

const ROUND_STATUS_LABELS: Record<string, string> = {
  recommendation_only: "仅推荐",
  planned: "已规划",
  executed: "已执行",
  measured: "已测量",
  mixed_or_incomplete: "混合或不完整",
  unknown: "未知",
};
const CONSTRAINT_OPERATOR_LABELS: Record<string, string> = {
  min: "不低于",
  max: "不高于",
  range: "范围",
  exact: "等于",
  allowed: "允许",
  forbidden: "禁止",
  other: "约束",
};
const CONSTRAINT_KIND_LABELS: Record<string, string> = {
  equipment: "设备",
  safety: "安全",
  process: "工艺",
  material: "材料",
  resource: "资源",
  other: "其他",
};
const CONTEXT_STATUS_LABELS: Record<string, string> = {
  complete: "完整",
  partial: "部分缺失",
  conflicted: "存在冲突",
};
const FIELD_STATUS_LABELS: Record<string, string> = {
  known: "已知",
  missing: "缺失",
  conflict: "冲突",
};
const VALUE_TYPE_LABELS: Record<string, string> = {
  continuous: "连续",
  integer: "整数",
  categorical: "分类",
  boolean: "布尔",
};
const RUN_PREF_SOURCE_LABELS: Record<string, string> = {
  user_query: "用户指定",
  default: "默认",
};
const LAYER_ROLE_LABELS: Record<string, string> = {
  electrode: "电极",
  buffer: "缓冲层",
  seed: "种子层",
  adhesion: "附着力层",
  capping: "盖层",
  other: "其他",
};

const SYSTEM_IDENTITY_FIELDS: ReadonlyArray<{
  key: string;
  label: string;
}> = [
  { key: "target_material", label: "靶材" },
  { key: "substrate", label: "衬底" },
  { key: "target_substrate_distance_mm", label: "靶基距" },
  { key: "power_supply_temperature", label: "电源温度" },
  { key: "target_angle", label: "靶角度" },
];

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}
function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.map(asObject).filter((row): row is JsonObject => Boolean(row))
    : [];
}
function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
function scalar(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  return null;
}
function knownLabel(
  value: unknown,
  labels: Record<string, string>,
): string | null {
  const key = text(value);
  return key ? (labels[key] ?? null) : null;
}

/** A structured_context lives at the top level of the full S1 output, or is the
 *  top-level object itself when emitted by the experimental-context-structuring
 *  sibling or as a context-rebuild fragment. */
function structuredContext(document: JsonObject): JsonObject | null {
  if (typeof document.rounds !== "undefined" || typeof document.equipment !== "undefined") {
    return document;
  }
  const nested = asObject(document.structured_context);
  return nested;
}

function assertionStatements(value: unknown): string[] {
  return asObjectArray(value)
    .map((row) => text(row.statement))
    .filter(Boolean);
}
function quantityDisplay(value: unknown): string | null {
  const qa = asObject(value);
  if (!qa) return null;
  const q = asObject(qa.quantity);
  const raw = q ? text(q.value_raw) || scalar(q.value_numeric) : null;
  const unit = q ? text(q.unit_raw) : "";
  if (!raw) return null;
  return unit ? `${raw} ${unit}` : raw;
}
function compositionDisplay(value: unknown): string | null {
  const ca = asObject(value);
  if (!ca) return null;
  const raw = text(ca.composition_raw);
  if (raw) return raw;
  const components = asObjectArray(ca.components)
    .map((c) => {
      const species = text(c.species);
      const amount = text(c.amount_raw) || scalar(c.amount_numeric);
      return [species, amount].filter(Boolean).join(" ");
    })
    .filter(Boolean);
  return components.length ? components.join("，") : null;
}

function ParamChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-surface-soft px-3 py-2">
      <div className="text-xs text-text-muted">{label}</div>
      <div className="mt-0.5 text-sm font-medium text-text">{value}</div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="grid gap-2">
      <Typography.Text strong>{title}</Typography.Text>
      <div className="grid gap-2 text-sm text-text-secondary">{children}</div>
    </section>
  );
}

function StringList({ values }: { values: string[] }) {
  if (values.length === 0) return null;
  return (
    <ul className="m-0 grid gap-1.5 pl-5">
      {values.map((value, index) => (
        <li key={`${value}-${index}`}>{value}</li>
      ))}
    </ul>
  );
}

function ExperimentalSystemIdentity({ ctx }: { ctx: JsonObject }) {
  const metadata = asObject(ctx.field_metadata);
  const rows = SYSTEM_IDENTITY_FIELDS.map(({ key, label }) => {
    const value = scalar(ctx[key]);
    const meta = asObject(metadata?.[key]);
    const status = knownLabel(meta?.status, FIELD_STATUS_LABELS);
    return { key, label, value, status };
  });
  const known = rows.filter((row) => row.value);
  const missing = rows.filter((row) => !row.value && row.status);
  return (
    <div className="grid gap-2">
      {known.length > 0 ? (
        <div className="m-0 grid gap-2 sm:grid-cols-2">
          {known.map((row) => (
            <ParamChip key={row.key} label={row.label} value={row.value ?? "—"} />
          ))}
        </div>
      ) : null}
      {missing.length > 0 ? (
        <Typography.Text type="secondary" className="text-sm">
          身份字段缺失/冲突：{missing.map((row) => `${row.label}（${row.status}）`).join("、")}
        </Typography.Text>
      ) : null}
    </div>
  );
}

function EquipmentFacts({ equipment }: { equipment: JsonObject }) {
  const facts: Array<{ label: string; value: string | null }> = [
    {
      label: "沉积方式",
      value: assertionStatements(equipment.deposition_method)[0] ?? null,
    },
    {
      label: "溅射模式",
      value: assertionStatements(equipment.sputtering_mode)[0] ?? null,
    },
    {
      label: "靶基距",
      value: asObjectArray(equipment.target_substrate_distance)
        .map(quantityDisplay)
        .filter(Boolean)
        .join("、"),
    },
    {
      label: "衬底温度",
      value: asObjectArray(equipment.substrate_temperature)
        .map(quantityDisplay)
        .filter(Boolean)
        .join("、"),
    },
  ];
  const gas = asObject(equipment.gas_and_pressure);
  if (gas) {
    facts.push({
      label: "N₂/Ar 比例",
      value: assertionStatements(gas.n2_ar_ratio).join("、"),
    });
    facts.push({
      label: "总流量",
      value: asObjectArray(gas.total_flow)
        .map(quantityDisplay)
        .filter(Boolean)
        .join("、"),
    });
    facts.push({
      label: "腔室压力",
      value: asObjectArray(gas.chamber_pressure)
        .map(quantityDisplay)
        .filter(Boolean)
        .join("、"),
    });
  }
  const core = (equipment.core_parameters as unknown[] | undefined) ?? [];
  const coreText = core
    .filter((p): p is string => typeof p === "string")
    .map((p) => researchPresentationParameterName(p, p))
    .join("、");
  if (coreText) facts.push({ label: "核心可调参数", value: coreText });
  const visible = facts.filter((row) => row.value);
  if (visible.length === 0) return null;
  return (
    <div className="m-0 grid gap-2 sm:grid-cols-2">
      {visible.map((row) => (
        <ParamChip key={row.label} label={row.label} value={row.value ?? ""} />
      ))}
    </div>
  );
}

function MaterialsFacts({ materials }: { materials: JsonObject }) {
  const target = asObject(materials.target);
  const film = asObject(materials.film);
  const targetComp = target
    ? asObjectArray(target.composition)
        .map(compositionDisplay)
        .filter(Boolean)
        .join("；")
    : "";
  const filmDesc = film ? assertionStatements(film.descriptions).join("；") : "";
  const substrateText = assertionStatements(materials.substrate).join("；");
  const layers = asObjectArray(materials.layers);
  const layerText = layers
    .map((layer) => {
      const role = knownLabel(layer.role, LAYER_ROLE_LABELS) ?? text(layer.role);
      const desc = assertionStatements(layer.assertions).join("；");
      return [role, desc].filter(Boolean).join("：");
    })
    .filter(Boolean)
    .join("；");
  const rows = [
    { label: "靶材组成", value: targetComp },
    { label: "薄膜", value: filmDesc },
    { label: "衬底", value: substrateText },
    { label: "膜层结构", value: layerText },
  ].filter((row) => row.value);
  if (rows.length === 0) return null;
  return (
    <div className="m-0 grid gap-2 sm:grid-cols-2">
      {rows.map((row) => (
        <ParamChip key={row.label} label={row.label} value={row.value} />
      ))}
    </div>
  );
}

function RoundCard({ round }: { round: JsonObject }) {
  const status = knownLabel(round.status, ROUND_STATUS_LABELS);
  const roundRef = text(round.round_ref) || text(round.round_id);
  // Each execution phase is a typedParameterValue[] array keyed by parameter_key.
  const phaseParams = (phase: unknown): string[] =>
    asObjectArray(phase)
      .map((item) => {
        const key = text(item.parameter_key);
        if (!key) return "";
        const name = researchPresentationParameterName(key, key);
        const value = scalar(item.value);
        const unit = text(item.unit);
        return value ? `${name}=${value}${unit ? " " + unit : ""}` : "";
      })
      .filter(Boolean);
  const actual = phaseParams(round.actual_execution);
  const recommended = phaseParams(round.recommendation);
  const planned = phaseParams(round.planned_execution);
  const measurements = asObjectArray(round.measurements)
    .map((m) => {
      const metric = text(m.metric);
      const q = quantityDisplay(m) ?? text(m.qualitative_result);
      return metric && q ? `${metric}：${q}` : "";
    })
    .filter(Boolean);
  const hasContent =
    actual.length || recommended.length || planned.length || measurements.length;
  if (!hasContent && !roundRef) return null;
  return (
    <article className="grid gap-2 rounded-lg border border-border bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong>{roundRef || "实验轮次"}</Typography.Text>
        {status ? <Tag className="m-0">{status}</Tag> : null}
      </div>
      {actual.length ? (
        <div className="text-sm">
          <span className="text-text-muted">实际执行：</span>
          {actual.join("，")}
        </div>
      ) : null}
      {planned.length ? (
        <div className="text-sm">
          <span className="text-text-muted">计划执行：</span>
          {planned.join("，")}
        </div>
      ) : null}
      {recommended.length ? (
        <div className="text-sm">
          <span className="text-text-muted">推荐参数：</span>
          {recommended.join("，")}
        </div>
      ) : null}
      {measurements.length ? (
        <div className="text-sm">
          <span className="text-text-muted">测量结果：</span>
          {measurements.join("；")}
        </div>
      ) : null}
      {!hasContent ? (
        <Typography.Text type="secondary" className="text-sm">
          尚未记录实验执行。
        </Typography.Text>
      ) : null}
    </article>
  );
}

function HardConstraints({ constraints }: { constraints: unknown }) {
  const rows = asObjectArray(constraints)
    .map((c) => {
      const field = text(c.field);
      const op = knownLabel(c.operator, CONSTRAINT_OPERATOR_LABELS);
      const kind = knownLabel(c.kind, CONSTRAINT_KIND_LABELS);
      const bound = (key: string) => {
        const q = asObject(c[key]);
        return q ? [text(q.value_raw) || scalar(q.value_numeric), text(q.unit_raw)].filter(Boolean).join(" ") : "";
      };
      const boundText = [bound("lower"), bound("upper"), bound("exact")].filter(Boolean).join("～");
      const raw = text(c.raw_expression);
      const label = field || raw;
      if (!label) return null;
      return { label, op, kind, boundText, raw };
    })
    .filter((row): row is NonNullable<typeof row> => row !== null);
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {rows.map((row, index) => (
        <Tag key={`${row.label}-${index}`} className="m-0">
          {[row.kind, row.label, row.op, row.boundText].filter(Boolean).join(" ")}
        </Tag>
      ))}
    </div>
  );
}

function UnknownsList({ unknowns }: { unknowns: unknown }) {
  const rows: string[] = asObjectArray(unknowns)
    .map((u) => {
      const field = text(u.field);
      const reason = text(u.reason);
      if (!field && !reason) return "";
      return [field, reason].filter(Boolean).join("：");
    })
    .filter((s): s is string => Boolean(s));
  if (rows.length === 0) return null;
  return <StringList values={rows} />;
}

function ParameterScope({ params }: { params: unknown }) {
  const rows = asObjectArray(params)
    .map((spec) => {
      const key = text(spec.parameter_key);
      if (!key) return null;
      const name = researchPresentationParameterName(key, text(spec.display_name) || key);
      const unit = text(spec.unit);
      const allowed = (spec.allowed_values as unknown[] | undefined) ?? [];
      const allowedText = allowed.map((v) => scalar(v) ?? "").filter(Boolean).join("、");
      const valueType = knownLabel(spec.value_type, VALUE_TYPE_LABELS);
      const rationale = text(spec.rationale);
      return { name, unit, allowedText, valueType, rationale };
    })
    .filter((row): row is NonNullable<typeof row> => row !== null);
  if (rows.length === 0) return null;
  return (
    <div className="grid gap-2">
      {rows.map((row, index) => (
        <div
          key={`${row.name}-${index}`}
          className="rounded-md border border-border bg-surface-soft px-3 py-2"
        >
          <div className="flex flex-wrap items-baseline gap-2">
            <Typography.Text strong className="text-sm">
              {row.name}
            </Typography.Text>
            {row.unit ? (
              <span className="text-xs text-text-muted">单位 {row.unit}</span>
            ) : null}
            {row.valueType ? (
              <span className="text-xs text-text-muted">{row.valueType}</span>
            ) : null}
          </div>
          {row.allowedText ? (
            <div className="mt-1 text-sm">候选值：{row.allowedText}</div>
          ) : null}
          {row.rationale ? (
            <div className="mt-1 text-xs text-text-muted">{row.rationale}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

export function PvdResearchContextPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const ctx = structuredContext(document);
  if (!ctx) return null;
  const researchContext = asObject(ctx.research_context);
  const objectives = researchContext
    ? assertionStatements(researchContext.objectives)
    : [];
  const equipment = asObject(ctx.equipment);
  const materials = asObject(ctx.materials);
  const systemCtx = asObject(ctx.experimental_system_context);
  const rounds = asObjectArray(ctx.rounds);
  const hardConstraints = ctx.hard_constraints;
  const unknowns = ctx.unknowns;
  const recommendationParamSet = asObject(document.recommendation_parameter_set);
  const runPreferences = asObject(document.run_preferences);

  const contextStatus = systemCtx
    ? knownLabel(systemCtx.context_status, CONTEXT_STATUS_LABELS)
    : null;

  return (
    <section data-testid="pvd-research-context-view" className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Title level={4} className="!mb-0">
          研究目标与已知条件
        </Typography.Title>
        {contextStatus ? (
          <Tag className="m-0" color={contextStatus === "完整" ? "success" : "warning"}>
            系统身份{contextStatus}
          </Tag>
        ) : null}
      </div>

      {objectives.length > 0 ? (
        <Section title="研究目标">
          <StringList values={objectives} />
        </Section>
      ) : null}

      {systemCtx ? (
        <Section title="实验系统身份">
          <ExperimentalSystemIdentity ctx={systemCtx} />
        </Section>
      ) : null}

      {equipment ? (
        <Section title="设备与工艺条件">
          <EquipmentFacts equipment={equipment} />
        </Section>
      ) : null}

      {materials ? (
        <Section title="材料">
          <MaterialsFacts materials={materials} />
        </Section>
      ) : null}

      {rounds.length > 0 ? (
        <Section title="实验轮次">
          <div className="grid gap-3">
            {rounds.map((round, index) => (
              <RoundCard key={index} round={round} />
            ))}
          </div>
        </Section>
      ) : null}

      {asObjectArray(hardConstraints).length > 0 ? (
        <Section title="硬约束">
          <HardConstraints constraints={hardConstraints} />
        </Section>
      ) : null}

      {asObjectArray(unknowns).length > 0 ? (
        <Section title="信息缺口">
          <UnknownsList unknowns={unknowns} />
        </Section>
      ) : null}

      {recommendationParamSet && Array.isArray(recommendationParamSet.parameters) ? (
        <Section title="可调参数范围">
          <ParameterScope params={recommendationParamSet.parameters} />
        </Section>
      ) : null}

      {runPreferences ? (
        <Section title="本轮实验安排">
          <div className="flex flex-wrap gap-2 text-sm">
            {scalar(runPreferences.initial_experiment_set_count) ? (
              <Tag className="m-0">
                初始实验组数 {scalar(runPreferences.initial_experiment_set_count)}
              </Tag>
            ) : null}
            {knownLabel(runPreferences.source, RUN_PREF_SOURCE_LABELS) ? (
              <Tag className="m-0">
                来源 {knownLabel(runPreferences.source, RUN_PREF_SOURCE_LABELS)}
              </Tag>
            ) : null}
            {runPreferences.capped_to_maximum === true ? (
              <Tag className="m-0">已达上限</Tag>
            ) : null}
          </div>
        </Section>
      ) : null}

      <ScientificResultSourceDetails document={document} />
    </section>
  );
}
