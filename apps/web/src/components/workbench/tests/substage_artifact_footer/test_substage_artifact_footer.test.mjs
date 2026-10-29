import assert from "node:assert/strict";
import { test } from "vitest";
import {
  buildSubstageArtifactDeepLink,
  projectSubstageArtifactFooter,
  reconcileSubstageArtifactFooterSelection,
} from "../../substageArtifactFooterCore.mjs";

function artifact(id, overrides = {}) {
  return {
    id,
    taskId: "task-a",
    title: `产物 ${id.toUpperCase()}`,
    format: "markdown",
    subStageInstanceId: "sub-a",
    artifactVisibility: "user",
    ...overrides,
  };
}

test("adding B and C keeps A in the complete Substage selector", () => {
  const initial = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["a"],
    artifacts: [artifact("a")],
  });
  const appended = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["a", "b", "c"],
    artifacts: [artifact("a"), artifact("b"), artifact("c")],
  });

  assert.deepEqual(
    initial.map((item) => item.id),
    ["a"],
  );
  assert.deepEqual(
    appended.map((item) => item.id),
    ["a", "b", "c"],
  );
});

test("refs and explicit instance ownership merge without duplicates or sibling leakage", () => {
  const items = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["a", "sibling", "legacy", "b"],
    artifacts: [
      artifact("a"),
      artifact("b"),
      artifact("c"),
      artifact("sibling", { subStageInstanceId: "sub-b" }),
      artifact("legacy", { subStageInstanceId: null }),
      artifact("other-task", { taskId: "task-b" }),
    ],
  });

  assert.deepEqual(
    items.map((item) => item.id),
    ["a", "legacy", "b", "c"],
  );
});

test("a referenced Artifact explicitly owned by a sibling cannot appear or win a deep link", () => {
  const items = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["current", "sibling"],
    artifacts: [
      artifact("current"),
      artifact("sibling", { subStageInstanceId: "sub-b" }),
    ],
  });
  const selected = reconcileSubstageArtifactFooterSelection({
    selection: null,
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactIds: items.map((item) => item.id),
    deepLinkedArtifactId: "sibling",
  });

  assert.deepEqual(
    items.map((item) => item.id),
    ["current"],
  );
  assert.equal(selected.artifactId, "current");
});

test("hidden and internal Artifacts never enter the Footer or win a deep link", () => {
  const items = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["visible", "hidden", "internal"],
    artifacts: [
      artifact("visible"),
      artifact("hidden", { artifactVisibility: "hidden" }),
      artifact("internal", { artifactVisibility: null, audience: "internal" }),
    ],
  });
  const selected = reconcileSubstageArtifactFooterSelection({
    selection: null,
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactIds: items.map((item) => item.id),
    deepLinkedArtifactId: "hidden",
  });

  assert.deepEqual(
    items.map((item) => item.id),
    ["visible"],
  );
  assert.equal(selected.artifactId, "visible");
});

test("Artifacts without a formal user-facing title are omitted instead of receiving a numbered label", () => {
  const items = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["named", "untitled"],
    artifacts: [artifact("named"), artifact("untitled", { title: "  " })],
  });

  assert.deepEqual(
    items.map((item) => item.id),
    ["named"],
  );
  assert.equal(items[0].title, "产物 NAMED");
});

test("same-Substage selection survives refresh, return, and appended Artifacts", () => {
  const selection = {
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactId: "b",
  };
  const refreshed = reconcileSubstageArtifactFooterSelection({
    selection,
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactIds: ["a", "b", "c"],
    deepLinkedArtifactId: null,
  });
  const returned = reconcileSubstageArtifactFooterSelection({
    selection: refreshed,
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactIds: ["a", "b", "c", "d"],
    deepLinkedArtifactId: null,
  });

  assert.equal(refreshed.artifactId, "b");
  assert.equal(returned.artifactId, "b");
});

test("valid deep links win stale selection and preserve unrelated query state", () => {
  const selected = reconcileSubstageArtifactFooterSelection({
    selection: {
      taskId: "task-a",
      substageInstanceId: "sub-a",
      artifactId: "a",
    },
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactIds: ["a", "b", "c"],
    deepLinkedArtifactId: "c",
  });
  const href = buildSubstageArtifactDeepLink({
    pathname: "/workbench/task-a",
    search: "?pane=artifact&artifact=a",
    artifactId: "artifact c/1",
  });

  assert.equal(selected.artifactId, "c");
  assert.equal(href, "/workbench/task-a?pane=artifact&artifact=artifact+c%2F1");
});

test("cross-task or cross-Substage selection cannot bleed into the current Footer", () => {
  const selected = reconcileSubstageArtifactFooterSelection({
    selection: {
      taskId: "task-a",
      substageInstanceId: "sub-a",
      artifactId: "b",
    },
    taskId: "task-a",
    substageInstanceId: "sub-b",
    artifactIds: ["c", "d"],
    deepLinkedArtifactId: null,
  });

  assert.deepEqual(selected, {
    taskId: "task-a",
    substageInstanceId: "sub-b",
    artifactId: "c",
  });
});

test("sibling outputs sort into reader-facing order and default to the report", () => {
  // Mint order for a durable dispatch: raw result JSON first, then files.
  const items = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["result", "leaderboard", "figure", "structure"],
    artifacts: [
      artifact("result", { format: "json", title: "agent result" }),
      artifact("leaderboard", { format: "csv", title: "agent · leaderboard.csv" }),
      artifact("figure", { format: "png", title: "agent · pareto_01.png" }),
      artifact("structure", { format: "cif", title: "agent · pareto_01.cif" }),
    ],
  });

  assert.deepEqual(
    items.map((item) => item.id),
    ["leaderboard", "figure", "result", "structure"],
  );

  const selected = reconcileSubstageArtifactFooterSelection({
    selection: null,
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactIds: items.map((item) => item.id),
    deepLinkedArtifactId: null,
  });
  assert.equal(selected.artifactId, "leaderboard");

  const withReport = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["result", "report", "leaderboard"],
    artifacts: [
      artifact("result", { format: "json", title: "agent result" }),
      artifact("report", { format: "markdown", title: "agent · report.md" }),
      artifact("leaderboard", { format: "csv", title: "agent · leaderboard.csv" }),
    ],
  });
  assert.deepEqual(
    withReport.map((item) => item.id),
    ["report", "leaderboard", "result"],
  );
});

test("same-rank artifacts keep their authored order", () => {
  const items = projectSubstageArtifactFooter({
    taskId: "task-a",
    substageInstanceId: "sub-a",
    artifactRefs: ["a", "b", "c"],
    artifacts: [
      artifact("a"),
      artifact("b"),
      artifact("c"),
    ],
  });

  assert.deepEqual(
    items.map((item) => item.id),
    ["a", "b", "c"],
  );
});
