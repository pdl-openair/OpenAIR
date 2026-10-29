import assert from "node:assert/strict";
import { readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(testDirectory, "../../..");
const httpPath = path.join(repoRoot, "apps/web/src/api/http.ts");

async function loadModule(entryPoint) {
  const pnpmModules = path.join(repoRoot, "node_modules/.pnpm");
  const esbuildFolder = (await readdir(pnpmModules)).find((name) => name.startsWith("esbuild@"));
  assert.ok(esbuildFolder, "esbuild must be available through the existing Vite toolchain");
  const esbuild = await import(pathToFileURL(path.join(pnpmModules, esbuildFolder, "node_modules/esbuild/lib/main.js")).href);
  const built = await esbuild.build({ entryPoints: [entryPoint], bundle: true, format: "esm", platform: "browser", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString("base64")}`);
}

const http = await loadModule(httpPath);

function response(status, detail) {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("provider 5xx preserves raw diagnostics but presents only temporary-unavailable copy", async () => {
  const detail = { code: "provider_http_error", message: "provider runtime dispatch worker claim failed: HTTP 502" };
  const error = await http.apiErrorFromResponse(response(502, detail));
  assert.equal(error.detail.code, "provider_http_error");
  assert.equal(error.userMessage, "服务暂时不可用，请稍后继续。");
  assert.doesNotMatch(error.userMessage, /provider|runtime|dispatch|worker|claim|HTTP\s*5\d\d|exception/i);
});

test("required capability diagnostic is not rendered as a user message or automatic recovery", async () => {
  const detail = "required_capability_unavailable:provider/runtime/dispatch worker claim";
  const error = await http.apiErrorFromResponse(response(422, detail));
  assert.equal(error.detail, detail);
  assert.equal(error.userMessage, "请求未完成，请检查后重试。");
  assert.doesNotMatch(error.userMessage, /required_capability_unavailable|provider|runtime|dispatch|worker|claim|exception|HTTP\s*5\d\d/i);
  assert.doesNotMatch(error.userMessage, /已恢复|已成功|自动重试/);
});

test("safe user input failure and successful response retain their normal paths", async () => {
  const badInput = await http.apiErrorFromResponse(response(400, { code: "invalid_input", userMessage: "研究问题不能为空。" }));
  assert.equal(badInput.userMessage, "研究问题不能为空。");
  const success = new Response(JSON.stringify({ status: "completed", message: "研究已完成。" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
  assert.deepEqual(await http.parseJson(success), { status: "completed", message: "研究已完成。" });
});
