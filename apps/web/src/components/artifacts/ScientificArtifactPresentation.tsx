import { researchPresentationScalarText } from "@openair/shared";
import { Table, Tag, Typography } from "antd";
import { researchArtifactTitle, researchArtifactProducerIdentity } from "../../lib/researchArtifactTitle";
import type { TableProps } from "antd";
import type { ReactNode } from "react";
import type {
  WorkbenchArtifact,
  WorkbenchTaskModel,
} from "../../model/workbenchTask";
import { OpenTrajectoryFinalSynthesisPresentation } from "./OpenTrajectoryFinalSynthesisPresentation";
import {
  OpenTrajectoryResearchPlanningPresentation,
  type OpenTrajectoryResearchPlanningKind,
} from "./OpenTrajectoryResearchPlanningPresentation";
import { OpenTrajectoryResearchRecordPresentation } from "./OpenTrajectoryResearchRecordPresentation";
import { PvdResearchRecordPresentation } from "./PvdResearchRecordPresentation";
import { PvdResearchContextPresentation } from "./PvdResearchContextPresentation";
import {
  PvdEvidenceSchemaPresentation,
  PvdRetrievalDesignPresentation,
} from "./PvdEvidenceDesignPresentation";
import { PvdEvidenceApplicabilityPresentation } from "./PvdEvidenceApplicabilityPresentation";
import {
  EvidenceGraph,
  isEvidenceAssessmentArtifact,
  resolveEvidenceGraphResearchRecordsArtifacts,
} from "./evidenceGraph";
import {
  WorkflowNodeResultPresentation,
  workflowNodeResultPresentationKind,
  type WorkflowNodeResultPresentationKind,
} from "./WorkflowNodeResultPresentation";

type JsonObject = Record<string, unknown>;
export type ScientificArtifactPresentationKind =
  | OpenTrajectoryResearchPlanningKind
  | "literature"
  | "evidence"
  | "mechanism"
  | "evidence_graph"
  | "final_synthesis"
  | "pvd_research_records"
  | "pvd_research_context"
  | "pvd_evidence_schema"
  | "pvd_retrieval_design"
  | "pvd_evidence_applicability"
  | WorkflowNodeResultPresentationKind;

const LITERATURE_SKILLS = new Set([
  "academic_source_retrieval_skill",
  "candidate_dedup_and_provenance",
  "title_abstract_screening",
  "title_abstract_screening_skill",
  "candidate_source_scoring",
  "candidate_tiering",
  "topk_source_selection",
]);
const EVIDENCE_SKILLS = new Set([
  "evidence_node_extraction",
  "evidence_node_extraction_skill",
]);
const MECHANISM_SKILLS = new Set([
  "mechanism_chain_synthesis",
  "mechanism_chain_synthesis_skill",
]);
const FINAL_SYNTHESIS_SKILLS = new Set([
  "research_lead_hypothesis_generation",
  "research_lead_hypothesis_generation_skill",
]);

const LITERATURE_ARTIFACT_TYPES = new Set([
  "metadata_candidates",
  "deduped_candidates",
  "screening_results",
  "candidate_scores",
  "source_tiers",
  "topk_selection",
]);
const EVIDENCE_ARTIFACT_TYPES = new Set([
  "research_records",
  "evidence_nodes",
  "evidence_node_packets",
  "evidence_extraction",
]);
const MECHANISM_ARTIFACT_TYPES = new Set([
  "mechanism_graph",
  "mechanism_chains",
  "mechanism_packets",
]);
const EVIDENCE_GRAPH_ARTIFACT_TYPES = new Set([
  "evidence_assessment_and_research_synthesis",
]);
const FINAL_SYNTHESIS_ARTIFACT_TYPES = new Set(["final_synthesis"]);
const PROBLEM_FRAMING_ARTIFACT_TYPES = new Set([
  "opentrajectory.problem_framing",
  "problem_framing",
]);
const RETRIEVAL_STRATEGY_ARTIFACT_TYPES = new Set([
  "opentrajectory.retrieval_strategy_design",
  "retrieval_strategy_design",
]);

// PVD S1/S2/S4 producer skills whose structured outputs get a specialized
// Chinese presentation instead of the generic JSON→Markdown projection.
const PVD_CONTEXT_SKILLS = new Set([
  "pvd_research_request_understanding",
  "pvd_experimental_context_structuring",
]);
const PVD_EVIDENCE_APPLICABILITY_SKILLS = new Set([
  "pvd_evidence_applicability_comparability_analysis",
  "pvd_evidence_applicability_final_synthesis",
]);
const PVD_EVIDENCE_APPLICABILITY_ARTIFACT_TYPES = new Set([
  "evidence_applicability",
]);
const PVD_CONTEXT_ARTIFACT_TYPES = new Set([
  "structured_context",
  "recommendation_parameter_set",
  "run_preferences",
]);

export function scientificArtifactUserTitle(
  artifact: Pick<WorkbenchArtifact, "artifactType" | "type"> & {
    title?: string | null;
  },
): string {
  return researchArtifactTitle(artifact);
}

function normalizedIdentity(value?: string | null): string {
  return (value ?? "").trim().toLowerCase().replace(/-/g, "_");
}

function asObject(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.map(asObject).filter((item): item is JsonObject => Boolean(item))
    : [];
}

function parseObject(content: string): JsonObject | null {
  try {
    return asObject(JSON.parse(content));
  } catch {
    return null;
  }
}

function producerSkillId(
  artifact: WorkbenchArtifact,
  model?: WorkbenchTaskModel,
): string {
  const frozenProducer = researchArtifactProducerIdentity(artifact);
  if (frozenProducer) return normalizedIdentity(frozenProducer);
  const sourceCall = artifact.sourceCapabilityCallId
    ? model?.capabilityCalls.find(
        (call) => call.id === artifact.sourceCapabilityCallId,
      )
    : model?.capabilityCalls.find(
        (call) =>
          call.outputArtifactRefs?.includes(artifact.id) ||
          call.artifactRefs?.includes(artifact.id),
      );
  return normalizedIdentity(sourceCall?.name);
}

export function classifyScientificArtifactPresentation(
  artifact: WorkbenchArtifact,
  _document: JsonObject,
  model?: WorkbenchTaskModel,
): ScientificArtifactPresentationKind | null {
  const nodeKind = workflowNodeResultPresentationKind(artifact);
  if (nodeKind) return nodeKind;

  const artifactType = normalizedIdentity(
    artifact.artifactType ?? artifact.type,
  );
  const skillId = producerSkillId(artifact, model);

  // S4 — PVD evidence applicability. Match before the generic evidence_graph
  // path: evidence_graph targets the OT assessment-and-research-synthesis
  // artifact, not the PVD comparability-analysis authorization artifact.
  if (
    PVD_EVIDENCE_APPLICABILITY_ARTIFACT_TYPES.has(artifactType) ||
    PVD_EVIDENCE_APPLICABILITY_SKILLS.has(skillId)
  ) {
    // Only treat as PVD applicability when the document actually carries the
    // S4 shape; otherwise fall through to let evidence_graph or generic handle it.
    if (
      Array.isArray(_document.support_conclusions) ||
      Array.isArray(_document.evidence_assessments) ||
      typeof _document.summary === "string"
    ) {
      return "pvd_evidence_applicability";
    }
  }

  if (
    artifactType === "pvd.research_records" ||
    ([
      "pvd_literature_evidence_acquisition_closure",
      "pvd_research_record_structuring",
    ].includes(skillId) &&
      (Array.isArray(_document.research_records) ||
        Array.isArray(_document.evidence_records)))
  )
    return "pvd_research_records";

  // S1 — PVD research context (structured_context / recommendation_parameter_set /
  // run_preferences / experimental-context-structuring output).
  if (PVD_CONTEXT_SKILLS.has(skillId)) {
    if (
      PVD_CONTEXT_ARTIFACT_TYPES.has(artifactType) ||
      typeof _document.equipment !== "undefined" ||
      typeof _document.rounds !== "undefined" ||
      typeof _document.structured_context !== "undefined"
    ) {
      return "pvd_research_context";
    }
  }

  // S2 — PVD evidence schema / retrieval design. These may arrive as the
  // planned_output envelope or as the bare skill output. Match by document
  // shape so the same envelope (pvd.research_retrieval_experiment_design.
  // planned_output) routes to the right specialized view by its carried key.
  if (skillId === "pvd_evidence_schema_design" || artifactType === "evidence_schema") {
    if (asObject(_document.evidence_schema)) return "pvd_evidence_schema";
  }
  if (
    skillId === "pvd_evidence_retrieval_design" ||
    artifactType === "retrieval_design"
  ) {
    if (asObject(_document.retrieval_design)) return "pvd_retrieval_design";
  }

  if (PROBLEM_FRAMING_ARTIFACT_TYPES.has(artifactType))
    return "problem_framing";
  if (RETRIEVAL_STRATEGY_ARTIFACT_TYPES.has(artifactType))
    return "retrieval_strategy_design";
  if (
    EVIDENCE_GRAPH_ARTIFACT_TYPES.has(artifactType) ||
    isEvidenceAssessmentArtifact(artifact, model)
  ) {
    return "evidence_graph";
  }
  if (FINAL_SYNTHESIS_ARTIFACT_TYPES.has(artifactType))
    return "final_synthesis";
  if (EVIDENCE_ARTIFACT_TYPES.has(artifactType)) return "evidence";
  if (MECHANISM_ARTIFACT_TYPES.has(artifactType)) return "mechanism";
  if (LITERATURE_ARTIFACT_TYPES.has(artifactType)) return "literature";

  if (FINAL_SYNTHESIS_SKILLS.has(skillId)) return "final_synthesis";
  if (EVIDENCE_SKILLS.has(skillId)) return "evidence";
  if (MECHANISM_SKILLS.has(skillId)) return "mechanism";
  if (LITERATURE_SKILLS.has(skillId)) return "literature";

  return null;
}

export function scientificArtifactPresentationKind(
  artifact: WorkbenchArtifact,
  model?: WorkbenchTaskModel,
): ScientificArtifactPresentationKind | null {
  if (!artifact.content || artifact.format !== "json") return null;
  const document = parseObject(artifact.content);
  return document
    ? classifyScientificArtifactPresentation(artifact, document, model)
    : null;
}

function scalar(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return "";
}

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readableEnum(value: unknown): string {
  const raw = scalar(value);
  return researchPresentationScalarText(raw);
}

function nestedScalar(object: JsonObject | null, key: string): string {
  return object ? scalar(object[key]) : "";
}

function identifierFor(row: JsonObject): string {
  const external = asObject(row.external_ids);
  const doi = scalar(row.doi) || nestedScalar(external, "doi");
  if (doi) return `DOI ${doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")}`;
  const arxiv = scalar(row.arxiv_id) || nestedScalar(external, "arxiv_id");
  return arxiv ? `arXiv ${arxiv}` : "";
}

function literatureRows(document: JsonObject): JsonObject[] {
  for (const key of [
    "metadata_candidates",
    "deduped_candidates",
    "screened_candidates",
    "candidate_scores",
    "source_tiers",
  ]) {
    const rows = asObjectArray(document[key]);
    if (rows.length > 0) return rows;
  }
  const topk = asObject(document.topk_selection);
  if (!topk) return [];
  const groups: Array<[string, string]> = [
    ["selected_for_p03", "Top-K"],
    ["extended_selected", "扩展入选"],
    ["supporting_papers", "机制支持"],
    ["method_inspiration_papers", "方法启发"],
    ["excluded_examples", "排除"],
  ];
  const seen = new Set<string>();
  const rows: JsonObject[] = [];
  for (const [key, groupLabel] of groups) {
    for (const row of asObjectArray(topk[key])) {
      const identity =
        scalar(row.dedupe_key) || scalar(row.candidate_id) || scalar(row.title);
      if (identity && seen.has(identity)) continue;
      if (identity) seen.add(identity);
      rows.push({ ...row, __selection_group: groupLabel });
    }
  }
  return rows;
}

function decisionText(row: JsonObject): string {
  const selectionGroup = scalar(row.__selection_group);
  const tier = readableEnum(row.tier);
  const screeningDecision = readableEnum(row.screening_decision);
  return [selectionGroup, tier || screeningDecision]
    .filter(Boolean)
    .join(" · ");
}

function reasonText(row: JsonObject): string {
  return (
    scalar(row.selection_reason) ||
    scalar(row.screening_reason) ||
    scalar(row.exclusion_reason) ||
    scalar(row.downrank_reason)
  );
}

function parseJsonValue(content: string): unknown | null {
  try {
    return JSON.parse(content) as unknown;
  } catch {
    return null;
  }
}

function EvidenceGraphPresentation({
  artifact,
  document,
  model,
}: {
  artifact: WorkbenchArtifact;
  document: JsonObject;
  model?: WorkbenchTaskModel;
}) {
  const researchRecordsArtifacts = resolveEvidenceGraphResearchRecordsArtifacts(
    artifact,
    model,
  );
  if (researchRecordsArtifacts.length === 0) {
    return (
      <ScientificEmpty label="当前无法确认本次证据评估对应的正式研究记录输入，已停止绘制关系图。" />
    );
  }
  if (researchRecordsArtifacts.some((source) => !source.content)) {
    return <ScientificEmpty label="正在读取本次证据评估使用的来源研究记录…" />;
  }
  const documents = researchRecordsArtifacts.map((source) =>
    parseJsonValue(source.displayProjection?.content ?? source.content!),
  );
  if (documents.some((source) => source === null)) {
    return (
      <ScientificEmpty label="本次证据评估对应的研究记录正文无法解析，已停止绘制关系图。" />
    );
  }
  const researchRecords = documents.flatMap((source) =>
    Array.isArray(source)
      ? source
      : Array.isArray((source as JsonObject)?.research_records)
        ? ((source as JsonObject).research_records as unknown[])
        : [],
  );
  return (
    <>
      {researchRecordsArtifacts.some((source) => source.displayProjection) && (
        <div style={{ color: "#64748b", marginBottom: 8 }}>
          包含历史草稿整理的研究记录；尚未关联判断的记录独立展示。
        </div>
      )}
      <EvidenceGraph assessment={document} researchRecords={researchRecords} />
    </>
  );
}

function ScientificEmpty({ label }: { label: string }) {
  return (
    <Typography.Text type="secondary" data-testid="ot-scientific-empty">
      {label}
    </Typography.Text>
  );
}

function LiteratureTable({ document }: { document: JsonObject }) {
  const rows: JsonObject[] = literatureRows(document).map(
    (row, index) => ({ ...row, __key: String(index) }) as JsonObject,
  );
  if (rows.length === 0)
    return <ScientificEmpty label="当前文献结构化产物暂无可呈现的候选记录。" />;
  const hasYear = rows.some((row) => numeric(row.year) !== null);
  const hasSource = rows.some(
    (row) => scalar(row.source_name) || scalar(row.venue),
  );
  const hasScore = rows.some((row) => numeric(row.final_score) !== null);
  const hasDecision = rows.some((row) => decisionText(row));
  const hasAccess = rows.some((row) => scalar(row.access_status));
  const hasIdentifier = rows.some((row) => identifierFor(row));
  const hasReason = rows.some((row) => reasonText(row));

  const columns: NonNullable<TableProps<JsonObject>["columns"]> = [
    {
      title: "文献题名",
      dataIndex: "title",
      key: "title",
      width: 270,
      render: (value: unknown) => (
        <Typography.Text strong title={scalar(value)}>
          {scalar(value) || "—"}
        </Typography.Text>
      ),
    },
    ...(hasYear
      ? [
          {
            title: "年份",
            key: "year",
            width: 72,
            render: (_: unknown, row: JsonObject) => scalar(row.year) || "—",
          },
        ]
      : []),
    ...(hasSource
      ? [
          {
            title: "来源",
            key: "source",
            width: 130,
            ellipsis: true,
            render: (_: unknown, row: JsonObject) =>
              scalar(row.source_name) || scalar(row.venue) || "—",
          },
        ]
      : []),
    ...(hasScore
      ? [
          {
            title: "评分",
            key: "score",
            width: 82,
            render: (_: unknown, row: JsonObject) => {
              const score = numeric(row.final_score);
              return score === null
                ? "—"
                : score.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
            },
          },
        ]
      : []),
    ...(hasDecision
      ? [
          {
            title: "分层与筛选判断",
            key: "decision",
            width: 128,
            render: (_: unknown, row: JsonObject) => {
              const text = decisionText(row);
              return text ? <Tag className="m-0">{text}</Tag> : "—";
            },
          },
        ]
      : []),
    ...(hasAccess
      ? [
          {
            title: "原文可获取性",
            key: "access",
            width: 130,
            ellipsis: true,
            render: (_: unknown, row: JsonObject) =>
              readableEnum(row.access_status) || "—",
          },
        ]
      : []),
    ...(hasIdentifier
      ? [
          {
            title: "文献标识",
            key: "identifier",
            width: 165,
            ellipsis: true,
            render: (_: unknown, row: JsonObject) => identifierFor(row) || "—",
          },
        ]
      : []),
    ...(hasReason
      ? [
          {
            title: "入选依据",
            key: "reason",
            width: 240,
            ellipsis: true,
            render: (_: unknown, row: JsonObject) => {
              const text = reasonText(row);
              return (
                <Typography.Text title={text}>{text || "—"}</Typography.Text>
              );
            },
          },
        ]
      : []),
  ];

  return (
    <section data-testid="ot-literature-table" className="min-w-0">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <Typography.Title level={4} className="!mb-0">
          文献候选与筛选结果
        </Typography.Title>
        <Typography.Text type="secondary">{rows.length} 条</Typography.Text>
      </div>
      <Table
        size="small"
        rowKey="__key"
        tableLayout="fixed"
        columns={columns}
        dataSource={rows}
        pagination={
          rows.length > 12
            ? { pageSize: 12, showSizeChanger: false, hideOnSinglePage: true }
            : false
        }
      />
    </section>
  );
}

function conditionSummary(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .map(asObject)
    .filter((item): item is JsonObject => Boolean(item))
    .map((item) => {
      const name = scalar(item.name);
      const valueText = scalar(item.value_text);
      const unit = scalar(item.unit);
      const measurement = [valueText, unit].filter(Boolean).join(" ");
      return [name, measurement].filter(Boolean).join("：");
    })
    .filter(Boolean)
    .join("；");
}

function metricSummary(value: unknown): string {
  const metric = asObject(value);
  if (!metric) return "";
  const name = scalar(metric.name);
  const valueText = scalar(metric.value_text) || scalar(metric.value);
  const unit = scalar(metric.unit);
  return [name, [valueText, unit].filter(Boolean).join(" ")]
    .filter(Boolean)
    .join("：");
}

function confidenceLabel(value: unknown): string {
  const score = numeric(value);
  return score === null ? "" : `置信度 ${(score * 100).toFixed(0)}%`;
}

function EvidenceCards({ document }: { document: JsonObject }) {
  if (asObjectArray(document.research_records).length > 0) {
    return <OpenTrajectoryResearchRecordPresentation document={document} />;
  }
  const rows = asObjectArray(document.evidence_items);
  if (rows.length === 0)
    return (
      <ScientificEmpty label="当前证据结构化产物暂无可呈现的 EvidenceNode。" />
    );
  return (
    <section data-testid="ot-evidence-view" className="min-w-0">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <Typography.Title level={4} className="!mb-0">
          科研证据
        </Typography.Title>
        <Typography.Text type="secondary">{rows.length} 条证据</Typography.Text>
      </div>
      <div className="grid gap-3">
        {rows.map((row, index) => {
          const claim = scalar(row.claim);
          const quote = scalar(row.evidence_quote);
          const source = [
            scalar(row.arxiv_id) ? `arXiv ${scalar(row.arxiv_id)}` : "",
            scalar(row.section_name),
          ]
            .filter(Boolean)
            .join(" · ");
          const condition = conditionSummary(row.conditions);
          const metric = metricSummary(row.metric);
          const evidenceType = readableEnum(row.evidence_type);
          const targetMatch = readableEnum(row.target_match_status);
          const grounding =
            readableEnum(row.evidence_support_tier) ||
            readableEnum(row.support_tier);
          const confidence = confidenceLabel(row.confidence);
          return (
            <article
              key={`${claim}-${index}`}
              className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
            >
              <Typography.Text strong className="block text-[15px] leading-6">
                {claim || "未命名证据"}
              </Typography.Text>
              {quote ? (
                <Typography.Paragraph className="!mb-2 !mt-2 border-l-2 border-slate-200 pl-3 text-slate-600">
                  {quote}
                </Typography.Paragraph>
              ) : null}
              <div className="flex flex-wrap gap-1.5">
                {source ? <Tag>{source}</Tag> : null}
                {condition ? <Tag>条件：{condition}</Tag> : null}
                {metric ? <Tag>指标：{metric}</Tag> : null}
                {evidenceType ? <Tag>{evidenceType}</Tag> : null}
                {targetMatch ? <Tag>目标匹配：{targetMatch}</Tag> : null}
                {grounding ? <Tag>{grounding}</Tag> : null}
                {confidence ? <Tag>{confidence}</Tag> : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function nodeLabel(value: unknown): string {
  return scalar(asObject(value)?.label) || "未命名节点";
}

function relationLabel(value: unknown): string {
  const relation = asObject(value);
  return scalar(relation?.description) || scalar(relation?.predicate) || "关联";
}

function MechanismGraph({ document }: { document: JsonObject }) {
  const edges = asObjectArray(document.mechanism_edges);
  const chains = asObjectArray(document.mechanism_chains);
  if (edges.length === 0 && chains.length === 0)
    return <ScientificEmpty label="当前机制结构化产物暂无可呈现的关系。" />;
  return (
    <section data-testid="ot-mechanism-graph" className="min-w-0">
      <Typography.Title level={4} className="!mb-3">
        机制关系图
      </Typography.Title>
      {chains.length > 0 ? (
        <div className="mb-4 grid gap-2">
          {chains.slice(0, 6).map((chain, index) => (
            <div
              key={`${scalar(chain.title)}-${index}`}
              className="rounded-md bg-slate-50 px-3 py-2"
            >
              <Typography.Text strong>
                {scalar(chain.title) || `机制链 ${index + 1}`}
              </Typography.Text>
              {scalar(chain.chain_summary) ? (
                <Typography.Paragraph className="!mb-0 !mt-1 text-slate-600">
                  {scalar(chain.chain_summary)}
                </Typography.Paragraph>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      <div className="grid gap-3">
        {edges.map((edge, index) => {
          const source = nodeLabel(edge.source_node);
          const target = nodeLabel(edge.target_node);
          const relation = relationLabel(edge.relation);
          const rationale = scalar(edge.rationale);
          const evidenceCount = Array.isArray(edge.evidence_ids)
            ? edge.evidence_ids.length
            : 0;
          return (
            <article
              key={`${source}-${target}-${index}`}
              className="rounded-lg border border-slate-200 p-3"
            >
              <div className="grid items-stretch gap-2 md:grid-cols-[minmax(0,1fr)_minmax(150px,0.8fr)_minmax(0,1fr)]">
                <div className="flex min-h-16 items-center justify-center rounded-md bg-slate-50 px-3 py-2 text-center font-medium">
                  {source}
                </div>
                <div className="flex min-h-16 flex-col items-center justify-center px-2 text-center text-sm text-slate-600">
                  <span>{relation}</span>
                  <span aria-hidden="true" className="text-xl leading-5">
                    →
                  </span>
                </div>
                <div className="flex min-h-16 items-center justify-center rounded-md bg-slate-50 px-3 py-2 text-center font-medium">
                  {target}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-start gap-1.5">
                {readableEnum(edge.edge_status) ? (
                  <Tag>{readableEnum(edge.edge_status)}</Tag>
                ) : null}
                {confidenceLabel(edge.confidence) ? (
                  <Tag>{confidenceLabel(edge.confidence)}</Tag>
                ) : null}
                {evidenceCount > 0 ? <Tag>{evidenceCount} 条证据</Tag> : null}
              </div>
              {rationale ? (
                <Typography.Paragraph className="!mb-0 !mt-2 text-slate-600">
                  {rationale}
                </Typography.Paragraph>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function ScientificArtifactPresentation({
  artifact,
  model,
}: {
  artifact: WorkbenchArtifact;
  model?: WorkbenchTaskModel;
}): ReactNode | null {
  if (!artifact.content || artifact.format !== "json") return null;
  const document = parseObject(artifact.content);
  if (!document) return null;
  const kind = classifyScientificArtifactPresentation(
    artifact,
    document,
    model,
  );
  const nodeKind = workflowNodeResultPresentationKind(artifact);
  if (nodeKind)
    return (
      <WorkflowNodeResultPresentation kind={nodeKind} document={document} />
    );
  if (kind === "problem_framing" || kind === "retrieval_strategy_design") {
    return (
      <OpenTrajectoryResearchPlanningPresentation
        kind={kind}
        document={document}
      />
    );
  }
  if (kind === "literature") return <LiteratureTable document={document} />;
  if (kind === "pvd_research_records")
    return <PvdResearchRecordPresentation document={document} />;
  if (kind === "pvd_research_context")
    return <PvdResearchContextPresentation document={document} />;
  if (kind === "pvd_evidence_schema")
    return <PvdEvidenceSchemaPresentation document={document} />;
  if (kind === "pvd_retrieval_design")
    return <PvdRetrievalDesignPresentation document={document} />;
  if (kind === "pvd_evidence_applicability")
    return <PvdEvidenceApplicabilityPresentation document={document} />;
  if (kind === "evidence_graph") {
    return (
      <EvidenceGraphPresentation
        artifact={artifact}
        document={document}
        model={model}
      />
    );
  }
  if (kind === "evidence") return <EvidenceCards document={document} />;
  if (kind === "mechanism") return <MechanismGraph document={document} />;
  if (kind === "final_synthesis")
    return <OpenTrajectoryFinalSynthesisPresentation document={document} />;
  return null;
}
