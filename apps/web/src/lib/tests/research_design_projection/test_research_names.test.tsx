import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { render, screen, cleanup } from "@testing-library/react";
import {
  getSubStageLabels,
  RESEARCH_ARTIFACT_LABELS_ZH,
  researchPresentationParameterName,
} from "@openair/shared";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkbenchFlowNav } from "../../../components/workbench/WorkbenchFlowNav";
import { SubstageArtifactFooter } from "../../../components/workbench/SubstageArtifactFooter";
import { researchArtifactTitle } from "../../researchArtifactTitle";
import { userFacingDynamicText } from "../../userFacingLanguage";
import { userVisibleWorkbenchArtifacts } from "../../workbenchUi";
import {
  researchDraftNodeTitle,
  researchDraftNodeAction,
} from "../../researchDesignPresentation";
import morningPlan from "./morning-ot-plan.json";

afterEach(cleanup);

function skillManifests(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true })
    .filter((entry) => !entry.name.startsWith("."))
    .flatMap((entry) => {
      const file = path.join(directory, entry.name);
      return entry.isDirectory()
        ? skillManifests(file)
        : entry.name === "skill.yaml"
          ? [file]
          : [];
    });
}

describe("formal research names", () => {
  it("names distinct producer outputs within the same semantic work envelope", () => {
    const names = ["pvd_research_request_understanding", "pvd_evidence_retrieval_design", "pvd_evidence_schema_design"].map((executorId) =>
      researchArtifactTitle({
        title: "研究成果",
        artifactType: "pvd.research_retrieval_experiment_design.planned_output",
        validatorResult: { producerCompletionAuthority: { executorRef: { executorType: "skill", executorId } } },
      }),
    );
    expect(names).toEqual(["研究目标、条件与参数", "工艺证据检索计划", "工艺证据需求与覆盖设计"]);
    expect(researchArtifactTitle({title: "研究成果", artifactType: "recommendation_parameter_set"})).toBe("可调参数与范围");
    expect(researchArtifactTitle({title: "研究成果", artifactType: "run_preferences"})).toBe("本轮实验安排");
    expect(researchArtifactTitle({title: "研究成果", artifactType: "structured_context"})).toBe("研究目标与已知条件");
    expect(researchArtifactTitle({title: "研究成果", validatorResult: { artifactContract: "direct_skill:cs_paper_source_resolution:v1" }})).toBe("论文与正式来源确认结果");
    expect(researchArtifactTitle({title: "研究成果", validatorResult: { artifactContract: "direct_skill:retrieval_matrix_generation:v1" }})).toBe("证据检索计划");
    expect(researchArtifactTitle({title: "pvd_evidence_retrieval_design result"})).toBe("pvd_evidence_retrieval_design result");
  });
  it("keeps the authored file tail so same-producer siblings stay distinguishable", () => {
    // task_ef2e442d S3: one dispatch mints the raw result plus file outputs,
    // all English titles that would otherwise collapse onto the label.
    const siblings = [
      { title: "crystal_design_structure_crossover_agent result", artifactType: "crystal_design_structure_crossover_agent" },
      { title: "Crystal design structure crossover · leaderboard.csv", artifactType: "crystal_design_structure_crossover_agent" },
      { title: "Crystal design structure crossover · pareto_01_MgO.png", artifactType: "crystal_design_structure_crossover_agent" },
      { title: "Crystal design structure crossover · pareto_01_MgO.cif", artifactType: "crystal_design_structure_crossover_agent" },
    ].map(researchArtifactTitle);
    expect(siblings).toEqual([
      "结构演化设计",
      "结构演化设计 · leaderboard.csv",
      "结构演化设计 · pareto_01_MgO.png",
      "结构演化设计 · pareto_01_MgO.cif",
    ]);
    expect(new Set(siblings).size).toBe(4);
    // Resolved titles are stable across the boundary (idempotent re-resolution).
    expect(researchArtifactTitle({ title: siblings[1], artifactType: "crystal_design_structure_crossover_agent" })).toBe(siblings[1]);
  });
  it("translates formal work identities in historical interaction copy without altering research terms", () => {
    expect(userFacingDynamicText("为 evidence_applicability_comparability_analysis 准备正式研究材料。AlScN / N₂", "")).toBe("为 证据适用性与可比性分析 准备正式研究材料。AlScN / N₂");
  });
  it("uses exact registered parameter names and preserves dynamic authored identities", () => {
    expect(researchPresentationParameterName("power", "Power")).toBe("功率");
    expect(researchPresentationParameterName("n2_flow", "N₂")).toBe("氮气流量（N₂）");
    expect(researchPresentationParameterName("ar_flow", "Ar")).toBe("氩气流量（Ar）");
    expect(researchPresentationParameterName("deposition_time", "deposition time")).toBe("沉积时间");
    expect(researchPresentationParameterName("pulse_power", "脉冲功率" )).toBe("脉冲功率");
  });
  it("maps all artifact types declared by the registered Skill contracts", () => {
    const skills = path.resolve(process.cwd(), "../../skills");
    const manifests = skillManifests(skills);
    const types = new Set<string>();
    for (const file of manifests) {
      const source = readFileSync(file, "utf8");
      for (const list of source.matchAll(
        /artifact_types:\r?\n((?:\s+- [^\r\n]+\r?\n)+)/g,
      )) {
        for (const item of list[1].matchAll(/^\s+-\s+([\w.:-]+)\s*$/gm))
          types.add(item[1]);
      }
    }
    expect(types.size).toBeGreaterThan(0);
    for (const type of types)
      expect(RESEARCH_ARTIFACT_LABELS_ZH[type], type).toMatch(
        /[\u3400-\u9fff]/,
      );
  });
  it("covers every formal substage, including disabled work types", () => {
    const schemas = path.resolve(process.cwd(), "../../schemas");
    for (const pack of readdirSync(schemas, { withFileTypes: true }).filter(
      (item) => item.isDirectory(),
    )) {
      const catalog = path.join(schemas, pack.name, "stage-substage-map.yaml");
      if (
        ![
          "cs_reproduction",
          "materials",
          "ope",
          "opentrajectory",
          "pvd",
          "sdr",
        ].includes(pack.name)
      )
        continue;
      const keys = [
        ...readFileSync(catalog, "utf8").matchAll(/\bkey:\s*(\w+)/g),
      ].map((match) => match[1]);
      for (const key of keys) {
        expect(getSubStageLabels("zh-CN")[key], `${pack.name}:${key}`).toMatch(
          /[\u3400-\u9fff]/,
        );
        expect(getSubStageLabels("en-US")[key]).toBeTruthy();
      }
    }
  });

  it("renders the morning draft's eleven objectives with six formal names in execution navigation", () => {
    const nodes = morningPlan.stages.flatMap((stage) => stage.substages);
    expect(nodes).toHaveLength(11);
    expect(new Set(nodes.map(researchDraftNodeTitle)).size).toBe(6);
    for (const node of nodes) {
      expect(researchDraftNodeTitle(node)).toBe(node.label);
      expect(researchDraftNodeAction(node)).toBe(node.action);
    }
    const subStages = nodes.map((node, index) => ({
      id: `sub-${index}`,
      subStage: `opentrajectory:${node.substage}`,
      label: node.action,
      objective: node.action,
      status: "running",
      artifactRefs: [],
    }));
    render(
      <WorkbenchFlowNav
        language="zh-CN"
        phaseAgg={{} as never}
        stagesByPhase={{ experiment: { subStages } } as never}
        progressSteps={[
          { id: "experiment", label: "执行验证", status: "running" },
        ]}
        viewingPhase="experiment"
        currentPagePhase="experiment"
        selectedSubStage={null}
        onSelectPhase={vi.fn()}
        onSelectSubStage={vi.fn()}
      />,
    );
    for (const node of nodes)
      expect(
        screen.getByText(`${node.label}：${node.action}`),
      ).toBeInTheDocument();
  });

  it("names distinct results in one substage consistently in the footer", () => {
    const artifacts = [
      "opentrajectory.problem_framing",
      "opentrajectory.retrieval_strategy_design",
      "research_records",
    ].map((type, index) => ({
      id: `a-${index}`,
      taskId: "task",
      title: type,
      type,
      format: "json",
      relatedStage: "problem_modeling" as const,
      subStageInstanceId: "sub",
      artifactVisibility: "user",
    }));
    render(
      <SubstageArtifactFooter
        taskId="task"
        substageInstanceId="sub"
        artifactRefs={artifacts.map((item) => item.id)}
        artifacts={artifacts}
        selection={null}
        artifactHref={(id) => `?artifact=${id}`}
        onSelectArtifact={vi.fn()}
      />,
    );
    for (const artifact of artifacts)
      expect(
        screen.getByRole("link", { name: researchArtifactTitle(artifact) }),
      ).toBeInTheDocument();
    expect(new Set(artifacts.map(researchArtifactTitle)).size).toBe(3);
    expect(
      userVisibleWorkbenchArtifacts(artifacts).map(
        (artifact) => artifact.title,
      ),
    ).toEqual(artifacts.map(researchArtifactTitle));
    expect(
      researchArtifactTitle({
        title: "general_research_reasoning",
        artifactType: "general_research_reasoning",
      }),
    ).toBe("科研分析与判断");
    expect(
      researchArtifactTitle({ title: "opentrajectory.unknown_internal_type" }),
    ).toBe("研究成果");
    expect(
      researchArtifactTitle({
        title: "本轮对照实验结果",
        type: "research_records",
      }),
    ).toBe("本轮对照实验结果");
    for (const title of Object.values(RESEARCH_ARTIFACT_LABELS_ZH))
      expect(title).toMatch(/[\u3400-\u9fff]/);
  });
});
