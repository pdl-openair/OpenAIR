from app.modules.task_draft_builder import build_research_start_draft


def test_research_query_gets_compact_semantic_project_title() -> None:
    query = (
        "调研 2024 年以来 GraphRAG 在科研文献问答中的主要方法与代表性工作，"
        "比较它与传统混合检索 RAG 的效果，并设计一个可复现实验验证两者在答案准确性、"
        "证据覆盖和引用可靠性上的差异。"
    )

    draft = build_research_start_draft(query)

    assert draft.title == "GraphRAG 科研文献问答对比研究"
    assert len(draft.title) <= 20
    assert draft.title not in query


def test_research_start_draft_respects_inline_evidence_materials() -> None:
    draft = build_research_start_draft(
        "我要启动一个证据整理研究任务。材料A：42 个患者来源结直肠癌类器官，"
        "5-FU/奥沙利铂 AUC 与 RECIST 反应相关，r=0.58，p<0.01。"
        "材料B：8 个样本只有剂量反应曲线。"
        "实验记录：6 个 PDO 样本，两个样本重复孔 CV>30%。"
    )

    joined = "\n".join(draft.missing_info)
    assert "已粘贴材料待结构化复核" in joined
    assert "已有论文、数据、代码或实验记录尚未上传" not in joined
    assert "EGFR" not in joined


def test_research_start_draft_does_not_treat_material_mentions_as_pasted_materials() -> None:
    draft = build_research_start_draft(
        "我要启动一个证据整理研究任务。我有实验记录但还没上传，希望先整理证据表和缺口。"
    )

    joined = "\n".join(draft.missing_info)
    assert "已有论文、数据、代码或实验记录尚未上传" in joined
    assert "已粘贴材料待结构化复核" not in joined
