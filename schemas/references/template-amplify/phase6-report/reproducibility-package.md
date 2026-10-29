# Reproducibility Package

> Generated during Phase 6 — Paper Writing
> Location: repro/

## Directory Structure

```
repro/
├── README.md              (how to reproduce all results)
├── environment.yml        (conda/pip environment, pinned versions)
├── download_data.sh        (script to download all required datasets)
├── run_all.sh              (single script to reproduce all experiments)
├── configs/                (all experiment configuration files)
├── scripts/                (all analysis/training scripts)
└── expected_results/       (expected output for verification)
```

## Requirements

- [ ] Every script runs without manual intervention
- [ ] All random seeds are fixed and documented
- [ ] Environment is fully specified (OS, Python version, package versions)
- [ ] Expected results match reported numbers within tolerance
- [ ] README.md provides clear step-by-step instructions
