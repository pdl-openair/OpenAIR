import type { APIRequestContext, APIResponse, Page, TestInfo } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { RUN_DIR } from "./config";

function safeName(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 120) || "item";
}

function evidenceDir(testInfo: TestInfo): string {
  return path.join(RUN_DIR, "network-console", safeName(testInfo.titlePath.join("__")));
}

export async function attachJson(testInfo: TestInfo, label: string, value: unknown, folder = "api-snapshots"): Promise<string> {
  const dir = path.join(RUN_DIR, folder);
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, `${safeName(testInfo.titlePath.join("__"))}__${safeName(label)}.json`);
  const body = JSON.stringify(value, null, 2);
  await fs.writeFile(file, body, "utf8");
  await testInfo.attach(label, { body, contentType: "application/json" });
  return file;
}

export async function attachText(testInfo: TestInfo, label: string, value: string, folder = "notes"): Promise<string> {
  const dir = path.join(RUN_DIR, folder);
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, `${safeName(testInfo.titlePath.join("__"))}__${safeName(label)}.txt`);
  await fs.writeFile(file, value, "utf8");
  await testInfo.attach(label, { body: value, contentType: "text/plain" });
  return file;
}

export async function screenshot(page: Page, testInfo: TestInfo, label: string): Promise<string> {
  const dir = path.join(RUN_DIR, "screenshots");
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, `${safeName(testInfo.titlePath.join("__"))}__${safeName(label)}.png`);
  await page.screenshot({ path: file, fullPage: true });
  await testInfo.attach(label, { path: file, contentType: "image/png" });
  return file;
}

export type ApiResponseSnapshot = {
  summary: Record<string, unknown>;
  bodyText: string;
};

function parseSnapshotJson(bodyText: string): unknown {
  if (!bodyText) {
    return null;
  }
  return JSON.parse(bodyText) as unknown;
}

async function responseSummary(response: APIResponse, bodyText: string): Promise<Record<string, unknown>> {
  return {
    url: response.url(),
    status: response.status(),
    ok: response.ok(),
    body: bodyText.slice(0, 20_000),
  };
}

export async function snapshotApiResponse(testInfo: TestInfo, label: string, response: APIResponse): Promise<ApiResponseSnapshot> {
  const bodyText = await response.text().catch(() => "");
  const summary = await responseSummary(response, bodyText);
  await attachJson(testInfo, label, summary);
  return { summary, bodyText };
}

export async function snapshotTask(request: APIRequestContext, testInfo: TestInfo, apiBaseUrl: string, taskId: string, label: string): Promise<any> {
  const response = await request.get(`${apiBaseUrl}/api/tasks/${encodeURIComponent(taskId)}`);
  const { summary, bodyText } = await snapshotApiResponse(testInfo, label, response);
  if (!response.ok()) {
    throw new Error(`Unable to read task ${taskId}: HTTP ${response.status()} ${(summary.body as string) ?? ""}`);
  }
  return parseSnapshotJson(bodyText);
}

type NetworkEntry = Record<string, unknown>;
type ConsoleEntry = Record<string, unknown>;

export function collectPageEvidence(page: Page, testInfo: TestInfo) {
  const network: NetworkEntry[] = [];
  const consoleEntries: ConsoleEntry[] = [];
  const pageErrors: ConsoleEntry[] = [];

  page.on("request", (request) => {
    network.push({
      type: "request",
      method: request.method(),
      url: request.url(),
      resourceType: request.resourceType(),
    });
  });
  page.on("response", (response) => {
    network.push({
      type: "response",
      method: response.request().method(),
      url: response.url(),
      status: response.status(),
      ok: response.ok(),
    });
  });
  page.on("requestfailed", (request) => {
    network.push({
      type: "requestfailed",
      method: request.method(),
      url: request.url(),
      resourceType: request.resourceType(),
      failure: request.failure()?.errorText ?? "unknown",
    });
  });
  page.on("console", (message) => {
    consoleEntries.push({
      type: message.type(),
      text: message.text(),
      location: message.location(),
    });
  });
  page.on("pageerror", (error) => {
    pageErrors.push({ name: error.name, message: error.message, stack: error.stack ?? null });
  });

  return {
    pageErrors,
    async flush(): Promise<void> {
      const dir = evidenceDir(testInfo);
      await fs.mkdir(dir, { recursive: true });
      const networkFile = path.join(dir, "network.json");
      const consoleFile = path.join(dir, "console.json");
      const pageErrorFile = path.join(dir, "page-errors.json");
      await fs.writeFile(networkFile, JSON.stringify(network, null, 2), "utf8");
      await fs.writeFile(consoleFile, JSON.stringify(consoleEntries, null, 2), "utf8");
      await fs.writeFile(pageErrorFile, JSON.stringify(pageErrors, null, 2), "utf8");
      await testInfo.attach("network-log", { path: networkFile, contentType: "application/json" });
      await testInfo.attach("console-log", { path: consoleFile, contentType: "application/json" });
      await testInfo.attach("page-errors", { path: pageErrorFile, contentType: "application/json" });
    },
  };
}
