import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import {
  AppstoreOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
  AuditOutlined,
  BulbOutlined,
  CompassOutlined,
  FileSearchOutlined,
  NodeIndexOutlined,
  SafetyCertificateOutlined
} from "@ant-design/icons";
import type { ReleaseGatedStarter } from "@openair/shared";
import { fetchStarters, submitResearchStart } from "../api/client";
import {
  cleanStarterGoalInput,
  createClientRequestId,
  rememberStartSession
} from "../lib/researchStart";
import { cn } from "../lib/cn";

const starterCardClass = cn(
  "rounded-lg border border-border! bg-surface! shadow-sm"
);

const starterIconClass = cn(
  "inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-accent-soft-2 text-2xl text-accent-strong"
);

const starterUsageGuideClass = cn(
  "grid gap-1 rounded-lg border border-border bg-bg p-2.5 text-sm leading-content text-text-secondary [&_strong]:font-semibold [&_strong]:text-text"
);

type DetailMode = "detail" | "create";
type PlazaStageKey = "problem" | "method" | "experiment" | "report";

const PLAZA_STAGE_META: Record<
  PlazaStageKey,
  { label: string; description: string; query: string; icon: JSX.Element }
> = {
  problem: {
    label: "问题建模",
    description: "把模糊方向收敛成可继续推进的问题。",
    query: "我想从材料性质预测里找一个能做下去的研究问题，现在还不确定该看小样本、外推还是数据质量。",
    icon: <CompassOutlined />
  },
  method: {
    label: "方案设计",
    description: "形成实验、筛选或验证路线。",
    query: "含氟添加剂改善锂金属循环，到底是形成了 LiF-rich SEI，还是只改变了沉积形貌？下一轮怎么把这两种解释分开？",
    icon: <BulbOutlined />
  },
  experiment: {
    label: "执行验证",
    description: "检查数据、代码、实验记录和证据边界。",
    query: "我想复现一篇材料性质预测论文，开始跑之前最容易漏掉哪些关键条件？",
    icon: <AuditOutlined />
  },
  report: {
    label: "结果总结",
    description: "整理结论、风险点和后续补证建议。",
    query: "这是报告草稿和结果表，帮我看看有没有结论写得超过数据实际能支持的范围。",
    icon: <FileSearchOutlined />
  }
};

const PLAZA_DIRECTIONS: Array<{
  title: string;
  description: string;
  input: string;
  output: string;
  confirmation: string;
  stage: PlazaStageKey;
  query: string;
  icon: JSX.Element;
}> = [
  {
    title: "关键文献定位",
    description: "只有题目、关键词或种子综述，先收敛检索词、必读论文和争议点。",
    input: "研究主题、关键词、种子论文或综述线索",
    output: "检索入口、候选来源层级、优先阅读理由",
    confirmation: "先确认检索边界，再继续做来源整理或证据抽取。",
    stage: "problem",
    query:
      "我想看看 AI 生成科研假设这个方向现在做到什么程度了，还有没有值得做的问题。",
    icon: <FileSearchOutlined />
  },
  {
    title: "证据整理",
    description: "已有论文、摘录或实验记录，拆出 claim、证据、推断和缺口。",
    input: "论文片段、图注、摘要、实验记录或来源包",
    output: "claim-证据对应表、推断标记、材料缺口",
    confirmation: "先区分证据和作者讨论，再决定哪些结论可引用。",
    stage: "problem",
    query:
      "我放了几篇 Li6PS5Cl 界面的论文，想先看看里面关于 LiNbO3 涂层的结论分别用了什么证据。",
    icon: <AuditOutlined />
  },
  {
    title: "机制争议图谱",
    description: "把已有证据表或来源包串成机制链，标清证据边、推断边和冲突点。",
    input: "证据表、来源包、候选机制或冲突观点",
    output: "机制链路、证据边、推断边和冲突点",
    confirmation: "先复核关键证据边，再把图谱用于方案设计。",
    stage: "method",
    query:
      "钙钛矿样品的 PL 提高了，但器件效率没变。我想看看现有的缺陷钝化解释是不是缺了关键一环。",
    icon: <SafetyCertificateOutlined />
  },
  {
    title: "研究推进计划",
    description: "把模糊研究目标拆成阶段、输入、产物、确认点和下一步。",
    input: "研究目标、已有材料、时间约束和期望产物",
    output: "阶段计划、输入清单、产物定义和人工确认点",
    confirmation: "先生成可审阅草案，确认后再推进具体执行。",
    stage: "method",
    query:
      "我想研究 OPE 方法在 policy 训练过程中会不会失效，但现在还只是个想法，不知道该先补文献还是先搭实验。",
    icon: <BulbOutlined />
  }
];

const PLAZA_SAMPLE_QUERIES: Array<{
  title: string;
  description: string;
  stage: PlazaStageKey;
  query: string;
  icon: JSX.Element;
  sampleStarterId?: string;
  disabledReason?: string;
}> = [
  {
    title: "GraphRAG 科研假说文献综述路径",
    description: "查看从真实输入到检索策略、来源表、推荐阅读和短综述草案的样例路径。",
    stage: "problem",
    query:
      "GraphRAG 用来生成科研假设到底靠不靠谱？我想先看看有没有认真评估过 unsupported claims，而不只是展示几个成功案例。",
    icon: <FileSearchOutlined />,
    sampleStarterId: "project_research_direction_review_v0_3"
  },
  {
    title: "类器官药敏预测的机制证据整理",
    description: "查看如何把一个宽泛目标拆成阶段、材料缺口、确认点和可继续推进的任务草案。",
    stage: "problem",
    query:
      "我最近在看结直肠癌类器官预测西妥昔单抗响应，想先弄清楚这个方向有没有真正做过独立患者队列验证。",
    icon: <SafetyCertificateOutlined />,
    sampleStarterId: "agent_work_plan_v0_3"
  },
  {
    title: "模型训练失败日志与复现实验检查",
    description: "根据代码、数据和日志，定位准备缺口和阻断原因。",
    stage: "experiment",
    query:
      "这些是五次训练的日志和配置，结果波动比论文里大很多，先看看是不是评估方式或数据划分没对齐。",
    icon: <NodeIndexOutlined />,
    disabledReason: "该方向还没有完整真实任务路径样例。"
  },
  {
    title: "材料筛选条件与候选路线生成",
    description: "根据性能目标，生成筛选条件、候选路线和风险点。",
    stage: "method",
    query:
      "想从三元硫族化合物里找一批值得继续算的候选，现在还没想清楚带隙、稳定性和合成可行性怎么一起筛。",
    icon: <CompassOutlined />,
    disabledReason: "该方向的样例仍在整理中。"
  }
];

const PLAZA_RESOURCE_CARDS: Array<{
  title: string;
  description: string;
  icon: JSX.Element;
  query: string;
}> = [
  {
    title: "文献与来源整理",
    description: "检索、筛选、边界",
    icon: <FileSearchOutlined />,
    query:
      "我想研究代码大模型的可靠性，先看看除了 HumanEval 还有哪些更接近真实项目的评测。"
  },
  {
    title: "证据链与风险",
    description: "冲突、缺口、风险",
    icon: <SafetyCertificateOutlined />,
    query: "同一个材料体系，有的文章说添加剂改善了界面，有的反而说阻抗变大，这种冲突一般怎么梳理？"
  },
  {
    title: "实验方案拆解",
    description: "对照、变量、步骤",
    icon: <BulbOutlined />,
    query:
      "钙钛矿样品的 PL 提高了，但器件效率没变，这个结果下一步该怎么验证？"
  },
  {
    title: "材料/数据资源",
    description: "辅助筛选与比对",
    icon: <AppstoreOutlined />,
    query: "以 Ag2GeS3（mp-9900）为起点，在 Ag-Ge-S 体系里还有哪些值得关注但实验报道较少的相？"
  }
];

const STARTER_QUERY_EXAMPLES: Record<string, string[]> = {
  project_research_direction_review_v0_3: [
    "我想看看 AI 生成科研假设这个方向现在做到什么程度了，还有没有值得做的问题。",
    "想做固态电池界面，但这个方向太大了，不知道从哪里切进去。",
    "代码智能体现在都在做自动修 bug，我想知道这个方向是真有进展，还是主要在刷 benchmark。"
  ],
  project_paper_support_analysis_v0_3: [
    "我放了 AlphaFold 3 的论文和几篇后续评测，想确认‘能做小分子筛选’这句话到底有没有直接证据。",
    "这几篇 hard carbon 预钠化论文都说能量密度提高了，但有些没把额外钠源算进去，想看看哪些结论还能成立。",
    "我上传了一篇类器官药敏论文，先帮我核对文中‘可以预测临床响应’这个结论实际做到哪一步。"
  ],
  workflow_literature_research_v0_3: [
    "停用 GLP-1 药物以后体重会反弹到什么程度？我想先把随访时间比较长的研究找齐。",
    "我想研究城市内涝预警，先看看现在常用哪些数据和方法。",
    "想了解钙钛矿/硅叠层电池的稳定性，尤其是不同测试条件下的结果该怎么比较。"
  ],
  workflow_source_evidence_prep_v0_3: [
    "这是 Zotero 导出的文献表，里面 arXiv、会议版和期刊版混在一起，先把重复的合并掉。",
    "我整理了一批野火烟霾和早产的论文链接，想先检查哪些是真正的队列研究，哪些只是综述里重复引用的来源。",
    "这份 PubMed 检索结果是类器官药敏相关的，先帮我分出只有题录、能读摘要和能拿到全文的。"
  ],
  agent_work_plan_v0_3: [
    "我想用两周时间把 Li6PS5Cl 和锂金属界面这个方向摸清楚，文献、机制和后面可能做的实验该怎么排？",
    "准备做代码大模型可靠性评测，但现在只有一个大概方向，先帮我拆成能推进的几步。",
    "我们有三个城区的雨量、水位和积水台账，想做内涝预警，第一阶段应该先把什么问题弄清楚？"
  ],
  agent_question_refinement_v0_3: [
    "我想从材料性质预测里找一个能做下去的问题，现在还不确定该看小样本、外推还是数据质量。",
    "我最近在看类器官药敏预测，但模型精度、跨队列泛化和机制解释好像是三件事，我该先问哪一个？",
    "多智能体做科研证据整理这个题有点大，怎么收成一个真正能评估的问题？"
  ],
  agent_gap_identification_v0_3: [
    "我整理了一批 hard carbon 预钠化论文，大家都在报首效提高，但全电池收益这块到底还缺什么证据？",
    "空间转录组里成纤维细胞亚群的命名总对不上，这只是方法差异，还是确实有研究缺口？",
    "OPE 论文大多在固定数据集上比较 estimator，我想知道训练过程中可靠性变化是不是还没人系统做。"
  ],
  agent_validation_design_v0_3: [
    "我想验证医学问答 RAG 的错误到底出在检索还是生成，实验应该怎么拆？",
    "含氟添加剂改善锂金属循环，到底是 LiF-rich SEI 起作用，还是沉积形貌变了？下一轮怎么区分？",
    "我想复现 GraphCast，但算力有限。最小做到什么程度，才能判断实现和论文结果是不是对得上？"
  ],
  agent_result_audit_v0_3: [
    "这是五次训练的日志、配置和 metrics。论文报 best run，我们算的是 mean±std，先看看结果差距是不是出在这里。",
    "Figure 2b 我按 supplementary 的公式重算不出来，CSV 和 notebook 都放进来了，先查单位和归一化。",
    "我上传了 XPS 峰位表和 EIS 结果，没有 raw data。现阶段哪些机制结论还能说，哪些应该先拿掉？"
  ],
  exp_research_task_workflow_planning_v0_2: [
    "我想做一篇 GLP-1 停药后体重反弹的系统综述，现在应该先把哪些问题和范围定下来？",
    "以 mp-9900 为 seed 跑出了 Ag4Ge3S5，后面从近似相排查到合成验证该怎么推进？",
    "我想研究代码生成的可靠性，先帮我排一下文献、benchmark 选择和实验验证的顺序。"
  ],
  workflow_evidence_mechanism_analysis_v0_3: [
    "钙钛矿样品的 PL 提高了，但器件效率没变。我想看看现有的缺陷钝化解释到底缺了哪一环。",
    "Li6PS5Cl 接锂以后阻抗一直涨，涂层和添加剂相关的文章不少，但各自证明到什么程度并不清楚。",
    "铜基 CO2 还原里，表面结构、局部 pH 和 CO 覆盖度都被拿来解释 C2+ 选择性，现有证据更支持哪条链？"
  ],
  agent_literature_research_v0_3: [
    "野火烟霾和早产的研究结论不太一致，想先把真正做过孕期暴露分析的队列研究找出来。",
    "AlphaFold 3 能不能用于小分子筛选？我想优先看真正评估过 ligand ranking 的工作。",
    "硫化物固态电池文章里的堆压差别很大，有没有专门研究堆压对界面和循环影响的论文？"
  ],
  exp_traceable_source_retrieval_v0_2: [
    "这份代码生成评测文献里 arXiv、会议版和扩展期刊版都有，先确认哪些其实是同一项工作。",
    "我搜 GLP-1 停药后体重反弹时混进了很多新闻和二次解读，想把原始试验和随访研究单独找出来。",
    "这批材料论文只有标题和链接，先看看哪些能确认正式出处，哪些还只是无法核对的线索。"
  ],
  agent_evidence_extraction_v0_3: [
    "我放了几篇 LiNbO3 涂层的论文，只看作者实际做过的表征，discussion 里的机制不要当成实验结果。",
    "AlphaFold 3 论文里关于小分子相互作用的结论，分别对应哪些数据集、指标和图表？",
    "这篇类器官药敏论文说模型能预测临床响应，我想把真正支撑这句话的患者数据和验证结果找出来。"
  ],
  exp_evidence_mechanism_chain_v0_2: [
    "hard carbon 预钠化提高首效以后，为什么全电池能量密度不一定提高？想把中间每一步的证据连起来。",
    "钙钛矿钝化以后 PL 寿命变长，但器件效率没变，这条机制链到底断在哪里？",
    "PFAS 传感器从标准溶液换到真实水样就失效，可能的基质干扰、吸附和电极污染怎么串起来看？"
  ],
  agent_summary_v0_3: [
    "这六篇论文是我这周读的，想先整理成一页组会摘要，别把几篇文章的结论混在一起。",
    "这份报告太长了，先帮我压成能快速判断结果、限制和下一步的版本。",
    "我放了几篇 GLP-1 停药后的研究，先总结目前到底知道什么、哪些问题还没有数据。"
  ],
  agent_artifact_review_v0_3: [
    "这是证据表和报告草稿，先看看有没有结论找不到原文位置，或者引用的图其实不支持正文说法。",
    "这份结果审计里有几项写着通过，但我没看到对应日志，帮我重新核一下。",
    "我准备把这份 XRD 判相结果放进汇报，先确认竞争相和未匹配峰有没有被忽略。"
  ],
  agent_presentation_guidance_v0_3: [
    "下周组会要讲这轮实验，正结果和失败记录都在这里，想按同一套口径整理，不要只挑好看的。",
    "我要汇报 OPE 在不同训练阶段的结果，怎么讲才能把 estimator 失效和数据 coverage 变化分清楚？",
    "这批文献已经整理过一轮了，帮我排一个组会讲述顺序，重点放在真正有分歧的地方。"
  ],
  exp_mmp_platform_training_v0_2: [
    "材料性质预测的 repo、数据说明和训练命令都在这里，开跑前先看看数据单位、划分和指标脚本有没有对齐。",
    "我准备训练类器官药敏模型，但代码入口、数据字段和评估脚本是不同人给的，想先做一次启动检查。",
    "GraphCast 的示例能跑，自定义 ERA5 切片还没跑通。先检查变量、时间分辨率和归一化是不是匹配。"
  ],
  skill_category_find_sources_v0_3: [
    "代码生成评测除了 HumanEval 还有哪些更接近真实项目？我比较关心跨文件修改和安全问题。",
    "hard carbon 预钠化放到全电池以后到底值不值？想先找真正计算过系统能量密度的文章。",
    "空间转录组里的成纤维细胞亚群总对不上，想找一些专门比较不同方法或队列的研究。"
  ],
  skill_category_screen_priorities_v0_3: [
    "这批类器官药敏论文太多了，我现在只想优先看真正做过独立患者队列验证的。",
    "扩散模型做蛋白设计的文章先筛一轮，我更关心做过湿实验的，不想只看结构指标。",
    "野火烟霾和早产的候选文献里，先保留暴露窗口和结局定义比较清楚的队列研究。"
  ],
  skill_category_organize_evidence_v0_3: [
    "我想比较 lecanemab 和 donanemab，但两项试验的人群、终点和治疗时间不一样，先把能比和不能比的证据分开。",
    "铜基 CO2 还原这些文章的电解槽、归一化和碳平衡都不统一，先整理成能横向看的证据表。",
    "Li6PS5Cl 界面这几篇论文里，模拟、综述引用和真实实验结果混在一起，想先分开。"
  ],
  skill_category_mechanism_map_v0_3: [
    "LiI 加进去以后，界面组成、阻抗和循环都变了，但这三件事之间是不是因果关系还不清楚。",
    "钙钛矿钝化以后 PL 变强、寿命变长，但器件效率没动，现有机制图应该怎么改？",
    "肠道菌群和药物响应之间的文章很多，哪些边只有相关性，哪些已经有干预证据？"
  ],
  skill_category_check_experiments_v0_3: [
    "我准备复现一篇 GNN 材料性质预测论文，代码和数据都找到了，开跑前最容易漏掉哪些条件？",
    "这是上周失败的合成记录。下一炉开始前，先看看温区、保温和冷却设置里有没有明显问题。",
    "医学 RAG 的评测准备开跑了，先检查数据集、检索库版本和引用正确性指标是不是都能复现。"
  ],
  skill_category_manage_progress_v0_3: [
    "材料性质预测这个课题现在小样本、外推和数据质量都想做，但肯定不能一起开，先帮我排个优先级。",
    "固态电池界面这块文献、表征和实验想法很多，想整理成接下来两周真正能推进的事情。",
    "城市内涝预警现在只有三个区的数据，缺失也不少。先安排一下数据检查、问题定义和基线验证的顺序。"
  ]
};

const MODE_CTA: Record<
  ReleaseGatedStarter["creationMode"],
  { okText: string; title: string; intro: string }
> = {
  create_real_task: {
    okText: "创建真实任务",
    title: "创建真实任务",
    intro: "当前起点可以直接创建真实任务；创建后会进入工作台继续推进。"
  },
  create_task_draft: {
    okText: "创建任务草案",
    title: "创建任务草案",
    intro: "当前起点会先创建可审阅的任务草案，确认后再继续推进。"
  },
  create_workflow_draft_then_confirm: {
    okText: "生成可确认草案",
    title: "生成工作草案",
    intro: "当前起点会先生成研究起点草案；你确认后再进入后续科研步骤。"
  },
  requires_existing_inputs: {
    okText: "查看需要补充的输入",
    title: "补充已有输入",
    intro:
      "当前起点需要已有来源包、证据材料或上游任务上下文；材料不足时只返回缺口和下一步。"
  },
  prep_check_only: {
    okText: "检查准备条件",
    title: "准备检查",
    intro: "当前起点只做准备检查或来源准备，不会伪装成已创建完整真实任务。"
  }
};

function modalCopyFor(starter: ReleaseGatedStarter | null) {
  if (!starter) {
    return {
      okText: "创建真实任务",
      title: "创建真实任务",
      intro: "写下一句真实科研问题后，会创建任务并进入工作台。"
    };
  }
  if (starter.starterId === "agent_literature_research_v0_3") {
    return {
      okText: "创建启动草案",
      title: "创建关键文献定位草案",
      intro:
        "当前起点会先创建可确认的关键文献定位草案；确认前只整理研究对象、检索边界和预期产物，不会执行真实检索。"
    };
  }
  return (
    MODE_CTA[starter.creationMode] ??
    MODE_CTA.create_workflow_draft_then_confirm
  );
}

const FEATURED_COPY: Record<
  string,
  {
    title: string;
    description: string;
    tag: string;
    icon: JSX.Element;
    scenario: string;
    boundary: string;
  }
> = {
  agent_literature_research_v0_3: {
    title: "关键文献定位 智能体",
    description:
      "从题目、关键词或种子综述出发，整理检索词簇、候选论文分层、必读理由和主要争议点。",
    tag: "文献定位",
    icon: <FileSearchOutlined />,
    scenario:
      "适合刚接到方向、只有少量关键词或两三篇综述，需要先确定怎么搜、先读什么、争议在哪里的人。",
    boundary:
      "只给检索和阅读路径；不声称已经完成 live 检索、全文阅读或穷尽综述。"
  },
  agent_evidence_extraction_v0_3: {
    title: "论文证据抽取 智能体",
    description:
      "从全文、摘录、图注或实验记录中拆出 claim、证据、推断、不确定性和缺失材料。",
    tag: "证据抽取",
    icon: <AuditOutlined />,
    scenario:
      "适合已经有论文材料或实验记录，需要判断每条结论到底由哪些实验、图表或数据支撑的人。",
    boundary: "需要可读材料；只有题目、题录或摘要时不会输出逐条全文证据。"
  },
  exp_evidence_mechanism_chain_v0_2: {
    title: "机制争议图谱 智能体",
    description:
      "把已有证据表或来源包整理成机制链、证据边、推断边、冲突解释和验证缺口。",
    tag: "机制争议",
    icon: <SafetyCertificateOutlined />,
    scenario:
      "适合已有一批证据或来源包，但机制解释互相打架，需要区分证据、推断和缺口的人。",
    boundary:
      "每条机制边必须能追溯到 evidence ID 或标为 inference/gap；topic-only 不给机制结论。"
  },
  agent_work_plan_v0_3: {
    title: "研究推进计划 智能体",
    description:
      "把模糊研究目标拆成阶段计划、输入清单、产物定义、确认点、风险和下一步动作。",
    tag: "推进计划",
    icon: <BulbOutlined />,
    scenario:
      "适合把开题、读文献、复现、证据整理等模糊目标变成可确认执行草案的人。",
    boundary:
      "确认前不自动执行检索、实验、训练或其他高成本步骤，也不把计划当完成结果。"
  },
  agent_summary_v0_3: {
    title: "科研材料总结 智能体",
    description:
      "把论文摘要、摘录、报告片段或已有产物压缩成科研人员可读的短摘要，并保留来源边界。",
    tag: "材料总结",
    icon: <FileSearchOutlined />,
    scenario:
      "适合已有论文片段、报告草稿、会议记录或系统产物，需要先看人话摘要，再展开详细 Markdown 的场景。",
    boundary:
      "必须先有材料；不会从空主题生成总结，也不会把摘要当作证据复核或质量背书。"
  },
  agent_artifact_review_v0_3: {
    title: "产物复核 智能体",
    description:
      "检查已有 artifact、报告草稿、证据表、结果或日志是否可继续使用，并指出需要修改的地方。",
    tag: "产物复核",
    icon: <AuditOutlined />,
    scenario:
      "适合在汇报、提交或继续分析前，先判断已有产物是否有足够证据、日志、来源记录和复核标准支撑。",
    boundary:
      "缺少可复核对象或记录时不做通过/不通过判定，也不会替缺失材料背书。"
  },
  agent_presentation_guidance_v0_3: {
    title: "汇报整理 智能体",
    description:
      "把已有结果、证据表、报告草稿或产物整理成汇报、PPT、报告或海报草案。",
    tag: "汇报整理",
    icon: <BulbOutlined />,
    scenario:
      "适合已经有结果和材料，需要组织成组会、PPT、报告或海报结构，并标出哪些结论不能夸大的场景。",
    boundary:
      "只组织已有材料，不重新做研究，不创造不存在的结果；确认前不发布为最终报告。"
  }
};

function iconFor(starter: ReleaseGatedStarter) {
  if (starter.starterId in FEATURED_COPY)
    return FEATURED_COPY[starter.starterId].icon;
  if (starter.category === "Project") return <CompassOutlined />;
  if (starter.category === "Workflow") return <NodeIndexOutlined />;
  if (starter.category === "Agent") return <BulbOutlined />;
  if (starter.productFamily === "evidence_mechanisms")
    return <SafetyCertificateOutlined />;
  return <AppstoreOutlined />;
}

function compactDescription(text: string) {
  return text.length > 54 ? `${text.slice(0, 52)}...` : text;
}

function cleanPlazaCopy(value: string): string {
  return value
    .replace(/\bAgent\b/gi, "智能体")
    .replace(/\bWorkflow\b/gi, "工作流模板")
    .replace(/\bSkill\s*Pack\b/gi, "技能包")
    .replace(/\bSkill\b/gi, "技能")
    .replace(/\bSDR\b|\bOPE\b|\bOpenTrajectory\b|\bMaterials\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function plazaStageForStarter(starter: ReleaseGatedStarter): PlazaStageKey {
  const text =
    `${starter.starterId} ${starter.title} ${starter.description} ${starter.productFamily}`.toLowerCase();
  if (/report|summary|总结|报告|写作|归纳/.test(text)) return "report";
  if (/training|experiment|check|复核|检查|训练|运行记录|复现|验证/.test(text))
    return "experiment";
  if (/screen|design|plan|材料|筛选|实验方案|路线|候选/.test(text))
    return "method";
  return "problem";
}

function plazaStageTag(starter: ReleaseGatedStarter) {
  return PLAZA_STAGE_META[plazaStageForStarter(starter)].label;
}

function displayTitleFor(starter: ReleaseGatedStarter) {
  return FEATURED_COPY[starter.starterId]?.title ?? starter.title;
}

function plazaTitleFor(starter: ReleaseGatedStarter): string {
  return cleanPlazaCopy(displayTitleFor(starter));
}

function plazaDescriptionFor(starter: ReleaseGatedStarter): string {
  const featured = FEATURED_COPY[starter.starterId];
  return compactDescription(
    cleanPlazaCopy(
      featured?.scenario ?? featured?.description ?? starter.description
    )
  );
}

function plazaQueryFor(starter: ReleaseGatedStarter): string {
  const examples = STARTER_QUERY_EXAMPLES[starter.starterId];
  if (examples?.[0]) return examples[0];
  const title = plazaTitleFor(starter);
  return `我想用“${title}”开始。请先问我研究对象、已有材料和想得到的结果。`;
}

function usageGuideFor(starter: ReleaseGatedStarter) {
  return (
    starter.usageGuide ?? {
      suitableFor: starter.description,
      input: `输入：${starter.requiredInputs.join("、")}`,
      output: `输出：${starter.outputList.join("、")}`,
      boundary: starter.boundaryCopy
    }
  );
}

const SAMPLE_DISABLED_TEXT = "样例整理中";
const SAMPLE_DISABLED_TIP = "该起点还没有完整真实任务样例，暂不开放查看。";
const VERIFIED_SAMPLE_STARTER_IDS = new Set([
  "project_research_direction_review_v0_3",
  "agent_work_plan_v0_3"
]);

const SYSTEM_AGENT_ENTRY_IDS = [
  "agent_summary_v0_3",
  "agent_artifact_review_v0_3",
  "agent_presentation_guidance_v0_3"
] as const;

const CURATED_RESEARCH_AGENT_IDS = [
  ...SYSTEM_AGENT_ENTRY_IDS,
  "agent_literature_research_v0_3",
  "agent_evidence_extraction_v0_3",
  "exp_evidence_mechanism_chain_v0_2",
  "agent_work_plan_v0_3",
  "agent_question_refinement_v0_3",
  "agent_gap_identification_v0_3",
  "agent_validation_design_v0_3",
  "agent_result_audit_v0_3"
] as const;

export default function StarterRegistryPage() {
  const navigate = useNavigate();
  const [starters, setStarters] = useState<ReleaseGatedStarter[]>([]);
  const [loading, setLoading] = useState(true);
  const [usingId, setUsingId] = useState<string | null>(null);
  const [focusedStarter, setFocusedStarter] =
    useState<ReleaseGatedStarter | null>(null);
  const [focusedMode, setFocusedMode] = useState<DetailMode>("detail");
  const [createStarter, setCreateStarter] =
    useState<ReleaseGatedStarter | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [goalSummary, setGoalSummary] = useState("");
  const [expandedUsageIds, setExpandedUsageIds] = useState<Set<string>>(
    () => new Set()
  );
  const activeStartRequestIdRef = useRef<string | null>(null);
  const currentIntentSessionIdRef = useRef<string | null>(null);

  const resetStartContinuation = () => {
    currentIntentSessionIdRef.current = null;
  };

  const rememberSubmittedStartSession = (input: {
    rawInput: string;
    sourcePage: "home" | "board" | "starter_plaza" | "sample";
    presetQuery?: string;
    sessionId: string;
  }) => {
    currentIntentSessionIdRef.current = input.sessionId;
    rememberStartSession(input);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchStarters()
      .then(items => {
        if (!cancelled) setStarters(items);
      })
      .catch(err => {
        const detail = err instanceof Error ? err.message : "无法加载科研起点";
        message.error(detail);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const plazaStarterPreview = useMemo(() => {
    const curated = CURATED_RESEARCH_AGENT_IDS.map(starterId =>
      starters.find(starter => starter.starterId === starterId)
    ).filter((starter): starter is ReleaseGatedStarter => Boolean(starter));
    const seen = new Set(curated.map(starter => starter.starterId));
    return [
      ...curated,
      ...starters.filter(starter => !seen.has(starter.starterId))
    ].slice(0, 12);
  }, [starters]);

  const openHomeWithQuery = (query?: string) => {
    const nextParams: Record<string, string> = { intent: "create" };
    if (query?.trim()) nextParams.query = query.trim();
    const nextSearch = new URLSearchParams(nextParams).toString();
    navigate({ pathname: "/", search: `?${nextSearch}` });
    setFocusedStarter(null);
    setGoalSummary(query?.trim() ?? "");
    resetStartContinuation();
  };

  const openSampleByStarterId = (starterId: string) => {
    navigate(`/starter-samples/${encodeURIComponent(starterId)}`);
  };

  const openSample = (starter: ReleaseGatedStarter) => {
    if (
      !starter.verifiedSample &&
      !VERIFIED_SAMPLE_STARTER_IDS.has(starter.starterId)
    ) {
      return;
    }
    openSampleByStarterId(starter.starterId);
  };

  const openCreateDialog = (starter: ReleaseGatedStarter | null) => {
    setCreateStarter(starter);
    setCreateModalOpen(true);
    setGoalSummary(starter ? plazaQueryFor(starter) : "");
    resetStartContinuation();
  };

  const closeCreateDialog = () => {
    setCreateModalOpen(false);
    setGoalSummary("");
    resetStartContinuation();
  };

  const openFocus = (starter: ReleaseGatedStarter, mode: DetailMode) => {
    if (mode === "create") {
      openCreateDialog(starter);
      return;
    }
    setFocusedStarter(starter);
    setFocusedMode(mode);
    window.setTimeout(() => {
      document
        .querySelector("[data-starter-focus-panel]")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 0);
  };

  const handleConfirmCreate = async (options?: {
    starter?: ReleaseGatedStarter | null;
  }) => {
    if (usingId || activeStartRequestIdRef.current) {
      message.info("正在判断下一步，请稍候。");
      return;
    }
    const cleanedGoal = goalSummary.trim();
    if (!cleanedGoal) {
      message.warning("请先用一句话描述你的科研问题或想推进的任务。");
      return;
    }
    const starter =
      options && "starter" in options
        ? (options.starter ?? null)
        : createStarter;
    const actionId = starter?.starterId ?? "__direct_draft__";
    setUsingId(actionId);
    const clientRequestId = createClientRequestId();
    activeStartRequestIdRef.current = clientRequestId;
    try {
      const cleaned = cleanStarterGoalInput(goalSummary);
      const input = cleaned || goalSummary.trim();
      const result = await submitResearchStart({
        input,
        sourcePage: "starter_plaza",
        selectedHint: starter?.starterId,
        clientRequestId
      });
      rememberSubmittedStartSession({
        rawInput: input,
        sourcePage: "starter_plaza",
        sessionId: result.sessionId
      });
      setCreateModalOpen(false);
      navigate(`/research-start/waiting/${result.sessionId}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "启动判断失败");
    } finally {
      setUsingId(null);
      activeStartRequestIdRef.current = null;
    }
  };

  const toggleUsageGuide = (id: string) => {
    setExpandedUsageIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderSampleButton = (starter: ReleaseGatedStarter, block = false) => {
    if (
      starter.verifiedSample ||
      VERIFIED_SAMPLE_STARTER_IDS.has(starter.starterId)
    ) {
      return (
        <Button
          block={block}
          type="default"
          onClick={() => openSample(starter)}
          data-testid="starter-card-sample"
        >
          查看样例
        </Button>
      );
    }
    return (
      <Tooltip title={starter.sampleDisabledReason ?? SAMPLE_DISABLED_TIP}>
        <Button
          block={block}
          disabled
          title={starter.sampleDisabledReason ?? SAMPLE_DISABLED_TIP}
          data-testid="starter-card-sample-disabled"
        >
          {SAMPLE_DISABLED_TEXT}
        </Button>
      </Tooltip>
    );
  };

  const renderUsageGuide = (starter: ReleaseGatedStarter, compact = false) => {
    const guide = usageGuideFor(starter);
    const expanded = expandedUsageIds.has(starter.starterId);
    return (
      <div className={cn("grid gap-1.5", compact && "mt-2")}>
        <Button
          type="link"
          size="small"
          className="h-auto! w-max p-0! text-sm"
          onClick={() => toggleUsageGuide(starter.starterId)}
          aria-expanded={expanded}
        >
          {expanded ? "收起说明" : "使用说明"}
        </Button>
        {expanded ? (
          <div
            className={
              compact
                ? cn(
                    starterUsageGuideClass,
                    "mt-2.5 px-2.5 py-2 text-xs leading-normal"
                  )
                : starterUsageGuideClass
            }
          >
            <div>
              <strong>适合：</strong>
              {guide.suitableFor}
            </div>
            <div>
              <strong>输入：</strong>
              {guide.input.replace(/^输入：/, "")}
            </div>
            <div>
              <strong>输出：</strong>
              {guide.output.replace(/^输出：/, "")}
            </div>
            <div>
              <strong>边界：</strong>
              {guide.boundary}
            </div>
          </div>
        ) : null}
      </div>
    );
  };

  const renderCreateModal = () => {
    const featured = createStarter
      ? FEATURED_COPY[createStarter.starterId]
      : null;
    const starterTitle = createStarter
      ? (featured?.title ?? createStarter.title)
      : "科研问题起步";
    const modeCopy = modalCopyFor(createStarter);
    const queryExamples = createStarter
      ? (STARTER_QUERY_EXAMPLES[createStarter.starterId] ?? [])
      : [];
    return (
      <Modal
        title={modeCopy.title}
        open={createModalOpen}
        onCancel={closeCreateDialog}
        onOk={() => void handleConfirmCreate()}
        okText={modeCopy.okText}
        cancelText="取消"
        confirmLoading={
          usingId === (createStarter?.starterId ?? "__direct_draft__")
        }
        okButtonProps={{ disabled: Boolean(usingId) }}
        destroyOnClose
        afterClose={() => {
          setGoalSummary("");
        }}
      >
        <Space direction="vertical" size={12} className="mt-2 w-full">
          <Alert
            type="info"
            showIcon
            message="先用一句话开始"
            description={`当前起点：${starterTitle}。${modeCopy.intro} 样例只用于了解用法，不会写入真实任务列表。`}
          />
          {createStarter ? renderUsageGuide(createStarter) : null}
          {queryExamples.length > 0 ? (
            <div
              className="grid gap-2 rounded-lg border border-border bg-surface-soft p-2.5 [&_.ant-btn]:h-auto [&_.ant-btn]:min-h-7 [&_.ant-btn]:whitespace-normal [&_.ant-btn]:text-left"
              data-testid="starter-query-examples"
            >
              <Typography.Text strong>可点选示例</Typography.Text>
              <Space wrap size={[8, 8]}>
                {queryExamples.map((example, index) => (
                  <Button
                    key={example}
                    type="default"
                    size="small"
                    onClick={() => setGoalSummary(example)}
                    data-testid={`starter-query-example-${index + 1}`}
                  >
                    示例 {index + 1}
                  </Button>
                ))}
              </Space>
              <Typography.Paragraph
                type="secondary"
                className="m-0 rounded-md bg-surface p-2.5 wrap-anywhere"
              >
                {goalSummary || queryExamples[0]}
              </Typography.Paragraph>
            </div>
          ) : null}
          <Input.TextArea
            rows={4}
            maxLength={500}
            showCount
            placeholder="请描述你的科研问题或想推进的任务"
            value={goalSummary}
            onChange={event => setGoalSummary(event.target.value)}
            aria-label="请描述你的科研问题或想推进的任务"
          />
        </Space>
      </Modal>
    );
  };

  const renderFocusPanel = () => {
    if (!focusedStarter) return null;
    const featured = FEATURED_COPY[focusedStarter.starterId];
    const title = plazaTitleFor(focusedStarter);
    return (
      <Card
        variant="borderless"
        className={cn(
          starterCardClass,
          "my-3.5",
          "[&_.ant-card-body]:flex [&_.ant-card-body]:justify-between [&_.ant-card-body]:gap-5 [&_.ant-card-body]:px-[22px]! [&_.ant-card-body]:py-5!",
          "max-[980px]:[&_.ant-card-body]:flex-col"
        )}
        data-starter-focus-panel
        data-testid={`starter-${focusedMode}-panel`}
      >
        <div className="flex min-w-0 flex-1 gap-4">
          <div className={starterIconClass}>{iconFor(focusedStarter)}</div>
          <div className="grid min-w-0 max-w-[760px] gap-2.5 [&_.ant-typography]:m-0 [&_.ant-typography]:leading-copy-loose [&_.ant-typography]:text-text-secondary [&_h4.ant-typography]:m-0">
            <Space wrap size={8}>
              <Tag>{plazaStageTag(focusedStarter)}</Tag>
              {focusedStarter.verifiedSample ? (
                <Tag color="green">有样例</Tag>
              ) : null}
            </Space>
            <Typography.Title level={4}>{title}</Typography.Title>
            {focusedMode === "create" ? (
              <>
                <Typography.Paragraph>
                  这个方向会回到首页输入框，你可以修改后再开始。
                </Typography.Paragraph>
                <Input.TextArea
                  rows={3}
                  maxLength={500}
                  showCount
                  placeholder="请描述你的科研问题或想推进的任务"
                  value={goalSummary || plazaQueryFor(focusedStarter)}
                  onChange={event => setGoalSummary(event.target.value)}
                />
              </>
            ) : (
              <>
                <Typography.Paragraph>
                  {plazaDescriptionFor(focusedStarter)}
                </Typography.Paragraph>
                <Typography.Text type="secondary">
                  {cleanPlazaCopy(
                    featured?.boundary ??
                      "开始前会先确认目标、已有材料和边界；不会直接执行高成本步骤。"
                  )}
                </Typography.Text>
              </>
            )}
          </div>
        </div>
        <Space wrap className="shrink-0 self-end">
          {focusedMode === "detail" ? (
            <Button
              type="primary"
              onClick={() => openFocus(focusedStarter, "create")}
            >
              用这个方向开始
            </Button>
          ) : (
            <Button
              type="primary"
              loading={usingId === focusedStarter.starterId}
              onClick={() =>
                void handleConfirmCreate({ starter: focusedStarter })
              }
              data-testid={`starter-submit-${focusedStarter.starterId}`}
            >
              提交这个起点
            </Button>
          )}
          {renderSampleButton(focusedStarter)}
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => setFocusedStarter(null)}
          >
            收起
          </Button>
        </Space>
      </Card>
    );
  };

  const renderPlazaThumb = (
    icon: JSX.Element,
    tone: "blue" | "purple" | "green" | "slate" = "purple"
  ) => {
    const toneClass = {
      blue: "from-[#f4f2ff] via-white to-[#e7e3ff] text-[#594aff]",
      purple: "from-[#f4f2ff] via-white to-[#e7e3ff] text-[#594aff]",
      green: "from-[#f7f6ff] via-white to-[#eeebff] text-[#6b5cf0]",
      slate: "from-[#faf9ff] via-white to-[#f0eeff] text-[#6b5cf0]"
    }[tone];
    return (
      <div
        className={cn(
          "relative flex h-[116px] w-full items-center justify-center overflow-hidden rounded bg-gradient-to-br",
          toneClass
        )}
      >
        <div className="absolute left-5 top-5 h-12 w-16 rounded border border-white/80 bg-white/80 shadow-sm" />
        <div className="absolute right-7 top-7 h-10 w-14 rounded border border-white/80 bg-white/70 shadow-sm" />
        <div className="absolute bottom-5 left-1/2 h-px w-28 -translate-x-1/2 bg-current/20" />
        <span className="relative z-10 inline-flex h-12 w-12 items-center justify-center rounded-lg bg-white text-2xl shadow-sm">
          {icon}
        </span>
      </div>
    );
  };

  const renderPlazaCard = (starter: ReleaseGatedStarter) => (
    <button
      key={starter.starterId}
      type="button"
      className="flex min-h-[120px] appearance-none flex-col items-start gap-3 rounded-xl border border-[#f0f2f6] bg-white p-4 text-left shadow-none transition-colors hover:border-[#594aff] focus:outline-none focus:ring-2 focus:ring-[#594aff]/30"
      onClick={() => openFocus(starter, "detail")}
      data-starter-id={starter.starterId}
    >
      <div className="flex w-full min-w-0 items-start justify-between gap-2">
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#f4f2ff] text-[#594aff]">
          {iconFor(starter)}
        </span>
        <Tag>{plazaStageTag(starter)}</Tag>
      </div>
      <div className="min-w-0">
        <Typography.Text strong className="block text-base text-[#1e1e1e]">
          {plazaTitleFor(starter)}
        </Typography.Text>
        <Typography.Paragraph className="mb-0 mt-1 line-clamp-2 text-sm leading-copy text-[#5e5e5e]">
          {plazaDescriptionFor(starter)}
        </Typography.Paragraph>
      </div>
    </button>
  );

  const renderStageCard = (stage: PlazaStageKey) => (
    <button
      key={stage}
      type="button"
      className="flex min-h-[76px] appearance-none flex-col gap-2 rounded-xl border border-transparent bg-white p-4 text-left shadow-none transition-colors hover:border-[#594aff] hover:text-[#594aff] focus:outline-none focus:ring-2 focus:ring-[#594aff]/30"
      onClick={() => openHomeWithQuery(PLAZA_STAGE_META[stage].query)}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2 text-base font-semibold text-[#1e1e1e]">
          <span className="text-[#594aff]">{PLAZA_STAGE_META[stage].icon}</span>
          <span>{PLAZA_STAGE_META[stage].label}</span>
        </span>
        <ArrowRightOutlined className="text-xs text-[#8e8e8e]" />
      </div>
      <Typography.Text className="pl-6 text-xs leading-normal text-[#5e5e5e]">
        {PLAZA_STAGE_META[stage].description}
      </Typography.Text>
    </button>
  );

  const renderDirectionCard = (
    direction: (typeof PLAZA_DIRECTIONS)[number],
    index: number
  ) => (
    <Card
      key={direction.title}
      variant="borderless"
      className="h-full rounded-xl border border-[#f6f7fa]! bg-white! shadow-none [&_.ant-card-body]:flex [&_.ant-card-body]:h-full [&_.ant-card-body]:flex-col [&_.ant-card-body]:gap-4 [&_.ant-card-body]:p-5!"
    >
      <div className="flex min-w-0 items-center gap-2">
        <Typography.Title level={5} className="m-0! text-xl! text-[#1e1e1e]!">
          {direction.title}
        </Typography.Title>
        <Tag>{PLAZA_STAGE_META[direction.stage].label}</Tag>
      </div>
      <Typography.Paragraph className="m-0! min-h-10 text-sm leading-copy text-[#5e5e5e]">
        {direction.description}
      </Typography.Paragraph>
      <div className="grid gap-2 rounded-lg bg-[#f7f9fc] p-3 text-sm leading-normal text-[#4b5563]">
        <div>
          <Typography.Text strong className="text-[#1e1e1e]">
            需要输入：
          </Typography.Text>
          {direction.input}
        </div>
        <div>
          <Typography.Text strong className="text-[#1e1e1e]">
            预计产物：
          </Typography.Text>
          {direction.output}
        </div>
        <Typography.Text className="text-xs leading-normal text-[#6b7280]">
          {direction.confirmation}
        </Typography.Text>
      </div>
      <Button
        type="primary"
        className="h-10! w-full rounded bg-[#594aff]!"
        onClick={() => openHomeWithQuery(direction.query)}
      >
        从此方向开始
      </Button>
      {renderPlazaThumb(direction.icon, index % 2 === 0 ? "purple" : "blue")}
    </Card>
  );

  const renderSampleQueryCard = (
    sample: (typeof PLAZA_SAMPLE_QUERIES)[number],
    index: number
  ) => (
    <Card
      key={sample.title}
      variant="borderless"
      className="h-full rounded-xl border border-[#f6f7fa]! bg-white! shadow-none [&_.ant-card-body]:flex [&_.ant-card-body]:h-full [&_.ant-card-body]:items-start [&_.ant-card-body]:gap-5 [&_.ant-card-body]:p-5!"
      data-testid="starter-sample-card"
    >
      <span
        className={cn(
          "inline-flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded bg-gradient-to-br text-2xl",
          index % 2 === 0
            ? "from-[#f4f2ff] to-[#e7e3ff] text-[#594aff]"
            : "from-[#f4f2ff] to-[#e7e3ff] text-[#594aff]"
        )}
      >
        {sample.icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-3">
        <span className="flex min-w-0 items-start justify-between gap-3">
          <Typography.Text strong className="text-lg text-[#1e1e1e]">
            {sample.title}
          </Typography.Text>
        </span>
        <Typography.Text className="mt-1 block text-sm leading-copy text-[#5e5e5e]">
          {sample.description}
        </Typography.Text>
        <span className="flex flex-wrap gap-2">
          <Tag>{PLAZA_STAGE_META[sample.stage].label}</Tag>
          {sample.sampleStarterId ? (
            <Tag color="green">可查看样例</Tag>
          ) : (
            <Tag>样例暂未开放</Tag>
          )}
        </span>
        <Space wrap>
          {sample.sampleStarterId ? (
            <Button
              type="default"
              onClick={() => openSampleByStarterId(sample.sampleStarterId!)}
              data-testid="starter-sample-view"
            >
              查看样例
            </Button>
          ) : (
            <Tooltip title={sample.disabledReason ?? SAMPLE_DISABLED_TIP}>
              <Button disabled data-testid="starter-sample-unavailable">
                样例暂未开放
              </Button>
            </Tooltip>
          )}
          <Button
            type="primary"
            className="bg-[#594aff]!"
            onClick={() => openHomeWithQuery(sample.query)}
            data-testid="starter-sample-copy"
          >
            复制为任务
            <ArrowRightOutlined />
          </Button>
        </Space>
      </span>
    </Card>
  );

  const renderResourceCard = (
    resource: (typeof PLAZA_RESOURCE_CARDS)[number]
  ) => (
    <button
      key={resource.title}
      type="button"
      className="flex min-h-[92px] appearance-none items-center gap-4 rounded-xl border border-[#f6f7fa] bg-white p-5 text-left shadow-none transition-colors hover:border-[#594aff] focus:outline-none focus:ring-2 focus:ring-[#594aff]/30"
      onClick={() => openHomeWithQuery(resource.query)}
    >
      <span className="inline-flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-lg bg-[#f4f2ff] text-xl text-[#594aff]">
        {resource.icon}
      </span>
      <span className="min-w-0">
        <Typography.Text strong className="block text-base text-[#1e1e1e]">
          {resource.title}
        </Typography.Text>
        <Typography.Text className="mt-1 block text-sm text-[#5e5e5e]">
          {resource.description}
        </Typography.Text>
      </span>
    </button>
  );

  const renderPlazaSectionTitle = (title: string) => (
    <div className="mb-4 flex items-center justify-between gap-4">
      <Typography.Title level={4} className="m-0! text-xl! text-black!">
        {title}
      </Typography.Title>
    </div>
  );

  const renderPlaza = () => (
    <>
      <section className="relative overflow-hidden rounded-xl bg-gradient-to-r from-[#594aff] to-[#a097ff] px-10 py-6 text-white max-[980px]:px-6">
        <div className="relative z-10 max-w-[960px]">
          <Typography.Title className="m-0! text-[40px]! font-semibold! leading-tight! text-white! max-[720px]:text-[30px]!">
            从真实研究场景开始
          </Typography.Title>
          <Typography.Paragraph className="mb-0 mt-4 text-base leading-copy text-white/90">
            浏览研究起点、样例和资源，开始自己的任务时，仍从研究目标输入。
          </Typography.Paragraph>
        </div>
        <div className="absolute right-10 top-1/2 hidden h-[108px] w-[220px] -translate-y-1/2 items-center justify-center rounded-xl bg-white/12 lg:flex">
          <div className="relative h-[76px] w-[150px]">
            <div className="absolute left-0 top-4 h-12 w-20 rounded border border-white/50 bg-white/70 shadow-sm" />
            <div className="absolute right-2 top-0 h-16 w-20 rounded border border-white/50 bg-white/80 shadow-sm" />
            <div className="absolute bottom-0 left-16 h-10 w-10 rounded-full border border-white/60 bg-white/75 shadow-sm" />
          </div>
        </div>
      </section>

      {renderFocusPanel()}

      <Spin spinning={loading}>
        <section className="mt-12">
          {renderPlazaSectionTitle("按研究阶段浏览")}
          <div className="grid grid-cols-4 gap-6 max-[1180px]:grid-cols-2 max-[640px]:grid-cols-1">
            {(Object.keys(PLAZA_STAGE_META) as PlazaStageKey[]).map(
              renderStageCard
            )}
          </div>
        </section>

        <section className="mt-12">
          {renderPlazaSectionTitle("推荐起点")}
          <div className="grid grid-cols-4 gap-6 max-[1320px]:grid-cols-2 max-[720px]:grid-cols-1">
            {PLAZA_DIRECTIONS.map((direction, index) =>
              renderDirectionCard(direction, index)
            )}
          </div>
        </section>

        <section id="starter-plaza-samples" className="mt-12">
          {renderPlazaSectionTitle("研究样例")}
          <div className="grid grid-cols-2 gap-6 max-[980px]:grid-cols-1">
            {PLAZA_SAMPLE_QUERIES.map((sample, index) =>
              renderSampleQueryCard(sample, index)
            )}
          </div>
        </section>

        <section className="mt-12">
          {renderPlazaSectionTitle("资源")}
          <div className="grid grid-cols-4 gap-6 max-[1180px]:grid-cols-2 max-[640px]:grid-cols-1">
            {PLAZA_RESOURCE_CARDS.map(renderResourceCard)}
          </div>
        </section>

        {plazaStarterPreview.length ? (
          <section className="mt-12">
            {renderPlazaSectionTitle("更多起点")}
            <div className="grid grid-cols-4 gap-4 max-[1320px]:grid-cols-3 max-[980px]:grid-cols-2 max-[640px]:grid-cols-1">
              {plazaStarterPreview.map(renderPlazaCard)}
            </div>
          </section>
        ) : null}
      </Spin>
    </>
  );

  return (
    <div
      className="min-h-full bg-[#f7f9fc] p-0!"
      data-testid="starter-plaza-page"
    >
      <main className="w-full px-12 pb-12 pt-2 max-[1320px]:px-8 max-[980px]:px-4 max-[980px]:pb-8 max-[980px]:pt-4">
        {renderPlaza()}
      </main>
      {renderCreateModal()}
    </div>
  );
}
