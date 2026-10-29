import { expect, type TestInfo } from "@playwright/test";
import { attachJson } from "./evidence";

export type Batch1FailureType = "NONE" | "HARNESS_SETUP_FAIL" | "PRODUCT_GATE_FAIL" | "INFRA_FAIL";

export type Batch1PreconditionReport = {
  scenario_id: string;
  task_id?: string;
  target_url: string;
  viewport: string;
  precondition_status: "PASS" | "FAIL";
  failure_type: Batch1FailureType;
  errors: string[];
  notes?: string[];
};

export type Batch1CaseGateResult = {
  id: string;
  bug: string;
  passed: boolean;
  description: string;
  failure_type: Batch1FailureType;
  precondition: Batch1PreconditionReport;
  actual?: unknown;
  expected?: unknown;
};

export class Batch1CaseRecorder {
  private readonly results: Batch1CaseGateResult[] = [];

  record(result: Batch1CaseGateResult): void {
    this.results.push(result);
    if (result.failure_type === "HARNESS_SETUP_FAIL") {
      expect.soft(false, `HARNESS_SETUP_FAIL ${result.id}: ${result.precondition.errors.join("; ") || result.description}`).toBe(true);
      return;
    }
    if (result.failure_type === "INFRA_FAIL") {
      expect.soft(false, `INFRA_FAIL ${result.id}: ${result.description}`).toBe(true);
      return;
    }
    expect.soft(result.passed, `${result.id}: ${result.description}`).toBe(true);
  }

  list(): Batch1CaseGateResult[] {
    return [...this.results];
  }

  summary(): Record<string, "PASS" | "FAIL"> {
    const out: Record<string, "PASS" | "FAIL"> = {};
    for (const result of this.results) {
      out[result.id] = result.passed ? "PASS" : "FAIL";
    }
    return out;
  }

  async attach(testInfo: TestInfo, label = "batch1-case-gates"): Promise<void> {
    await attachJson(testInfo, label, this.results, "gates");
    await attachJson(testInfo, `${label}-summary`, this.summary(), "gates");
  }
}
