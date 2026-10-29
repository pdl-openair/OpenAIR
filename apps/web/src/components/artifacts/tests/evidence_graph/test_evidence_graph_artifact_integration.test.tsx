import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type {
  WorkbenchArtifact,
  WorkbenchTaskModel,
} from "../../../../model/workbenchTask";
import { ArtifactRenderer } from "../../ArtifactRenderer";
import {
  createEvidenceGraphWorkbenchFixture,
  FIXTURE_RECORD_IDS,
} from "./evidenceGraphWorkbenchFixture";

const RECORD_ID = "rr:11111111111111111111111111111111";

class ResizeObserverMock {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    queueMicrotask(() => {
      this.callback(
        [{ target } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    });
  }

  unobserve() {}

  disconnect() {}
}

class DOMMatrixReadOnlyMock {
  readonly m22 = 1;
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixReadOnlyMock);
  Object.defineProperties(HTMLElement.prototype, {
    offsetWidth: { configurable: true, get: () => 760 },
    offsetHeight: { configurable: true, get: () => 620 },
  });
  Object.defineProperty(SVGElement.prototype, "getBBox", {
    configurable: true,
    value: () => ({ x: 0, y: 0, width: 80, height: 16 }),
  });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 760,
      bottom: 620,
      width: 760,
      height: 620,
      toJSON: () => ({}),
    }),
  );
});

function assessmentArtifact(): WorkbenchArtifact {
  return {
    id: "assessment",
    type: "evidence_assessment_and_research_synthesis",
    artifactType: "evidence_assessment_and_research_synthesis",
    title: "证据评估与研究综合",
    format: "json",
    relatedStage: "execution_validation",
    sourceCapabilityCallId: "call-assessment",
    content: JSON.stringify({
      direct_assessment: "当前证据支持有边界的稳定性判断。",
      evidence_judgments: [
        {
          statement: "在当前测量条件下，方法 A 的稳定性更高。",
          disposition: "supported",
          record_ids: [RECORD_ID],
          scope_boundary: "仅适用于当前温度和测量窗口。",
          rationale: "来源研究记录提供了直接测量结果。",
          confidence: 0.88,
        },
      ],
      conflicts: [],
      limitations: [],
      evidence_gaps: [],
      synthesis_warnings: [],
    }),
  };
}

function recordsArtifact(content = true): WorkbenchArtifact {
  return {
    id: "records",
    type: "research_records",
    artifactType: "research_records",
    title: "来源研究记录",
    format: "json",
    relatedStage: "execution_validation",
    content: content
      ? JSON.stringify({
          research_records: [
            {
              record_id: RECORD_ID,
              research_object: "催化剂稳定性",
              record_type: "quantitative_result",
              result: { statement: "在 400–600°C 范围内稳定性提高。" },
              uncertainty: "长期运行仍需继续验证。",
              source: {
                title: "高温稳定性研究",
                doi: "10.1000/stability.zh",
                arxiv_id: "",
                provider: "publisher",
                source_locator: "https://doi.org/10.1000/stability.zh",
                section_name: "结果",
              },
            },
          ],
        })
      : null,
  };
}

function taskModel(recordsContent = true, linked = true): WorkbenchTaskModel {
  const assessment = assessmentArtifact();
  const records = recordsArtifact(recordsContent);
  return {
    artifacts: [assessment, records],
    capabilityCalls: [
      {
        id: "call-assessment",
        name: "ot_v3_evidence_assessment_synthesis",
        status: "completed",
        inputArtifactRefs: linked ? [records.id] : [],
        outputArtifactRefs: [assessment.id],
      },
    ],
  } as unknown as WorkbenchTaskModel;
}

describe("Evidence Graph formal ArtifactRenderer integration", () => {
  it("renders historical draft projection without replacing original content or inventing links", async () => {
    const model = taskModel();
    const record = model.artifacts[1];
    const projectedId = "rr:22222222222222222222222222222222";
    const projectedContent = record.content!.replaceAll(RECORD_ID, projectedId);
    record.content = JSON.stringify({ research_records: [{ research_object: "旧草稿" }] });
    record.displayProjection = {
      kind: "historical_draft_display",
      label: "历史草稿整理展示",
      sourceArtifactId: record.id,
      sourceArtifactIds: [record.id],
      originalStatus: "unfinalized",
      content: projectedContent,
    };
    render(<ArtifactRenderer artifact={model.artifacts[0]} model={model} bodyOnly />);
    expect(await screen.findByTestId(`evidence-node-${projectedId}`)).toBeInTheDocument();
    expect(screen.getByText(/包含历史草稿整理/)).toBeInTheDocument();
    expect(screen.queryByTestId("judgment-node-01")).not.toBeInTheDocument();
    expect(record.content).toContain("旧草稿");
  });

  it("renders judgments using records from every formally supplied input artifact", async () => {
    const model = taskModel();
    const secondId = "rr:22222222222222222222222222222222";
    const second = { ...recordsArtifact(), id: "records-2" };
    second.content = second.content!.replaceAll(RECORD_ID, secondId);
    model.artifacts.push(second);
    model.capabilityCalls[0].inputArtifactRefs!.push(second.id);
    const assessment = JSON.parse(model.artifacts[0].content!);
    assessment.evidence_judgments[0].record_ids.push(secondId);
    model.artifacts[0].content = JSON.stringify(assessment);

    render(
      <ArtifactRenderer artifact={model.artifacts[0]} model={model} bodyOnly />,
    );

    expect(
      await screen.findByTestId(`evidence-node-${RECORD_ID}`),
    ).toBeInTheDocument();
    expect(screen.getByTestId(`evidence-node-${secondId}`)).toBeInTheDocument();
    expect(screen.getByTestId("judgment-node-01")).toBeInTheDocument();
  });

  it("waits for every formal input before projecting the complete graph", () => {
    const model = taskModel();
    model.artifacts.push({ ...recordsArtifact(false), id: "records-2" });
    model.capabilityCalls[0].inputArtifactRefs!.push("records-2");
    render(
      <ArtifactRenderer artifact={model.artifacts[0]} model={model} bodyOnly />,
    );
    expect(screen.queryByTestId("ot-evidence-graph")).not.toBeInTheDocument();
    expect(screen.getByTestId("ot-scientific-empty")).toHaveTextContent(
      "正在读取",
    );
  });

  it("does not overwrite conflicting record identities across formal inputs", async () => {
    const model = taskModel();
    const conflicting = { ...recordsArtifact(), id: "records-2" };
    conflicting.content = conflicting.content!.replace(
      "稳定性提高",
      "稳定性下降",
    );
    model.artifacts.push(conflicting);
    model.capabilityCalls[0].inputArtifactRefs!.push(conflicting.id);
    render(
      <ArtifactRenderer artifact={model.artifacts[0]} model={model} bodyOnly />,
    );
    expect(
      screen.queryByTestId(`evidence-node-${RECORD_ID}`),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("judgment-node-01")).not.toBeInTheDocument();
  });

  it("renders the Chinese acceptance fixture through the canonical renderer with all four relations and shared records", async () => {
    const { assessmentArtifact, model } = createEvidenceGraphWorkbenchFixture();
    render(
      <ArtifactRenderer artifact={assessmentArtifact} model={model} bodyOnly />,
    );

    expect(await screen.findByTestId("ot-evidence-graph")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "证据关系图" }),
    ).toBeInTheDocument();
    for (const label of ["支持", "反驳", "限制", "不确定"]) {
      expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
    }
    expect(
      screen.getAllByTestId(`evidence-node-${FIXTURE_RECORD_IDS.field}`),
    ).toHaveLength(1);
    expect(screen.getByTestId("conflict-node-01")).toHaveTextContent(
      "证据冲突",
    );
    expect(screen.getByTestId("gap-node-01")).toHaveTextContent("证据缺口");
    expect(
      screen.getByTestId(`evidence-node-${FIXTURE_RECORD_IDS.gapOnly}`),
    ).toBeInTheDocument();
  });

  it("renders the graph through the canonical JSON artifact renderer chain", async () => {
    const model = taskModel();
    const assessment = model.artifacts[0];
    render(<ArtifactRenderer artifact={assessment} model={model} bodyOnly />);

    expect(await screen.findByTestId("ot-evidence-graph")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "证据关系图" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId(`evidence-node-${RECORD_ID}`)).toHaveTextContent(
      "在 400–600°C 范围内稳定性提高",
    );
    expect(screen.getByTestId("judgment-node-01")).toHaveTextContent(
      "在当前测量条件下，方法 A 的稳定性更高",
    );
  });

  it("shows a Chinese fail-closed state when the formal ResearchRecord input cannot be resolved", () => {
    const model = taskModel(true, false);
    render(
      <ArtifactRenderer artifact={model.artifacts[0]} model={model} bodyOnly />,
    );

    expect(screen.queryByTestId("ot-evidence-graph")).not.toBeInTheDocument();
    expect(screen.getByTestId("ot-scientific-empty")).toHaveTextContent(
      "无法确认本次证据评估对应的正式研究记录输入",
    );
  });

  it("reports dependency loading without falling back to unrelated records", () => {
    const model = taskModel(false, true);
    render(
      <ArtifactRenderer artifact={model.artifacts[0]} model={model} bodyOnly />,
    );

    expect(screen.getByTestId("ot-scientific-empty")).toHaveTextContent(
      "正在读取本次证据评估使用的来源研究记录",
    );
  });
});
