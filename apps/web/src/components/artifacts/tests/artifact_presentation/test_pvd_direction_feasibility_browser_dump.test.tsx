/**
 * 1440x900 presentation dump for T14 PVD Cap5/Cap6.
 * Fixture class: CANONICAL_SHAPE_TEST_FIXTURE
 * REAL_PVD_RUN_VERIFIED=NO
 */
import { fireEvent, render } from "@testing-library/react";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ScientificArtifactPresentation } from "../../ScientificArtifactPresentation";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import {
  PRESENTATION_FIXTURE_NOTE,
  candidateDirectionFixture,
  feasibilityBlockFixture,
  feasibilityConditionalFixture,
  feasibilityDeferFixture,
  malformedCandidateFixture,
  malformedFeasibilityFixture,
} from "./pvdDirectionFeasibilityPresentationFixtures";

const OUT_DIR = resolve(
  process.cwd(),
  "artifacts/t14-pvd-direction-feasibility-browser",
);

function artifact(
  artifactType: string,
  content: Record<string, unknown>,
): WorkbenchArtifact {
  return {
    id: `dump-${artifactType}`,
    type: artifactType,
    artifactType,
    substage: artifactType,
    title: artifactType,
    format: "json",
    relatedStage: "execution_validation" as never,
    content: JSON.stringify(content),
  };
}

function dump(name: string, html: string, meta: Record<string, unknown>) {
  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(
    resolve(OUT_DIR, `${name}.html`),
    `<!doctype html><html><head><meta charset="utf-8"/><title>${name}</title>
<style>
  body{margin:0;font-family:Segoe UI,sans-serif;background:#f7f8fa;color:#1f2329}
  .frame{width:1440px;min-height:900px;margin:0 auto;padding:24px;box-sizing:border-box;background:#fff}
  .meta{width:1440px;margin:12px auto;font:12px/1.4 Consolas,monospace;color:#666}
</style></head><body>
<pre class="meta">${JSON.stringify(meta, null, 2)}</pre>
<div class="frame">${html}</div>
</body></html>`,
    "utf8",
  );
  writeFileSync(resolve(OUT_DIR, `${name}.json`), JSON.stringify(meta, null, 2), "utf8");
}

describe("T14 PVD browser dump 1440x900 (canonical-shape fixtures)", () => {
  it("writes first-screen and detail dumps for Cap5/Cap6 scenarios", () => {
    expect(PRESENTATION_FIXTURE_NOTE).toContain("CANONICAL_SHAPE_TEST_FIXTURE");

    const cases: Array<{
      name: string;
      type: string;
      document: Record<string, unknown>;
      openDetails?: boolean;
    }> = [
      {
        name: "01-candidate-direction-first",
        type: "process_factor_candidate_experiment_analysis",
        document: candidateDirectionFixture,
      },
      {
        name: "02-candidate-direction-details",
        type: "process_factor_candidate_experiment_analysis",
        document: candidateDirectionFixture,
        openDetails: true,
      },
      {
        name: "03-feasibility-conditional-first",
        type: "parameter_experiment_plan_feasibility_validation",
        document: feasibilityConditionalFixture,
      },
      {
        name: "04-feasibility-conditional-details",
        type: "parameter_experiment_plan_feasibility_validation",
        document: feasibilityConditionalFixture,
        openDetails: true,
      },
      {
        name: "05-feasibility-defer",
        type: "parameter_experiment_plan_feasibility_validation",
        document: feasibilityDeferFixture,
      },
      {
        name: "06-feasibility-block",
        type: "parameter_experiment_plan_feasibility_validation",
        document: feasibilityBlockFixture,
      },
      {
        name: "07-malformed-candidate-fail-closed",
        type: "process_factor_candidate_experiment_analysis",
        document: malformedCandidateFixture,
      },
      {
        name: "08-malformed-feasibility-fail-closed",
        type: "parameter_experiment_plan_feasibility_validation",
        document: malformedFeasibilityFixture,
      },
    ];

    for (const row of cases) {
      const { container, unmount } = render(
        <ScientificArtifactPresentation artifact={artifact(row.type, row.document)} />,
      );
      if (row.openDetails) {
        const trigger = Array.from(container.querySelectorAll("*")).find(
          (node) => node.textContent === "技术详情与核查信息",
        );
        if (trigger) fireEvent.click(trigger);
      }
      const text = container.textContent ?? "";
      dump(row.name, container.innerHTML, {
        BROWSER_FIXTURE: "CANONICAL_SHAPE_TEST_FIXTURE",
        REAL_PVD_RUN_VERIFIED: "NO",
        viewport: "1440x900",
        scenario: row.name,
        artifactType: row.type,
        hasFailClosed: text.includes("当前无法安全结构化展示"),
        hasInternalLeak: /(candidate_ref|node6:factor_assessment|parameter_keys|direction_refs|scope_exact)/.test(
          text,
        ),
      });
      expect(text.length).toBeGreaterThan(20);
      unmount();
    }
  });
});
