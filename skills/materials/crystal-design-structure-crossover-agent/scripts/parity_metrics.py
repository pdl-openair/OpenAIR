# scripts/parity_metrics.py
"""evo_sun_parity 实验的六指标聚合与判定（实验方案 §3–§5）。

指标对齐 Chemeleon2 Methods（S085–S091）：
  mSUN / uniqueness / novelty / comp validity / metastability / coverage(FMD⁻¹)

臂目录三种形态（glob 可匹配多个目录，池化）：
  (a) EA run 目录（含 population_gen*.json）：
      主口径 = 末代 population ∪ 历代 pareto_rank==1 且不在末代者（至多补 20，近世代优先）
      --hof-only 时只取后者（副口径，对 EA 有利，只作参考）
  (b) 简单目录：*.cif + per_structure_metrics.json（random_walk_rep<N>/ 等）
  (c) 仅 *.cif：无逐体指标 → 需 --evaluate-missing（调 mattergen_eval 补评）否则报错

uniqueness/mSUN 的 unique 维 = 池化集合内先到先得的 StructureMatcher 判重
（与 Chemeleon2 同：集合级、非 batch-of-1）。比例均在原始池化样本上计算，
去重只产生 0/1 指示符，不丢样本；n_effective 单独报告。

FMD⁻¹ 的参考分布默认 fresh_gen 臂（未引导生成器），嵌入用 AMD（k=100，
pymatgen 自算，等距不变、纯几何——与 Chemeleon2 的 VAE latent 不同，
绝对值不可比，仅臂间可比）。

用法：
  python3 parity_metrics.py \
    --arm fresh_gen='mattergen-results/mattergen-base-200/generated_crystals_cif' \
    --arm random_walk='mattergen-results/mattergen-base-200/evo_sun_parity/random_walk_rep*' \
    --arm static_ea='mattergen-results/mattergen-base-200/evo_sun_parity/static_rules_rep*' \
    --arm agent_ea='mattergen-results/mattergen-base-200/evo_sun_parity/agent_no_mem_rep*' \
    [--hof-only] [--boot 2000] [--amd-k 100] [--out evo_sun_parity_metrics.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from pymatgen.analysis.structure_matcher import StructureMatcher
from scipy.stats import fisher_exact

sys.path.insert(0, str(Path(__file__).parent))
from evaluators import cif_to_structure  # noqa: E402

# Chemeleon2 (Alex-MP-20, 10k/模型) 参照值：并排报告用（evo_sun_parity.md §1）
CHEMELEON2_REF = {
    "msun": {"baseline": 0.159, "w_rl": 0.613, "ratio": 3.85},
    "novelty": {"baseline": 0.623, "w_rl": 0.975},
    "metastability": {"baseline": 0.512, "w_rl": 0.721},
    "uniqueness": {"baseline": 0.994, "w_rl": 0.887},
}

_MATCHER = StructureMatcher()  # 默认容差，与 novelty 判定同协议


# ================= 采样收集 =================

def _bucket_key(st) -> str:
    """StructureMatcher 匹配的必要条件是组成相同：reduced_formula 分桶预筛。"""
    try:
        return st.composition.reduced_formula
    except Exception:
        return "?"


def collect_arm(pattern: str, hof_only: bool) -> list[dict]:
    """glob 匹配所有目录并池化，返回 [{name, cif, ehull_eval, novel, ...}]。"""
    dirs = sorted(d for d in glob.glob(pattern) if Path(d).is_dir())
    if not dirs:
        sys.exit(f"臂目录 glob 无匹配: {pattern}")
    samples: list[dict] = []
    for d in dirs:
        d = Path(d)
        gen_files = sorted(d.glob("population_gen*.json"))
        if gen_files:  # (a) EA run 目录
            samples.extend(_collect_ea(d, gen_files, hof_only))
        elif (d / "per_structure_metrics.json").exists():  # (b) 简单目录
            samples.extend(_collect_simple(d))
        elif list(d.glob("*.cif")):  # (c) 仅 cif
            sys.exit(f"目录 {d} 只有 CIF 无逐体指标：先跑评估或加 --evaluate-missing")
        else:
            sys.exit(f"目录 {d} 既无 population_gen*.json 也无 *.cif")
    return samples


def _collect_ea(d: Path, gen_files: list[Path], hof_only: bool) -> list[dict]:
    """EA run：主口径 = 末代 pop ∪ 历代 front（不在末代者，至多补 20）。"""
    def load(p: Path) -> list[dict]:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return [x for x in raw if isinstance(x, dict) and x.get("cif")]

    last_gen = load(gen_files[-1])
    keep = [] if hof_only else last_gen
    last_names = {x.get("name") for x in last_gen}
    hof: list[dict] = []
    for p in reversed(gen_files):  # 近世代优先
        for x in load(p):
            if x.get("pareto_rank") == 1 and x.get("name") not in last_names:
                hof.append(x)
                last_names.add(x.get("name"))
                if not hof_only and len(hof) >= 20:
                    break
        if not hof_only and len(hof) >= 20:
            break
    return keep + hof[: 40 if hof_only else 20]


def _collect_simple(d: Path) -> list[dict]:
    metrics = json.loads((d / "per_structure_metrics.json").read_text(encoding="utf-8"))
    cifs = {p.stem: p for p in d.glob("*.cif")}
    out = []
    for m in metrics:
        name = m.get("name")
        cif_path = None
        for cand in (name, f"{name}_final", f"{name}_final.cif"):
            if cand in cifs:
                cif_path = cifs[cand]
                break
        if cif_path is None:
            print(f"  [warn] {d.name}: 指标条目 {name!r} 找不到对应 CIF，跳过")
            continue
        ind = dict(m)
        ind["cif"] = cif_path.read_text(encoding="utf-8")
        out.append(ind)
    return out


# ================= 指标 =================

def uniqueness_flags(samples: list[dict]) -> np.ndarray:
    """先到先得：与更早的同约化式样本 StructureMatcher 匹配 → 0。"""
    parsed: list[tuple[int, object]] = []
    for i, s in enumerate(samples):
        try:
            parsed.append((i, cif_to_structure(s["cif"])))
        except Exception:
            parsed.append((i, None))
    flags = np.zeros(len(samples))
    for pos, (i, st) in enumerate(parsed):
        if st is None:
            continue  # 解析失败：unique 记 0（保守）
        key = _bucket_key(st)
        dup = any(
            st2 is not None and _bucket_key(st2) == key and _MATCHER.fit(st2, st)
            for _, st2 in parsed[:pos]
        )
        flags[i] = 0.0 if dup else 1.0
    return flags


def amd_matrix(samples: list[dict], k: int) -> np.ndarray:
    """AMD 描述子（k 近邻平均距离，等距不变，纯几何）作为 FMD 嵌入。"""
    rows = []
    for s in samples:
        try:
            st = cif_to_structure(s["cif"])
            rows.append(_amd_vector(st, k))
        except Exception as e:
            print(f"  [warn] AMD 计算失败 {s.get('name')!r}: {e}")
    return np.asarray(rows, dtype=float)


def _amd_vector(st, k: int) -> list[float]:
    k_orig = k  # 兜底降 k 后仍需返回定长向量（见函数末尾）
    r = 4.0
    while True:
        nns = st.get_all_neighbors(r)
        counts = [len(x) for x in nns]
        if counts and min(counts) >= k:
            break
        if r > 20.0:  # 稀疏大胞兜底：降 k 到可得的
            k = max(1, min(counts) if counts else 1)
            break
        r *= 1.5
    per_site = []
    for site_nns in nns:
        dists = sorted(getattr(nb, "nn_distance", None) or nb[1] for nb in site_nns)
        per_site.append(dists[:k])
    vec = [float(np.mean(col)) for col in zip(*per_site)]
    if len(vec) < k_orig:  # 稀疏大胞降 k 的兜底路径：末值补齐，保持矩阵等形
        vec = vec + [vec[-1]] * (k_orig - len(vec))
    return vec


def fmd_inv(x: np.ndarray, y: np.ndarray) -> float:
    """FMD（Fréchet 距离，Chemeleon2 E016）取 1/(1+FMD)（E017），越大越对齐。"""
    from scipy.linalg import sqrtm
    mu1, mu2 = x.mean(axis=0), y.mean(axis=0)
    s1, s2 = np.cov(x.T), np.cov(y.T)
    covmean = sqrtm(s1 @ s2)
    if np.iscomplexobj(covmean):
        covmean = covmean.real
    fmd = float(((mu1 - mu2) ** 2).sum() + np.trace(s1 + s2 - 2 * covmean))
    return 1.0 / (1.0 + max(fmd, 0.0))


def _prop_ci(flags: np.ndarray, boot: int, rng: np.random.Generator) -> tuple[float, float, float]:
    """比例 + bootstrap 95% CI（对原始样本重采样）。"""
    if len(flags) == 0:
        return float("nan"), float("nan"), float("nan")
    point = float(flags.mean())
    n = len(flags)
    draws = [float(rng.choice(flags, size=n, replace=True).mean()) for _ in range(boot)]
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, float(lo), float(hi)


def quota_msun_flags(
    metastable: np.ndarray, novel: np.ndarray, flags: np.ndarray,
    buckets: list, k: int,
) -> np.ndarray:
    """quota-mSUN 指示向量:每个组成桶(reduced_formula)至多计 K 个 mSUN。

    与 msun 同基数(metastable ∧ novel ∧ unique),再加配额:按池化顺序
    先到先得,同桶第 K+1 个起的 mSUN 记 0 —— 同时罚家族坍缩与低质量
    (steering_parity2 实验一主指标,方案 A4)。NaN(评估失败)保持 NaN,
    由上层掩码剔除分母;解析失败无法分桶的个体按超配额处理(保守)。
    """
    msun = metastable * novel * flags
    out = msun.copy()
    counts: Counter = Counter()
    for i, b in enumerate(buckets):
        if msun[i] == 1.0:  # NaN 不等于 1,自然跳过
            if b is None or counts[b] >= k:
                out[i] = 0.0
            else:
                counts[b] += 1
    return out


def _quota_ci(
    metastable: np.ndarray, novel: np.ndarray, flags: np.ndarray,
    buckets: list, k: int, boot: int, rng: np.random.Generator,
) -> tuple[float, float, float]:
    """quota-mSUN 密度 + bootstrap 95% CI(每次重采样后重新施加配额)。

    配额是样本集的确定性变换,必须「先重采样再配额」(重采样抽中的重复
    个体会再次触发配额),不能先配额再重采样。分母与 msun 一致
    (novel 非缺失的个体数)。
    """
    m = ~(np.isnan(metastable) | np.isnan(novel))
    n = int(m.sum())
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    idx = np.arange(len(m))[m]
    point = float(quota_msun_flags(metastable, novel, flags, buckets, k)[m].mean())
    draws = []
    for _ in range(boot):
        sel = rng.choice(idx, size=n, replace=True)
        q = quota_msun_flags(
            metastable[sel], novel[sel], flags[sel], [buckets[i] for i in sel], k
        )
        draws.append(float(q.mean()))
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, float(lo), float(hi)


def analyze_arm(name: str, samples: list[dict], boot: int, rng,
                quota_k: int | None = None) -> dict:
    uniq = uniqueness_flags(samples)
    ehull = np.array([s.get("ehull_eval") if s.get("ehull_eval") is not None else np.nan
                      for s in samples])
    novel = np.array([s.get("novel") if s.get("novel") is not None else np.nan
                      for s in samples])
    compv = np.array([s.get("comp_valid_eval") if s.get("comp_valid_eval") is not None else np.nan
                      for s in samples])
    metastable = np.where(np.isnan(ehull), np.nan, (ehull < 0.1).astype(float))
    # 评估失败(None→NaN)的个体按各指标分别剔除分母，与 novel 的处理一致

    def masked(v: np.ndarray, flags: np.ndarray):
        m = ~np.isnan(v)
        return v[m], flags[m]

    out = {"n_total": len(samples), "n_effective": int(uniq.sum())}
    for key, v, fl in (
        ("uniqueness", uniq, uniq),
        ("novelty", novel, uniq),
        ("comp_validity", compv, uniq),
        ("metastability", metastable, uniq),
        ("msun", (metastable * novel * uniq), uniq),
    ):
        vv, ff = (v, fl) if key == "uniqueness" else masked(v, fl)
        out[key] = dict(zip(("point", "ci_lo", "ci_hi"), _prop_ci(vv, boot, rng)))
        out[key]["n"] = int(len(vv))

    if quota_k is not None:
        buckets = []
        for s in samples:
            try:
                buckets.append(_bucket_key(cif_to_structure(s["cif"])))
            except Exception:
                buckets.append(None)
        point, lo, hi = _quota_ci(metastable, novel, uniq, buckets, quota_k, boot, rng)
        m = ~(np.isnan(metastable) | np.isnan(novel))
        out["quota_msun"] = {"point": point, "ci_lo": lo, "ci_hi": hi,
                             "n": int(m.sum()), "quota_k": quota_k}
    return out


# ================= 主流程 =================

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arm", action="append", required=True,
                   help="NAME=GLOB，可重复；GLOB 匹配的目录池化为一个臂")
    p.add_argument("--hof-only", action="store_true", help="EA 臂只取历代 front（副口径）")
    p.add_argument("--quota-k", type=int, default=None,
                   help="启用 quota-mSUN(steering_parity2 实验一主指标):"
                        "每个 reduced_formula 桶至多计 K 个 mSUN(预注册 K=3)")
    p.add_argument("--boot", type=int, default=2000)
    p.add_argument("--amd-k", type=int, default=100)
    p.add_argument("--reference-arm", default="fresh_gen", help="FMD⁻¹ 的参考分布臂")
    p.add_argument("--out", default="evo_sun_parity_metrics.json")
    args = p.parse_args()

    rng = np.random.default_rng(20260902)
    arms: dict[str, list[dict]] = {}
    for spec in args.arm:
        name, _, pattern = spec.partition("=")
        if not pattern:
            sys.exit(f"--arm 格式应为 NAME=GLOB: {spec!r}")
        arms[name] = collect_arm(pattern, args.hof_only)
        print(f"==> 臂 {name}: 池化 {len(arms[name])} 个样本")

    if args.reference_arm not in arms:
        sys.exit(f"--reference-arm {args.reference_arm!r} 不在 --arm 列表里")

    aggregate = {}
    for name, samples in arms.items():
        aggregate[name] = analyze_arm(name, samples, args.boot, rng, quota_k=args.quota_k)
        agg = aggregate[name]
        print(f"  {name}: mSUN={agg['msun']['point']:.3f} "
              f"[{agg['msun']['ci_lo']:.3f},{agg['msun']['ci_hi']:.3f}] "
              f"n={agg['msun']['n']}/{agg['n_total']}(去重后 {agg['n_effective']})")
        if "quota_msun" in agg:
            q = agg["quota_msun"]
            print(f"  {name}: quota-mSUN(K={q['quota_k']})={q['point']:.3f} "
                  f"[{q['ci_lo']:.3f},{q['ci_hi']:.3f}] n={q['n']}")

    # coverage：各臂对参考臂的 FMD⁻¹
    ref_emb = amd_matrix(arms[args.reference_arm], args.amd_k)
    for name, samples in arms.items():
        emb = ref_emb if name == args.reference_arm else amd_matrix(samples, args.amd_k)
        aggregate[name]["fmd_inv"] = fmd_inv(emb, ref_emb)
        print(f"  {name}: FMD⁻¹={aggregate[name]['fmd_inv']:.4f}")

    # mSUN 两两 Fisher 精确检验(启用 quota 时对主指标 quota 口径加做一套)
    pairwise = {}
    pairwise_quota = {}
    names = list(arms)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = aggregate[names[i]]["msun"], aggregate[names[j]]["msun"]
            table = [[round(a["point"] * a["n"]), a["n"] - round(a["point"] * a["n"])],
                     [round(b["point"] * b["n"]), b["n"] - round(b["point"] * b["n"])]]
            _, pval = fisher_exact(table, alternative="two-sided")
            pairwise[f"{names[i]}_vs_{names[j]}"] = {
                "msun_a": a["point"], "msun_b": b["point"], "p_value": float(pval),
            }
            if args.quota_k is not None:
                qa, qb = aggregate[names[i]]["quota_msun"], aggregate[names[j]]["quota_msun"]
                tq = [[round(qa["point"] * qa["n"]), qa["n"] - round(qa["point"] * qa["n"])],
                      [round(qb["point"] * qb["n"]), qb["n"] - round(qb["point"] * qb["n"])]]
                _, pq = fisher_exact(tq, alternative="two-sided")
                pairwise_quota[f"{names[i]}_vs_{names[j]}"] = {
                    "quota_msun_a": qa["point"], "quota_msun_b": qb["point"],
                    "p_value": float(pq),
                }

    # uplift：判定输入（2026-09-03 修订判据）+ Chemeleon2 参照并排
    # 主判定: agent_ea mSUN 的 CI 下界 ≥ 61.3%(chemeleon2 w/ RL)
    # 次判定: agent_ea − random_walk ≥ +10pp(Fisher p<0.05 在 pairwise 里)
    uplift = {"chemeleon2_reference": CHEMELEON2_REF}
    if "agent_ea" in aggregate and "random_walk" in aggregate:
        rw = aggregate["random_walk"]["msun"]["point"]
        ag = aggregate["agent_ea"]["msun"]["point"]
        ci_lo = aggregate["agent_ea"]["msun"]["ci_lo"]
        uplift["delta_agent_over_random_walk"] = float(ag / rw) if rw > 0 else None
        uplift["msun_ci_lo_vs_chemeleon2_w_rl"] = {
            "ci_lo": ci_lo, "threshold": CHEMELEON2_REF["msun"]["w_rl"],
            "primary_pass": bool(ci_lo >= CHEMELEON2_REF["msun"]["w_rl"]),
        }
        uplift["msun_gain_pp_over_random_walk"] = {
            "gain": float((ag - rw) * 100), "secondary_threshold_pp": 10.0,
        }

    result = {
        "scheme": "evo_sun_parity", "hof_only": args.hof_only,
        "quota_k": args.quota_k,
        "aggregate": aggregate, "pairwise": pairwise,
        **({"pairwise_quota": pairwise_quota} if args.quota_k is not None else {}),
        "uplift": uplift,
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"==> 写出 {args.out}")
    print(f"    Δ(agent_ea/random_walk) = {uplift.get('delta_agent_over_random_walk')} "
          f"| Chemeleon2 参照 = {CHEMELEON2_REF['msun']['ratio']}×")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
