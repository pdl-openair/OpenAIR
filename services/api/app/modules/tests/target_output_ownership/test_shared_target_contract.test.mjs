import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import test from "node:test";

const root = fileURLToPath(new URL("../../../../../../", import.meta.url));
const require = createRequire(path.join(root, "apps/web/package.json"));
const ts = require(process.env.OPENAIR_TYPESCRIPT_MODULE || "typescript");
const loaded = new Map();
function loadTs(file) {
  if (loaded.has(file)) return loaded.get(file);
  const output = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const unit = { exports: {} };
  const localRequire = (ref) =>
    ref.startsWith(".")
      ? loadTs(path.resolve(path.dirname(file), `${ref}.ts`))
      : require(ref);
  new Function("require", "module", "exports", output)(
    localRequire,
    unit,
    unit.exports,
  );
  loaded.set(file, unit.exports);
  return unit.exports;
}
const contract = loadTs(
  path.join(root, "packages/shared/src/researchOrchestration.ts"),
);
const sample = JSON.parse(
  fs.readFileSync(
    path.join(
      root,
      "packages/shared/test-fixtures/workflow-target-output-ownership-valid.json",
    ),
  ),
);
function currentPayload() {
  const payload = JSON.parse(
    fs.readFileSync(
      path.join(
        root,
        "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json",
      ),
    ),
  ).researchTask;
  const design = payload.researchDesignVersions[0];
  design.projectCompletionRequirements[0].workflowResponsibilityKey = "records";
  design.workflowResponsibilityCoverage = [
    sample.workflowResponsibilityCoverage,
  ];
  payload.completionDecisions = [
    {
      completionDecisionId: "isolated-completion-fixture",
      scientificStateVersion: 1,
      disposition: "evidence_gap",
      sourceRefs: [],
      decidedAt: "2026-10-09T00:00:00Z",
      completionAuthorityVersion: "project_completion_v1",
      researchDesignRef: {
        researchDesignId: design.researchDesignId,
        version: design.version,
      },
      canComplete: false,
      requirementAssessments: [sample.completionRequirementAssessment],
      gaps: [],
      uncertainty: [],
      limitations: [],
      rationale: "Isolated shape fixture; applicability pending.",
    },
  ];
  payload.agentInteractionRecords = [sample.reuseInteractionRecord];
  return payload;
}
test("legacy and additive target completion contracts decode", () => {
  const legacy = JSON.parse(
    fs.readFileSync(
      path.join(
        root,
        "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json",
      ),
    ),
  ).researchTask;
  contract.assertResearchTaskV12(legacy);
  contract.assertResearchTaskV12(currentPayload());
});
test("unknown fulfillment status fails decoding", () => {
  const payload = currentPayload();
  payload.completionDecisions[0].requirementAssessments = [
    {
      ...sample.completionRequirementAssessment,
      fulfillmentStatus: "invented_success",
    },
  ];
  assert.throws(
    () => contract.assertResearchTaskV12(payload),
    /fulfillmentStatus/,
  );
});
test("core responsibility cannot claim not applicable", () => {
  const payload = currentPayload();
  payload.researchDesignVersions[0].workflowResponsibilityCoverage = [
    {
      ...sample.workflowResponsibilityCoverage,
      requirement: "core",
      coverageMode: "not_selected",
      reusedResultRefs: [],
    },
  ];
  assert.throws(
    () => contract.assertResearchTaskV12(payload),
    /responsibility coverage/,
  );
});
