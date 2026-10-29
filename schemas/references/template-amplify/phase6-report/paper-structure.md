# Paper Structure

> Generated during Phase 6 — Paper Writing
> Location: paper/

## Directory Structure

```
paper/
├── main.tex              (only \input{} references — no prose here)
├── preamble.tex          (packages, macros, theorem environments)
├── sections/
│   ├── abstract.tex
│   ├── introduction.tex
│   ├── related-work.tex
│   ├── method.tex
│   ├── theoretical.tex   (if project has theoretical analysis)
│   ├── experiments.tex
│   ├── results.tex
│   ├── discussion.tex
│   └── conclusion.tex
├── figures/
├── tables/
├── references.bib
└── supplementary/
    └── proofs.tex        (full proof details if space-limited in main paper)
```

## Per-Section Self-Check

After polishing each section, verify:

```
Section: [name]
Word count: [N] (minimum: [M])
Citation count: [N] (minimum: [M])
Figures referenced: [list]
Tables referenced: [list]
Status: [PASS / BELOW MINIMUM — expand before proceeding]
```

## Per-Section Fidelity Audit

Before presenting any section to user:

```
FIDELITY AUDIT for [section name]:
- Quantitative claims: [N] total, [N] verified against logs, [N] unverifiable
- Method descriptions: [N] total, [N] verified against scripts
- Interpretive claims: [N] total (these are LEGITIMATE — see below)
- Status: PASS / FAIL
```

## Minimum Quality Requirements

| Element | Minimum |
|---------|---------|
| Figures | ≥3 |
| Tables | ≥2 |
| References | ≥20 |
| Introduction word count | ≥600 |
| Related Work word count | ≥500 |
| Discussion word count | ≥400 |
