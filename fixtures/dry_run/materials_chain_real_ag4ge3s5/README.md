# materials_chain_real_ag4ge3s5 — Materials Pack 全链路真跑 fixture

> 生成日期:2026-07-03 ~ 2026-07-07
> 故事线:**Ag2GeS3 (mp-9900, MP 种子) → Ag4Ge3S5 (mattergen 演化 novel) → K2S3 flux → XRD 主相 92%**
> 来源:`/tmp/mp_run/materials_chain/`(真跑输出,本 fixture 为自包含副本)

## 与旧 fixture 的区别

| 项 | `fixtures/dry_run/materials_chain/`(旧) | 本 fixture(新) |
|---|---|---|
| 故事线 | CrSiTe3 | **Ag4Ge3S5** |
| 来源 | DRY-RUN 模式手写 | **真跑** + 局部 stub |
| mp_id | mp-676(❌ 实为 CaN6) | mp-9900 / mp-3779(已校对) |
| Stage 1 | fixture 回放 | **MP API + crystal-property-prediction MCP partial 真跑** |
| Stage 2 | fixture 回放 | **mattergen MCP 真跑** |
| Stage 3 | fixture 回放 | **5 步 Pipeline 真跑** |
| Stage 4 | 占位 | **Stub**(L3 待落地) |
| Stage 5 | fixture 回放 | **Fixture 回放**(无本机 Docker) |

## 包含文件

```
materials_chain_real_ag4ge3s5/
├── 01_mp_seeds.md             # Stage 1a:16 passing,top=mp-9900 Ag2GeS3 CL=0.886
├── 02_novel_candidates.md     # Stage 1b:Pareto 2 唯一(gen002_ind_001 Ag4Ge3S5)
├── 03_flux_parameters.md      # Stage 2 :K2S3 reactive flux,5 套方案
├── 04_synthesis_log.md        # Stage 3 :Stub,304h timeline,0.38g/38%
├── 05_xrd_report.md           # Stage 4 :主相 92% + 副相 Ag2S 5%,Rwp=4.8%
├── cifs/
│   ├── gen002_ind_001_Ag4Ge3S5.cif   # 12 sites, sg=1 P1(mattergen 演化产物)
│   └── gen004_ind_000_Ag4Ge3S5.cif   # 12 sites, sg=1 P1(Pareto 备选)
└── README.md(本文件)
```

## 字段契约完整性

全 5 substage 的 spec 字段全部填齐(`schemas/materials/specs/*.md`),仅以下降级:

| 字段 | 模式 | 备注 |
|---|---|---|
| `01.verified_by_cppred` | partial | 8 ok / 9 fail,degraded 候选保留 |
| `04.spectra_path` | stub | L3 atomic skill `materials-flux-synthesis-execution` 待落地 |
| `05.model_training` | fixture 回放 | 本机无 XRD-1.1 Docker |

## 路径约定

- chain 内部引用一律用 **fixture 相对路径**(`./02_novel_candidates.md`、`cifs/gen002_ind_001_Ag4Ge3S5.cif`)
- 涉及 `/tmp/mp_run` 的引用仅作"原始真跑位置"标注,fixture 不依赖该路径存在
- `04.spectra_path` 指向 `spectra_stub/...`,**fixture 内未实际放置该文件**(stub 占位)

## 用途

- 回归测试:验证 L2 编排 skill 能消费真跑数据格式
- 字段契约:每个 substage 的 spec 字段在真跑场景下如何填充
- 故事线样本:从 MP 种子到 XRD 验证的可合成相完整轨迹
