# G4 Gate — Write-Ready

> **Between Phase 5 (Results Integration) and Phase 6 (Paper Writing)**

ALL applicable items must be satisfied. Present to the user for sign-off.

**Results quality:**
- [ ] Core experiments/analyses complete (no planned runs remaining)
- [ ] [Type M] All methods ran to **completion** (NOT partial/prematurely-stopped runs)
- [ ] [Type M] Method outperforms at least one baseline on primary metric with statistical support (p-values, CIs reported)
- [ ] [Type M] Ablation study complete — every key component tested
- [ ] [Type D] Story line evidence chain complete — no narrative gaps
- [ ] [Type D] Alternative explanations addressed with evidence

**Novelty gate:**
- [ ] Multi-agent discussion completed (Step 4)
- [ ] Devil's Advocate found ZERO "fatal" vulnerabilities (if any fatal found, they were resolved via deepening, pivoting, or user-approved downgrade — NOT silently worked around)
- [ ] At least one finding that a domain expert would NOT have predicted before seeing the data

**Verification:**
- [ ] `claim-evidence-alignment` passed — full mapping table reviewed
- [ ] All seeds from `evaluation-protocol.yaml` ran and reported (no cherry-picking)
- [ ] Negative results documented

**Content readiness:**
- [ ] 4–6+ content points, each with supporting figures/tables
- [ ] At least **3 figures** produced and ready
- [ ] At least **2 tables** produced and ready
- [ ] Content outline presented to user and **explicitly approved**

**User confirmation:**
- [ ] User confirmed: "ready to write paper" (exact phrase or clear equivalent)
- [ ] Venue target final confirmation: "[venue] — still correct?"

Only after ALL applicable items pass: set G4 status to `passed` and proceed to `paper-writing`.

---

## Hard Rule

If the user has NOT explicitly said "ready to write paper" or equivalent, G4 CANNOT pass. Do NOT infer readiness from silence, from "okay", or from "looks good" (which may refer to results, not write-readiness). ASK EXPLICITLY: "Shall I proceed to paper writing now?"

## ⛔ MANDATORY STOP — This is the LAST checkpoint before paper writing

After presenting the G4 checklist, **END YOUR RESPONSE IMMEDIATELY.**

Do NOT invoke `paper-writing` in this same response.
Do NOT begin writing any LaTeX.
Do NOT set up the paper directory structure.

**STOP. WAIT.** The user must explicitly say "ready to write paper" or equivalent.

If you find yourself writing LaTeX without the user having said these words,
**YOU ARE VIOLATING A CRITICAL RULE.** Stop immediately.

---

## Red Flags — STOP

- Declaring results "complete" without statistical verification
- Missing ablation study for a Type M project
- Story line gaps in a Type D project (findings that don't connect)
- Content outline with fewer than 4 substantive points
- Proceeding to paper writing without explicit user confirmation
