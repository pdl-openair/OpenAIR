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
const samples = JSON.parse(fs.readFileSync(path.join(root, "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json"), "utf8"));
function taskWithDesign(key) {
  const task = structuredClone(samples.researchTask);
  task.researchDesignVersions[0] = structuredClone(samples[key]);
  return task;
}
test("additive gap and combined contribution objects decode", () => {
  for (const key of ["researchDesignExecutableGaps", "researchDesignCompositeContributions"])
    contract.assertResearchTaskV12(taskWithDesign(key));
});
test("unknown or incomplete contributions cannot decode", () => {
  const task = taskWithDesign("researchDesignCompositeContributions");
  task.researchDesignVersions[0].projectCompletionRequirements[0].contributingResponsibilityKeys.push("unknown");
  assert.throws(() => contract.assertResearchTaskV12(task));
});
test("current contributions require their target criterion", () => {
  const task = taskWithDesign("researchDesignCompositeContributions");
  task.researchDesignVersions[0].coverageReport.claims[0].acceptanceCriterionRefs = [];
  assert.throws(() => contract.assertResearchTaskV12(task));
});
test("gap status cannot mask a boundary gap", () => {
  const task = taskWithDesign("researchDesignExecutableGaps");
  task.researchDesignVersions[0].coverageReport.targets[1].sourceType = "constraint";
  assert.throws(() => contract.assertResearchTaskV12(task));
});
