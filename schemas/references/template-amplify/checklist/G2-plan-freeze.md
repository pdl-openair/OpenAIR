# G2 Gate — Plan Freeze

> **Between Phase 3 (Method/Analysis Design) and Phase 4 (Experiment Execution)**

ALL applicable items must be satisfied. Present this checklist to the user for sign-off.

**Type M items:**
- [ ] `evaluation-protocol.yaml` locked
- [ ] Innovation method fully argued, `innovation_point` marked
- [ ] Baselines confirmed with sources
- [ ] Ablation plan documented
- [ ] Failure modes predicted with backup plans for each

**Type D items:**
- [ ] Analysis framework and storyboard frozen
- [ ] Sufficiency criteria defined and all boxes checked
- [ ] Alternative explanations pre-identified

**Type C items:**
- [ ] Utility claim stated with quantitative targets
- [ ] Evaluation framework defined (correctness, performance, scalability, comparison)
- [ ] Competing tools identified and compared
- [ ] Case studies planned
- [ ] Documentation plan in place

**Universal items:**
- [ ] Story line confirmed by user
- [ ] `value_proposition` written in `research-anchor.yaml`
- [ ] Venue target still reasonable (invoke `venue-alignment` check)
- [ ] User approved everything explicitly

Only after ALL applicable items pass: set G2 status to `passed` and proceed to execution phase.

---

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Let me just try something first" | Undirected experiments waste compute and produce uninterpretable results. Design first. |
| "The evaluation can be decided later" | Post-hoc evaluation selection is cherry-picking. Lock metrics NOW. |
| "This is too simple to need a full design" | Simple methods still need locked evaluation, baselines, and a story line. Simplicity ≠ no planning. |
| "I already know what will work" | Then it should be trivial to write down. If you can't write it, you don't know it. |
| "Baselines are obvious" | Obvious to you ≠ convincing to reviewers. Document and confirm. |
| "Analysis doesn't need a framework" | Unstructured analysis produces shallow, incomplete results every time. |

## Red Flags — STOP Immediately

- Writing implementation code before G2 passes
- Choosing metrics without user discussion
- Skipping baselines "because the method is different"
- Starting Type D analysis without sufficiency criteria
- Merging Type H tracks without marking a primary
- Proceeding without explicit user approval on story line

---

## ⛔ MANDATORY STOP — After G2 Gate

After presenting the G2 gate checklist, **END YOUR RESPONSE IMMEDIATELY.**

Do NOT invoke `experiment-execution` in this same response.
Do NOT begin implementing code, downloading data, or setting up experiments.
Do NOT proceed to G3 check.

**STOP. WAIT. The user must approve the frozen plan before any execution begins.**

Your final output should be the G2 checklist followed by:
"G2 gate checklist is above. Plan is frozen. Do you approve proceeding to Phase 4 (Experiment Execution)?"

Then STOP.
