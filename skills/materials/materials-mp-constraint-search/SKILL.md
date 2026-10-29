---
name: materials-mp-constraint-search
description: "Query Materials Project for crystals satisfying a multi-objective performance constraint set (bandgap / formation_energy / ehull + banned elements), with optional crystal-property-prediction MCP cross-validation of cl_score / f_max / exfoliation_energy. Use when the user wants real DFT-verified seed candidates for DEMO evolution, or wants to know which existing materials meet a property constraint set. Common phrasings: '找满足约束的种子材料', '查 MP 看哪些三元硫族化合物带隙在 0.5–2 eV', '给我一批高合成可行性的演化种子'. Do NOT use when: (a) just looking up one material like MoS2's bandgap → use pymatgen/scripts/mp_query.py info; (b) generating NEW crystals → use materials-new-crystal-generation; (c) computing properties for an existing CIF → use crystal-property-prediction MCP directly or materials-crystal-property-evaluation."
license: MIT
metadata:
  skill-author: DEMO project
---

# MP Constraint Search

## Input

- **约束集**：bandgap / formation_energy / ehull 上下界、元素数范围、禁用元素列表
- **多样性参数**：每个 chemsys 保留前 N 个、排序键（auto / stability / cl_score）
- **是否启用 crystal-property-prediction MCP 验证**（`--verify-cppred`）：决定是否对 cl_score / f_max / exfoliation_energy 做交叉验证
- **MP_API_KEY**：从项目根 `.env` 读取（如 `/home/george/code/DEMO/.env`；申请见 https://next-gen.materialsproject.org/api）

## Default Constraint Set

Aligned with `DEMO/Evoloop.py` `my_constraints`:

| Constraint | Threshold | Filtered where |
|------------|-----------|----------------|
| `bandgap_eV` | ∈ [0.2, 3.5] | MP server-side |
| `formation_energy_eV` | ≤ 0 | MP server-side |
| `ehull_eV` | ≤ 1.0 | MP server-side |
| `num_elements` | == 3 | MP server-side |
| `element_set` banned | Hg, Tl, Na, K, F | MP server-side (`exclude_elements`) |
| `sg_number` | ≠ 1 | Client-side |
| `cl_score` | ≥ 0.50 | **crystal-property-prediction MCP only** (optional `--verify-cppred`) |
| `f_max_eV_A` | ≤ 2.0 | **crystal-property-prediction MCP only** (optional `--verify-cppred`) |
| `exfoliation_energy_meV` | ∈ [-5, 350] | **crystal-property-prediction MCP only** (optional `--verify-cppred`) |

**Important:** MP does not compute cl_score / f_max / exfoliation_energy.
For full constraint satisfaction you must enable `--verify-cppred` (each candidate
costs ~10–30s against the crystal-property-prediction MCP at `mcp.example.internal:22222`).

## Steps

推荐端到端流水线（产出 `mp_candidates_diverse_v2/`，命中率 60%、chemsys 多样性 100%、约 7 分钟）：

```
Step 1: MP server-side query
  num_elements=3, bandgap ∈ [0.2,3.5], formation_energy ≤ 0,
  ehull ≤ 1.0, exclude Hg/Tl/Na/K/F
    ↓ (~20s)
Step 2: Client-side filter
  sg ≠ 1, ban 22 rare elements (Ac/Th/U/.../Tc/Pm/Po/At/Rn/Fr/Ra)
    ↓
Step 3: Diversity filter
  group by chemsys, keep top-1 per group (rank_by=stability)
    ↓
Step 4: Crystal-property-prediction MCP verification
  per candidate (~10–30s each): cl_score / f_max / exfoliation
    ↓
Step 5: Constraint check + write passing_candidates.json
  filter to candidates passing ALL 9 constraints
  emit cif_base64 list ready for evolution seeds
```

### Quick Start

```bash
# 1. Fast survey — only uses MP server-side fields (≈20s for 30 candidates)
python3 .claude/skills/materials-mp-constraint-search/scripts/search_mp_candidates.py \
    --max-results 30

# 2. Recommended full pipeline — diverse, rare-excluded, cppred-verified
python3 .claude/skills/materials-mp-constraint-search/scripts/search_mp_candidates.py \
    --verify-cppred \
    --max-results 15 \
    --max-per-chemsys 1 \
    --output-dir ./seeds_v1
# → produces passing_candidates.json (clean structured output)

# 3. Customize the constraint set
python3 .claude/skills/materials-mp-constraint-search/scripts/search_mp_candidates.py \
    --bandgap 1.0 2.0 \
    --ehull-max 0.5 \
    --num-elements 2 4 \
    --banned-elements Hg Tl Cd Pb
```

## Output

Layout：

```
mp_candidates_<timestamp>/
├── candidates.json             # 所有候选，含 MP 字段 + cppred 验证字段（结构化 JSON）
├── cifs/                       # 每个候选的 CIF 结构文件
│   ├── mp-1234_MoS2.cif
│   └── ...
├── summary.json                # 完整原始数据 + 查询元信息
├── report.md                   # 人类可读 Markdown 报告
└── passing_candidates.json     # ⭐ 通过全部约束的候选 + base64 CIF
                                 #   （仅 --verify-cppred 时生成）
```

### `passing_candidates.json` schema

```jsonc
{
  "metadata": {
    "generated_at": "2026-06-15T20:50:00",
    "constraint_set": { /* DEFAULT_CONSTRAINTS snapshot */ },
    "query_params": { /* all CLI args as passed */ }
  },
  "summary": {
    "total_candidates": 6,
    "cppred_verified_ok": 5,
    "passing_all_constraints": 3,
    "unique_chemsys": 3,
    "hit_rate": 0.60
  },
  "passing_candidates": [
    {
      "mp_id": "mp-1197575",
      "formula": "Ag2AsS2",
      "chemsys": "Ag-As-S",
      "element_set": ["Ag", "As", "S"],
      "properties": {
        "bandgap_eV": 0.538, "formation_energy_eV": -0.146,
        "ehull_eV": 0.003,   "cl_score": 0.809,
        "f_max_eV_A": 0.170, "exfoliation_energy_meV": 28.8,
        "sg_number": 7,      "sg_symbol": "Pc",
        "cppred_bandgap_ev": 0.848,  "cppred_hull_ev": 1.091
      },
      "passes_constraints": {
        "bandgap_eV": true, "formation_energy_eV": true,
        "ehull_eV": true,   "num_elements": true,
        "sg_number": true,  "cl_score": true,
        "f_max_eV_A": true, "exfoliation_energy_meV": true,
        "element_set_banned": true
      },
      "cif_path": "cifs/mp-1197575_Ag2AsS2.cif",
      "cif_base64": "IyBnZW5lcmF0ZWQg..."   // inline CIF
    }
  ],
  "seed_cif_files": [   // ⭐ 直接喂给 mattergen MCP
    {"name": "mp-1197575_Ag2AsS2.cif",  "cif_base64": "..."},
    {"name": "mp-567334_Ag(BCl)6.cif",  "cif_base64": "..."},
    {"name": "mp-568392_Ag10Te4Br3.cif","cif_base64": "..."}
  ]
}
```

## CLI Reference

```
Query parameters:
  --max-results N            最终返回数上限（默认 200）
  --bandgap MIN MAX          带隙范围 eV（默认 0.2 3.5）
  --formation-energy-max F   形成能上限 eV/atom（默认 0）
  --ehull-max H              凸包能上限 eV/atom（默认 1.0）
  --num-elements MIN MAX     元素数范围（默认 3 3）

Element filtering:
  --banned-elements LIST     禁用元素（默认 Hg Tl Na K F，服务端过滤）
  --keep-radioactive         保留锕系/Tc/Pm/Po/At/Rn/Fr/Ra（默认排除）

Diversity:
  --max-per-chemsys N        每个化学系统保留前 N 个（默认 1，确保多样性）
  --rank-by MODE             组内排序：auto / stability / cl_score（默认 auto）

Verification:
  --verify-cppred              启用 crystal-property-prediction MCP 验证 cl_score/f_max/exfoliation
  --cppred-mcp-url URL         crystal-property-prediction MCP 端点（默认 mcp.example.internal:22222）

Output:
  --output-dir DIR           输出目录（默认 mp_candidates_<ts>/）
```

### Default Rare Elements

When `--keep-radioactive` is NOT passed (default), these 22 elements are
excluded client-side (MP API's `exclude_elements` URL param caps at 60 chars,
so the original 5 banned elements go to MP server-side, and the rest are
filtered client-side after the query):

```
Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr    (actinides, 15)
Tc Pm Po At Rn Fr Ra                            (synthetic/rare, 7)
```

**Why exclude by default?** These elements have near-zero representation in
ICSD, so the JACS CL-score ensemble treats them as out-of-distribution and
returns CL ≈ 0.003–0.02 for any crystal containing them — guaranteed
constraint failure. Skipping them saves a crystal-property-prediction MCP call (5–10× speedup).

## How It Works

1. **Server-side filters** (pushed to MP API):
   - `num_elements=(3,3)` → `num_elements` (or `nelements` on old mp_api)
   - `band_gap=(0.2, 3.5)` → `band_gap`
   - `formation_energy_per_atom=(None, 0)` → `formation_energy_per_atom`
   - `energy_above_hull=(None, 1.0)` → `energy_above_hull`
   - `exclude_elements=["Hg","Tl","Na","K","F"]` → element exclusion
2. **Client-side filters**: `sg_number` blacklist, redundant element check
3. **Optional cppred verification**: each candidate's CIF is POSTed to crystal-property-prediction MCP
   `predict_flux_properties`, which returns `cl_score / f_max / exfoliation_energy`
   via ALIGNN + CHGNet + MatterSim + JACS pipeline.

## Field Name Mapping (DEMO ↔ MP ↔ crystal-property-prediction MCP)

| DEMO constraint | MP API field | crystal-property-prediction MCP field |
|-----------------|--------------|----------------|
| `bandgap_eV` | `band_gap` | `bandgap_ev` |
| `formation_energy_eV` | `formation_energy_per_atom` | `formation_energy_ev` |
| `ehull_eV` | `energy_above_hull` | `hull_ev` |
| `num_elements` | `nelements` / `num_elements` | `num_elements` |
| `element_set` | `elements` | `element_set` |
| `sg_number` | `symmetry.number` | `sg_number` |
| `cl_score` | ❌ not in MP | `cl_score` |
| `f_max_eV_A` | ❌ not in MP | `f_max_ev_a` |
| `exfoliation_energy_meV` | ❌ not in MP | `exfoliation_energy_mev` |

**Unit normalization:** crystal-property-prediction MCP returns `bandgap_ev` / `f_max_ev_a` / `hull_ev`
(lowercase + `_ev` suffix). DEMO evolution constraints use `_eV` / `_eV_A`. The
script preserves both forms in `summary.json` and uses `_eV` in `candidates.json`
for consistency with `Evoloop.py`.

## Feeding Results into DEMO Evolution

The CIFs in `cifs/` are directly usable as `seed_cif_files` for the mattergen MCP:

```python
import base64, glob
seeds = []
for path in glob.glob("mp_candidates_*/cifs/*.cif")[:8]:
    with open(path, "rb") as f:
        seeds.append({
            "name": path.split("/")[-1],
            "cif_base64": base64.b64encode(f.read()).decode(),
        })
# Pass `seeds` to run_autonomous_evolution(seed_cif_files=seeds, ...)
```

## Common Pitfalls

1. **ehull in MP vs ALIGNN**: MP's `energy_above_hull` is from DFT, but the
   DEMO evolution uses ALIGNN-predicted `ehull_eV`. ALIGNN systematically
   overestimates ehull for 2D/metastable materials. If you're cross-checking,
   expect MP ehull ≤ ALIGNN ehull by 0.5–2 eV.

2. **exfoliation_energy in MP**: MP does NOT have a bulk `exfoliation_energy`
   field. The only way to compute it for arbitrary MP candidates is via CHGNet
   (which is what crystal-property-prediction MCP does). Don't expect `--verify-cppred` to match any
   MP-stored number.

3. **num_elements=3 hits a lot**: The default query returns thousands of
   candidates. Always use `--max-results` to cap.

4. **Crystal-property-prediction MCP availability**: If `mcp.example.internal:22222` is down, `--verify-cppred`
   will hang until timeout (180s/candidate). Pre-check with the
   `materials-new-crystal-generation` probe script.

## Bundled Resources

- **`scripts/search_mp_candidates.py`** — main executable
- **`references/constraint_mapping.md`** — deep dive into constraint ↔ field
  mappings, MP API quirks, and edge cases

## Integration with Other Skills

- **`materials-pymatgen`**: builds on pymatgen's `mp_api.client.MPRester` and
  reuses the `.env` loading pattern.
- **`materials-crystal-property-evaluation`**: Crystal-property-prediction MCP internally runs the
  same property pipeline documented in that skill (ALIGNN + CHGNet + MatterSim + JACS).
- **`materials-new-crystal-generation`**: Output CIFs can be fed as
  `seed_cif_files` to `run_autonomous_evolution`.
