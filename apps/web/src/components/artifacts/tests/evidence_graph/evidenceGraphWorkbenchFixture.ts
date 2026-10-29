import type {
  WorkbenchArtifact,
  WorkbenchTaskModel,
} from "../../../../model/workbenchTask";

export const FIXTURE_RECORD_IDS = {
  calibrated: "rr:11111111111111111111111111111111",
  field: "rr:22222222222222222222222222222222",
  contrary: "rr:33333333333333333333333333333333",
  shortWindow: "rr:44444444444444444444444444444444",
  gapOnly: "rr:55555555555555555555555555555555",
} as const;

function researchRecord({
  recordId,
  title,
  result,
  object,
  doi,
  locator,
}: {
  recordId: string;
  title: string;
  result: string;
  object: string;
  doi: string;
  locator: string;
}) {
  return {
    record_id: recordId,
    record_type: "quantitative_result",
    research_object: object,
    result: { statement: result },
    uncertainty: "结论仅适用于记录中明确说明的测量条件。",
    source: {
      title,
      doi,
      arxiv_id: "",
      provider: "publisher",
      source_locator: locator,
      section_name: "结果",
    },
  };
}

export const EVIDENCE_GRAPH_WORKBENCH_RECORDS = {
  research_records: [
    researchRecord({
      recordId: FIXTURE_RECORD_IDS.calibrated,
      title: "闭环轨迹精度研究（2024）",
      result: "校准后闭环轨迹精度在当前测量范围内提高约 18%。",
      object: "闭环控制器的轨迹精度",
      doi: "10.1000/openair.calibrated-trajectory",
      locator: "https://doi.org/10.1000/openair.calibrated-trajectory",
    }),
    researchRecord({
      recordId: FIXTURE_RECORD_IDS.field,
      title: "长时现场观测研究（2023）",
      result: "长时现场观测未复现同等幅度的性能提升。",
      object: "长时现场条件下的控制性能",
      doi: "https://doi.org/10.1000/OpenAIR.Field-Observation",
      locator: "https://doi.org/10.1000/OpenAIR.Field-Observation",
    }),
    researchRecord({
      recordId: FIXTURE_RECORD_IDS.contrary,
      title: "跨平台复现实验（2024）",
      result: "在另一平台上，改进方法与基线的稳定性差异不显著。",
      object: "跨平台稳定性复现",
      doi: "10.1000/openair.cross-platform",
      locator: "https://doi.org/10.1000/openair.cross-platform",
    }),
    researchRecord({
      recordId: FIXTURE_RECORD_IDS.shortWindow,
      title: "短时稳定窗口实验（2022）",
      result: "短时窗口内稳定性得到验证，但没有覆盖长期漂移。",
      object: "短时窗口稳定性",
      doi: "10.1000/openair.short-window",
      locator: "https://doi.org/10.1000/openair.short-window",
    }),
    researchRecord({
      recordId: FIXTURE_RECORD_IDS.gapOnly,
      title: "低温条件边界记录（2024）",
      result: "记录定义了低温测量边界，但未提供足以形成判断的独立结果。",
      object: "低温条件下的测量边界",
      doi: "10.1000/openair.low-temperature-boundary",
      locator: "https://doi.org/10.1000/openair.low-temperature-boundary",
    }),
  ],
};

export const EVIDENCE_GRAPH_WORKBENCH_ASSESSMENT = {
  direct_assessment:
    "当前证据支持有边界的性能改进判断，但跨平台与长期稳定性仍需谨慎。",
  evidence_judgments: [
    {
      statement: "在已校准的当前测量条件下，方法 A 可以提高轨迹精度。",
      disposition: "supported",
      record_ids: [FIXTURE_RECORD_IDS.calibrated, FIXTURE_RECORD_IDS.field],
      scope_boundary: "仅适用于已校准控制器和当前测量范围。",
      rationale: "两条正式研究记录提供了当前测量范围内的直接结果。",
      confidence: 0.88,
    },
    {
      statement: "方法 A 的性能提升可以直接泛化到不同平台。",
      disposition: "refuted",
      record_ids: [FIXTURE_RECORD_IDS.field, FIXTURE_RECORD_IDS.contrary],
      scope_boundary: "仅判断当前已记录的跨平台复现结果。",
      rationale: "现场与跨平台记录没有复现同等幅度的性能提升。",
      confidence: 0.82,
    },
    {
      statement: "现有证据能够证明方法 A 具备长期稳定性。",
      disposition: "limited",
      record_ids: [
        FIXTURE_RECORD_IDS.calibrated,
        FIXTURE_RECORD_IDS.shortWindow,
      ],
      scope_boundary: "现有测量仅覆盖有限运行窗口。",
      rationale: "短时稳定性成立，但长期漂移没有被充分测量。",
      confidence: 0.76,
    },
    {
      statement: "在未覆盖的运行条件下仍可保持相同改进幅度。",
      disposition: "uncertain",
      record_ids: [FIXTURE_RECORD_IDS.shortWindow],
      scope_boundary: "未覆盖的运行条件没有足够直接证据。",
      rationale: "当前记录不足以明确支持或反驳该判断。",
      confidence: 0.58,
    },
  ],
  conflicts: [
    {
      statement: "校准实验与现场观测对性能提升幅度给出了不一致结果。",
      record_ids: [FIXTURE_RECORD_IDS.calibrated, FIXTURE_RECORD_IDS.field],
      implication: "不能把校准条件下的改进幅度直接外推到长时现场条件。",
    },
  ],
  limitations: ["当前记录没有覆盖所有运行条件。"],
  evidence_gaps: [
    {
      gap: "仍缺少低温条件下的独立性能验证。",
      related_record_ids: [FIXTURE_RECORD_IDS.gapOnly],
      impact: "低温条件下的性能判断目前不能成立。",
    },
  ],
  synthesis_warnings: [],
};

export function createEvidenceGraphWorkbenchFixture(): {
  assessmentArtifact: WorkbenchArtifact;
  recordsArtifact: WorkbenchArtifact;
  model: WorkbenchTaskModel;
} {
  const recordsArtifact: WorkbenchArtifact = {
    id: "fixture-research-records",
    type: "research_records",
    artifactType: "research_records",
    title: "来源研究记录",
    format: "json",
    relatedStage: "execution_validation",
    sourceCapabilityCallId: "fixture-call-records",
    content: JSON.stringify(EVIDENCE_GRAPH_WORKBENCH_RECORDS),
  };
  const assessmentArtifact: WorkbenchArtifact = {
    id: "fixture-evidence-assessment",
    type: "evidence_assessment_and_research_synthesis",
    artifactType: "evidence_assessment_and_research_synthesis",
    title: "证据评估与研究综合",
    format: "json",
    relatedStage: "execution_validation",
    sourceCapabilityCallId: "fixture-call-assessment",
    content: JSON.stringify(EVIDENCE_GRAPH_WORKBENCH_ASSESSMENT),
  };

  const model = {
    taskId: "fixture-t12-eg-workbench",
    artifacts: [recordsArtifact, assessmentArtifact],
    capabilityCalls: [
      {
        id: "fixture-call-records",
        name: "evidence_node_extraction",
        status: "completed",
        outputArtifactRefs: [recordsArtifact.id],
      },
      {
        id: "fixture-call-assessment",
        name: "ot_v3_evidence_assessment_synthesis",
        status: "completed",
        inputArtifactRefs: [recordsArtifact.id],
        inputCallRefs: ["fixture-call-records"],
        outputArtifactRefs: [assessmentArtifact.id],
      },
    ],
  } as unknown as WorkbenchTaskModel;

  return { assessmentArtifact, recordsArtifact, model };
}
