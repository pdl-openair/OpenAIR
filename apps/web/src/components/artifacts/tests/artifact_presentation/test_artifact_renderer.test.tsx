import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ArtifactRenderer,
  projectStructuredArtifactMarkdown,
} from "../../ArtifactRenderer";
import { projectStructuredArtifactForDisplay } from "../../structuredArtifactProjection";

describe("ArtifactRenderer", () => {
  it("presents CS source facts in Chinese and separates technical checks without rewriting references", () => {
    const result = projectStructuredArtifactForDisplay(
      JSON.stringify({
        title: "Original Paper Title",
        source_evidence: {
          kind: "primary_paper_author_declared_code_link",
          content_form: "metadata_only",
          url: "https://example.test/code",
        },
        resolved_commit: "abc123",
        operation: "read_head",
        exit_code: 0,
        head_matches_resolved_commit: true,
        provider_error: "SYSTEM_FAILURE_MUST_NOT_RENDER",
        limitations: "尚未完成独立测量。",
      }),
      "论文与代码来源",
    );
    expect(result?.markdown).toContain("论文作者声明的代码链接");
    expect(result?.markdown).toContain("仅元数据，尚未取得正文");
    expect(result?.markdown).toContain("来源依据");
    expect(result?.markdown).toContain("Original Paper Title");
    expect(result?.markdown).toContain("https://example.test/code");
    expect(result?.markdown).toContain("abc123");
    expect(result?.markdown).toContain("尚未完成独立测量。");
    expect(result?.markdown).not.toContain("读取代码版本");
    expect(result?.technicalDetails).toContain("读取代码版本");
    expect(result?.technicalDetails).toContain("代码版本是否一致");
    expect(JSON.stringify(result)).not.toContain(
      "SYSTEM_FAILURE_MUST_NOT_RENDER",
    );
  });

  it("resolves parameter labels only from exact unambiguous display names in the same artifact", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        parameter_registry: [
          { parameter_key: "test_power", display_name: "测试功率" },
        ],
        result: { parameter_key: "test_power", value: 4, unit: "W" },
        unexpected_scientific_fact: "新增科研事实保留。",
      }),
      "参数结果",
    );
    expect(markdown).toContain("测试功率");
    expect(markdown).not.toContain("test_power");
    expect(markdown).toContain("新增科研事实保留。");
  });
  it("prioritizes research content and demotes planning bookkeeping", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        items: [
          {
            confirmation_status: "unconfirmed",
            information_source: "planner_inference",
            system_planning_judgement: "controller_planning",
            description: "2024年以来存在若干具有公开描述或实现的代表性方法。",
          },
        ],
      }),
      "研究结果",
    );

    expect(markdown).toContain(
      "2024年以来存在若干具有公开描述或实现的代表性方法。",
    );
    expect(markdown).not.toMatch(/^#{1,6} 确认状态$/m);
    expect(markdown).not.toMatch(/^#{1,6} 信息来源$/m);
    expect(markdown).not.toMatch(/^#{1,6} 系统规划判断$/m);
    expect(markdown).not.toMatch(/^#{1,6} 条目 \d+$/m);
  });

  it("uses meaningful array identity instead of mechanical item headings", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        findings: [
          { title: "GraphRAG 方法族", evidence: "公开实现与方法描述可核查" },
          {
            finding: "混合检索仍是强基线",
            evidence: "多个公开评测采用向量与关键词混合检索",
          },
        ],
      }),
      "比较结果",
    );

    expect(markdown).toContain("### GraphRAG 方法族");
    expect(markdown).toContain("### 混合检索仍是强基线");
    expect(markdown).not.toMatch(/条目 \d+/);
  });

  it("preserves an unknown legitimate research field with a readable semantic label", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        evaluation_protocol:
          "固定数据集、统一检索预算，并报告准确性与引用可靠性。",
      }),
      "实验设计",
    );

    expect(markdown).toContain("## \u8bc4\u4f30\u534f\u8bae");
    expect(markdown).toContain(
      "固定数据集、统一检索预算，并报告准确性与引用可靠性。",
    );
  });

  it("preserves legitimate scientific identifiers instead of suppressing generic id suffixes", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        datasetId: "dataset-42",
        sampleId: "sample-A",
        geneId: "TP53",
        compoundId: "CHEBI:15365",
        study_id: "study-2026-08",
      }),
      "研究证据",
    );

    expect(markdown).toContain("\u6570\u636e\u96c6\u7f16\u53f7");
    expect(markdown).toContain("dataset-42");
    expect(markdown).toContain("\u6837\u54c1\u7f16\u53f7");
    expect(markdown).toContain("sample-A");
    expect(markdown).toContain("\u57fa\u56e0\u7f16\u53f7");
    expect(markdown).toContain("TP53");
    expect(markdown).toContain("\u5316\u5408\u7269\u7f16\u53f7");
    expect(markdown).toContain("CHEBI:15365");
    expect(markdown).toContain("\u7814\u7a76\u7f16\u53f7");
    expect(markdown).toContain("study-2026-08");
  });

  it("preserves scientific runtime metrics instead of suppressing generic runtime prefixes", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        runtime_ms: 184,
        runtime_seconds: 0.184,
      }),
      "性能结果",
    );

    expect(markdown).toContain(
      "\u8fd0\u884c\u8017\u65f6\uff08\u6beb\u79d2\uff09",
    );
    expect(markdown).toContain("184");
    expect(markdown).toContain("\u8fd0\u884c\u8017\u65f6\uff08\u79d2\uff09");
    expect(markdown).toContain("0.184");
  });

  it("preserves unknown legitimate fields that share an internal-looking prefix", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        provider_effect: "供应商差异可能影响检索延迟。",
      }),
      "敏感性分析",
    );

    expect(markdown).toContain("\u6765\u6e90\u63d0\u4f9b\u65b9\u5f71\u54cd");
    expect(markdown).toContain("供应商差异可能影响检索延迟。");
  });

  it("continues suppressing only known internal bookkeeping fields", () => {
    const markdown = projectStructuredArtifactMarkdown(
      JSON.stringify({
        findings: "科研正文必须保留",
        runtime_metadata: "runtime-secret",
        orchestration_state: "orchestration-secret",
        provider_name: "provider-secret",
        lifecycle_state: "lifecycle-secret",
        schema_version: "schema-secret",
        dispatch_id: "dispatch-secret",
        internalRecordId: "id-secret",
      }),
      "研究结果",
    );

    expect(markdown).toContain("科研正文必须保留");
    expect(markdown).not.toContain("runtime-secret");
    expect(markdown).not.toContain("orchestration-secret");
    expect(markdown).not.toContain("provider-secret");
    expect(markdown).not.toContain("lifecycle-secret");
    expect(markdown).not.toContain("schema-secret");
    expect(markdown).not.toContain("dispatch-secret");
    expect(markdown).not.toContain("id-secret");
  });

  it("preserves canonical Markdown body that uses configuration/adapter/trace research vocabulary", () => {
    const body =
      "The experimental configuration uses an adapter to trace the ablation protocol across runs.";
    render(
      <ArtifactRenderer
        artifact={{
          id: "canonical-md-research-vocab-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "research_report",
          title: "实验配置说明",
          format: "markdown",
          content: body,
          relatedStage: "execution_validation",
        }}
        taskId="task-1"
        bodyOnly
      />,
    );

    expect(screen.getByText(body)).toBeInTheDocument();
    expect(
      screen.queryByText(/当前步骤没有形成可用业务产物/),
    ).not.toBeInTheDocument();
  });

  it("preserves structured research strings that mention configuration/adapter/trace", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "canonical-json-research-vocab-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "research_report",
          title: "方法配置记录",
          format: "json",
          content: JSON.stringify({
            findings:
              "The experimental configuration uses an adapter to trace baseline drift.",
            description:
              "Adapter-level configuration and trace coverage are research content.",
            runtime_metadata: "runtime-secret",
            schema_version: "schema-secret",
            dispatch_id: "dispatch-secret",
          }),
          relatedStage: "execution_validation",
        }}
        taskId="task-1"
        bodyOnly
      />,
    );

    expect(
      screen.getByText(
        "The experimental configuration uses an adapter to trace baseline drift.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Adapter-level configuration and trace coverage are research content.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/当前步骤没有形成可用业务产物/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("runtime-secret")).not.toBeInTheDocument();
    expect(screen.queryByText("schema-secret")).not.toBeInTheDocument();
    expect(screen.queryByText("dispatch-secret")).not.toBeInTheDocument();
  });

  it("projects a canonical JSON artifact into readable Markdown in the Workbench body", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "canonical-json-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "research_report",
          title: "实时调研结果",
          format: "json",
          content: JSON.stringify({
            researchQuestion: "比较两种候选方法的实验表现",
            findings: ["方法 A 在准确率上更高", "方法 B 的计算成本更低"],
            question_candidates: [
              {
                candidate_id: "candidate-1",
                local_key: "local-1",
                question: "优先验证哪种方法",
              },
            ],
            verification_readiness: "数据集与评价指标已经就绪",
          }),
          relatedStage: "execution_validation",
          validatorResult: {
            contractVersion: "artifact-evaluator.v1",
            evaluator: "canonical-artifact-validator",
            artifactState: "usable",
            usable: true,
            missingInputs: [],
            issues: [],
            nextAction: "可供查看",
          },
        }}
        taskId="task-1"
        bodyOnly
      />,
    );

    expect(
      screen.getByRole("heading", { name: "实时调研结果" }),
    ).toBeInTheDocument();
    expect(screen.getByText("比较两种候选方法的实验表现")).toBeInTheDocument();
    expect(screen.getByText("方法 A 在准确率上更高")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "候选研究问题" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "验证准备情况" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("当前产物暂无正文。")).not.toBeInTheDocument();
    expect(screen.queryByText(/\{"researchQuestion":/)).not.toBeInTheDocument();
    expect(screen.queryByText("candidate-1")).not.toBeInTheDocument();
    expect(screen.queryByText("local-1")).not.toBeInTheDocument();
  });

  it("localizes a raw canonical artifact-type title in the Workbench body", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "question-refinement-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "execution_output",
          artifactType: "research_question_refinement",
          title: "research_question_refinement",
          format: "json",
          content: JSON.stringify({
            researchQuestion: "GraphRAG 应如何与混合检索 RAG 对比？",
            assumptions: [
              {
                assumption_id: "topic_unspecified",
                source_basis: "planner_inference",
                statement: "继续核查公开证据",
              },
            ],
            handoff: {
              candidate_ids: ["exploratory_mapping"],
              handoff_status: "needs_user_clarification",
              next_consumer: "user_clarification",
            },
          }),
          relatedStage: "problem_modeling",
        }}
        taskId="task-1"
        bodyOnly
      />,
    );

    expect(
      screen.getByRole("heading", { name: "研究问题凝练" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/系统规划推断/)).toBeInTheDocument();
    expect(screen.getByText(/需要补充用户信息/)).toBeInTheDocument();
    expect(screen.getByText("用户补充")).toBeInTheDocument();
    expect(
      screen.queryByText("research_question_refinement"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("topic_unspecified")).not.toBeInTheDocument();
    expect(screen.queryByText("exploratory_mapping")).not.toBeInTheDocument();
    expect(screen.queryByText("planner_inference")).not.toBeInTheDocument();
  });

  it("renders literature and validation contract fields as readable research headings", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "literature-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "execution_output",
          artifactType: "key_literature_positioning",
          title: "key_literature_positioning",
          format: "json",
          content: JSON.stringify({
            search_boundary: {
              authorization_status: "retrieval_authorized",
              target_question: "比较 GraphRAG 与混合检索 RAG",
            },
            candidate_literature: [
              {
                candidate_id: "internal-candidate-1",
                title: "GraphRAG representative method",
                screening_status: "candidate_unverified",
              },
            ],
            coverage_limitations: [{ statement: "仅纳入可公开核查的来源" }],
          }),
          relatedStage: "problem_modeling",
        }}
        taskId="task-1"
        bodyOnly
      />,
    );

    expect(
      screen.getByRole("heading", { name: "关键文献定位" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "检索边界" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "候选文献与公开实现" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "覆盖限制" }),
    ).toBeInTheDocument();
    expect(screen.getByText("已获准检索")).toBeInTheDocument();
    expect(screen.getByText("候选，待核验")).toBeInTheDocument();
    expect(screen.queryByText("retrieval_authorized")).not.toBeInTheDocument();
    expect(screen.queryByText("internal-candidate-1")).not.toBeInTheDocument();
  });

  it("joins reading_priority onto candidate_literature titles without exposing candidate_id", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "literature-priority-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "execution_output",
          artifactType: "key_literature_positioning",
          title: "key_literature_positioning",
          format: "json",
          content: JSON.stringify({
            candidate_literature: [
              {
                candidate_id: "graphrag_core",
                source_identifier: "https://arxiv.org/abs/2404.16130",
                title:
                  "From Local to Global: A Graph RAG Approach to Query-Focused Summarization",
                screening_status: "screened_in",
                screening_rationale: "Directly represents the GraphRAG method.",
                role_hint: "method_basis",
              },
            ],
            reading_priority: [
              {
                rank: 1,
                candidate_id: "graphrag_core",
                reason: "Directly addresses the core GraphRAG architecture.",
              },
            ],
            coverage_limitations: [
              {
                limitation_id: "coverage_1",
                limitation_kind: "coverage",
                statement:
                  "Current retrieval does not yet establish full-field consensus.",
              },
            ],
          }),
          relatedStage: "problem_modeling",
        }}
        taskId="task-1"
        bodyOnly
      />,
    );

    expect(
      screen.getByRole("heading", { name: "阅读优先级" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "#1 · From Local to Global: A Graph RAG Approach to Query-Focused Summarization",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Directly addresses the core GraphRAG architecture."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Current retrieval does not yet establish full-field consensus.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("graphrag_core")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/\{"candidate_literature"/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/"candidate_id":"graphrag_core"/),
    ).not.toBeInTheDocument();
  });

  it("keeps JSON outside the Workbench body on its existing code render path", () => {
    const content = JSON.stringify({ candidate_id: "candidate-1" });
    render(
      <ArtifactRenderer
        artifact={{
          id: "library-json-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "research_report",
          title: "JSON 预览",
          format: "json",
          content,
          relatedStage: "execution_validation",
        }}
      />,
    );

    // Valid JSON is projected into readable Markdown (not a raw code block).
    expect(
      screen.getByTestId("artifact-markdown-library-json-1"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("artifact-markdown-library-json-1"),
    ).toHaveTextContent("JSON 预览");
    expect(screen.queryByLabelText("JSON 预览")).not.toBeInTheDocument();
    expect(screen.queryByText(/"candidate_id"/)).not.toBeInTheDocument();
  });

  it("keeps the Workbench empty-body message when an artifact has no content", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "empty-json-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "research_report",
          title: "空产物",
          format: "json",
          relatedStage: "execution_validation",
        }}
        bodyOnly
      />,
    );

    expect(screen.getByText("当前产物暂无正文。")).toBeInTheDocument();
  });

  it("keeps Markdown artifacts on the Markdown render path", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "markdown-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "research_report",
          title: "Markdown 产物",
          format: "markdown",
          content: "# 原有 Markdown 正文\n\n这段正文应继续按 Markdown 渲染。",
          relatedStage: "execution_validation",
        }}
        bodyOnly
      />,
    );

    expect(
      screen.getByRole("heading", { name: "原有 Markdown 正文" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("这段正文应继续按 Markdown 渲染。"),
    ).toBeInTheDocument();
  });

  it("renders a CIF artifact with content without throwing", async () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "cif-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "structure",
          title: "Li2O crystal structure",
          format: "cif",
          content: "data_Li2O\n_cell_length_a 4.62",
          relatedStage: "execution_validation",
        }}
        taskId="task-1"
      />,
    );

    expect(
      (await screen.findAllByText("Li2O crystal structure")).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByTestId("artifact-human-review-note"),
    ).toBeInTheDocument();
  });
  it("renders an OT literature retrieval artifact as a scientific table", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "ot-literature-1",
          type: "execution_output",
          title: "academic_source_retrieval_skill result",
          format: "json",
          relatedSkillId: "academic_source_retrieval_skill",
          relatedStage: "execution_validation",
          content: JSON.stringify({
            metadata_candidates: [
              {
                candidate_id: "internal-candidate-1",
                dedupe_key: "doi:10.1000/example",
                title: "Fast-charging degradation in silicon anodes",
                year: 2025,
                source_name: "openalex",
                doi: "10.1000/example",
                arxiv_id: null,
                access_status: "structured_fulltext_ready",
              },
            ],
            retrieval_report: { total_candidates: 1 },
          }),
        }}
        bodyOnly
      />,
    );

    expect(screen.getByTestId("ot-literature-table")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "文献题名" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Fast-charging degradation in silicon anodes"),
    ).toBeInTheDocument();
    expect(screen.getByText("DOI 10.1000/example")).toBeInTheDocument();
    expect(screen.queryByText("internal-candidate-1")).not.toBeInTheDocument();
  });

  it("keeps Top-K selection group and tier visible in the literature table", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "ot-topk-1",
          type: "execution_output",
          title: "selection output",
          format: "json",
          relatedSkillId: "topk_source_selection",
          relatedStage: "execution_validation",
          content: JSON.stringify({
            topk_selection: {
              selected_for_p03: [
                {
                  candidate_id: "candidate:internal",
                  dedupe_key: "doi:10.1000/topk",
                  title: "Operando evidence for interphase-driven degradation",
                  final_score: 0.93,
                  tier: "core",
                  pool: "primary",
                  selection_reason:
                    "Directly resolves the mechanism evidence slot.",
                },
              ],
              extended_selected: [],
              supporting_papers: [],
              method_inspiration_papers: [],
              excluded_examples: [],
            },
          }),
        }}
        bodyOnly
      />,
    );

    expect(screen.getByTestId("ot-literature-table")).toBeInTheDocument();
    expect(screen.getByText("Top-K · 核心")).toBeInTheDocument();
    expect(screen.getByText("0.93")).toBeInTheDocument();
    expect(
      screen.getByText("Directly resolves the mechanism evidence slot."),
    ).toBeInTheDocument();
    expect(screen.queryByText("candidate:internal")).not.toBeInTheDocument();
  });

  it("renders OT EvidenceNode output as evidence cards instead of JSON prose", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "ot-evidence-1",
          type: "execution_output",
          title: "evidence_node_extraction result",
          format: "json",
          relatedSkillId: "evidence_node_extraction",
          relatedStage: "execution_validation",
          content: JSON.stringify({
            evidence_items: [
              {
                evidence_id: "ev:internal",
                source_anchor_id: "anchor:internal",
                candidate_id: "candidate:internal",
                arxiv_id: "2500.00001",
                section_name: "Results and Discussion",
                evidence_type: "quantitative_result",
                claim: "Li||Cu cells reached 99.5% Coulombic efficiency.",
                evidence_quote:
                  "Average Coulombic efficiency reached 99.5% under the stated current density.",
                conditions: [
                  { name: "current density", value_text: "1", unit: "mA cm−2" },
                ],
                metric: {
                  name: "Coulombic efficiency",
                  value_text: "99.5",
                  unit: "%",
                },
                mechanism: null,
                confidence: 0.99,
                target_match_status: "matched",
                evidence_support_tier: "fulltext_grounded",
              },
            ],
            summary: {},
            audit: {},
            extraction_warnings: [],
          }),
        }}
        bodyOnly
      />,
    );

    expect(screen.getByTestId("ot-evidence-view")).toBeInTheDocument();
    expect(
      screen.getByText("Li||Cu cells reached 99.5% Coulombic efficiency."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/指标：Coulombic efficiency：99.5 %/),
    ).toBeInTheDocument();
    expect(screen.getByText("目标匹配：匹配")).toBeInTheDocument();
    expect(screen.getByText("全文证据")).toBeInTheDocument();
    expect(screen.queryByText("ev:internal")).not.toBeInTheDocument();
    expect(screen.queryByText(/\{"evidence_items"/)).not.toBeInTheDocument();
  });

  it("renders OT mechanism synthesis as a directed relationship view with evidence context", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "ot-mechanism-1",
          type: "execution_output",
          title: "mechanism_chain_synthesis result",
          format: "json",
          relatedSkillId: "mechanism_chain_synthesis",
          relatedStage: "execution_validation",
          content: JSON.stringify({
            mechanism_edges: [
              {
                local_edge_key: "edge-1",
                edge_id: "me:00000000000000000001",
                edge_status: "support",
                source_node: {
                  node_type: "structure",
                  label: "Surface reconstruction",
                },
                relation: {
                  predicate: "promotes",
                  description: "accelerates electrolyte oxidation",
                },
                target_node: {
                  node_type: "interphase",
                  label: "Cathode interphase growth",
                },
                evidence_ids: ["ev:1", "ev:2"],
                rationale:
                  "Two independent observations support the directional relation.",
                confidence: 0.84,
              },
            ],
            mechanism_chains: [
              {
                local_chain_key: "chain-1",
                chain_id: "mc:00000000000000000001",
                title: "Reconstruction-driven interphase growth",
                local_edge_keys: ["edge-1"],
                edge_ids: ["me:00000000000000000001"],
                chain_summary:
                  "Surface reconstruction precedes accelerated interphase growth.",
                confidence: 0.84,
              },
            ],
            conflicts: [],
            gaps: [],
            summary: {},
            audit: {},
            synthesis_warnings: [],
          }),
        }}
        bodyOnly
      />,
    );

    expect(screen.getByTestId("ot-mechanism-graph")).toBeInTheDocument();
    expect(screen.getByText("Surface reconstruction")).toBeInTheDocument();
    expect(
      screen.getByText("accelerates electrolyte oxidation"),
    ).toBeInTheDocument();
    expect(screen.getByText("Cathode interphase growth")).toBeInTheDocument();
    expect(screen.getByText("2 条证据")).toBeInTheDocument();
    expect(
      screen.queryByText("me:00000000000000000001"),
    ).not.toBeInTheDocument();
  });

  it("keeps an unknown structured artifact on the readable structured fallback", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "unknown-structured-1",
          type: "execution_output",
          title: "未知结构化结果",
          format: "json",
          relatedStage: "execution_validation",
          content: JSON.stringify({
            evaluation_protocol: "固定检索预算并报告引用可靠性。",
          }),
        }}
        bodyOnly
      />,
    );

    expect(
      screen.getByRole("heading", { name: "未知结构化结果" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("固定检索预算并报告引用可靠性。"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("ot-literature-table")).not.toBeInTheDocument();
  });

  it("does not crash on malformed or partial OT structured content", () => {
    const { rerender } = render(
      <ArtifactRenderer
        artifact={{
          id: "malformed-ot-1",
          type: "execution_output",
          title: "损坏的结构化结果",
          format: "json",
          relatedSkillId: "candidate_source_scoring",
          relatedStage: "execution_validation",
          content: "{not valid json",
        }}
        bodyOnly
      />,
    );

    // Unparseable JSON falls back to the raw code block path.
    expect(screen.getByLabelText("损坏的结构化结果")).toHaveTextContent(
      "{not valid json",
    );

    rerender(
      <ArtifactRenderer
        artifact={{
          id: "partial-ot-1",
          type: "execution_output",
          title: "部分文献结果",
          format: "json",
          relatedSkillId: "candidate_source_scoring",
          relatedStage: "execution_validation",
          content: JSON.stringify({ candidate_scores: [] }),
        }}
        bodyOnly
      />,
    );
    expect(screen.getByTestId("ot-scientific-empty")).toHaveTextContent(
      "暂无可呈现的候选记录",
    );
  });
  it("shows a usable degraded result without presenting it as a task failure", () => {
    render(
      <ArtifactRenderer
        artifact={{
          id: "degraded-artifact-1",
          taskId: "task-1",
          sourceTaskId: "task-1",
          type: "research_report",
          title: "当前研究结果",
          format: "markdown",
          content: "已形成的可信研究内容。",
          relatedStage: "execution_validation",
          artifactState: "usable",
          artifactQuality: "degraded",
          resultProtocol: {
            version: "result-protocol.v1",
            status: "degraded",
            accepted_items: [{ path: "queries", count: 7 }],
            quarantined_items: [
              { path: "queries[7]", reason: "invalid_query" },
            ],
            repaired_items: [
              { path: "queries[2]", action: "normalized_boolean_syntax" },
            ],
            warnings: ["one query was skipped"],
            coverage: { accepted_query_count: 7 },
          },
        }}
        taskId="task-1"
      />,
    );

    expect(screen.getByTestId("artifact-degraded-result")).toHaveTextContent(
      "已形成可用结果，部分内容已跳过或修复",
    );
    expect(screen.getByText(/已隔离 1 项，已修复 1 项/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("查看降级与跳过项"));
    expect(screen.getByTestId("artifact-degraded-details")).toHaveTextContent(
      "queries[7]：invalid_query",
    );
    expect(screen.getByTestId("artifact-degraded-details")).toHaveTextContent(
      "queries[2]：normalized_boolean_syntax",
    );
    expect(screen.getByTestId("artifact-degraded-details")).toHaveTextContent(
      "one query was skipped",
    );
    expect(screen.queryByText(/任务失败/)).not.toBeInTheDocument();
  });
});
