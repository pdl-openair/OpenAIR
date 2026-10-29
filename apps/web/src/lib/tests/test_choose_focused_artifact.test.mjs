import assert from "node:assert/strict";
import { test } from "vitest";
import { chooseFocusedArtifactId } from "../artifactViewSelectionCore.mjs";
import { formatPresentationRank } from "../artifactPresentationOrder.mjs";

test("explicit focus and deep links still win over any format preference", () => {
  const ids = ["json-result", "md-report"];
  const formats = { "json-result": "json", "md-report": "markdown" };
  assert.equal(
    chooseFocusedArtifactId({
      focusedArtifactId: "json-result",
      deepLinkedArtifactId: null,
      visibleArtifactIds: ids,
      formatsById: formats,
    }),
    "json-result",
  );
  assert.equal(
    chooseFocusedArtifactId({
      focusedArtifactId: null,
      deepLinkedArtifactId: "json-result",
      visibleArtifactIds: ids,
      formatsById: formats,
    }),
    "json-result",
  );
});

test("default focus prefers the report over the first-minted raw JSON", () => {
  assert.equal(
    chooseFocusedArtifactId({
      focusedArtifactId: null,
      deepLinkedArtifactId: null,
      visibleArtifactIds: ["json-result", "csv-table", "md-report", "png-figure"],
      formatsById: {
        "json-result": "json",
        "csv-table": "csv",
        "md-report": "markdown",
        "png-figure": "png",
      },
    }),
    "md-report",
  );
});

test("without formats the historical first-id fallback is kept", () => {
  assert.equal(
    chooseFocusedArtifactId({
      focusedArtifactId: null,
      deepLinkedArtifactId: null,
      visibleArtifactIds: ["a", "b"],
    }),
    "a",
  );
  assert.equal(
    chooseFocusedArtifactId({
      focusedArtifactId: null,
      deepLinkedArtifactId: null,
      visibleArtifactIds: [],
    }),
    null,
  );
});

test("format rank orders 报告 → 表格 → 图 → 原始数据 → 其他", () => {
  assert.ok(formatPresentationRank("markdown") < formatPresentationRank("csv"));
  assert.ok(formatPresentationRank("csv") < formatPresentationRank("png"));
  assert.ok(formatPresentationRank("png") < formatPresentationRank("json"));
  assert.ok(formatPresentationRank("json") < formatPresentationRank("cif"));
  assert.ok(formatPresentationRank("CIF") === formatPresentationRank("cif"));
  assert.ok(formatPresentationRank(null) === formatPresentationRank("cif"));
});
