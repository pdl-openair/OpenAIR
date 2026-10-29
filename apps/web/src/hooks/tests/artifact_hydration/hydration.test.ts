import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useHydratedModel } from "../../useHydratedWorkbenchModel";
import type { useWorkbenchTaskModel } from "../../useWorkbenchTaskModel";
import type { WorkbenchTaskModel } from "../../../model/workbenchTask";
import {
  artifactBodyKey,
  loadArtifactBody,
} from "../../../lib/artifactBodyCache";
const mocks = vi.hoisted(() => ({ content: vi.fn(), token: "" }));
vi.mock("../../../api/client", () => ({ fetchArtifactContent: mocks.content }));
vi.mock("../../../api/http", async (original) => ({
  ...(await original<typeof import("../../../api/http")>()),
  getApiBaseUrl: () => "http://test",
  getAuthToken: () => mocks.token,
}));
function load(taskId: string, version = "v1") {
  return {
    status: "ready",
    source: "api",
    model: {
      taskId,
      artifacts: [{ id: "artifact-a", currentVersionId: version }],
    } as WorkbenchTaskModel,
  } as ReturnType<typeof useWorkbenchTaskModel>;
}
function body(content = "readable research") {
  return {
    artifactId: "artifact-a",
    content,
    format: "markdown",
    sourceMode: "live",
  };
}
beforeEach(() => {
  mocks.content.mockReset();
  mocks.token = `e30.${btoa(JSON.stringify({ sub: "user-a", workspaceId: "workspace-a" }))}.signature`;
  sessionStorage.clear();
});
it("deduplicates pending requests and retains failure through projection updates until explicit retry", async () => {
  let reject!: (reason: Error) => void;
  mocks.content.mockImplementationOnce(
    () =>
      new Promise((_, fail) => {
        reject = fail;
      }),
  );
  const { result, rerender } = renderHook(
    ({ state }) => useHydratedModel(state),
    { initialProps: { state: load("failure") } },
  );
  let pending!: Promise<void>;
  act(() => {
    pending = result.current.hydrate("failure", "artifact-a");
  });
  rerender({ state: load("failure") });
  await act(async () => {
    await result.current.hydrate("failure", "artifact-a");
  });
  expect(mocks.content).toHaveBeenCalledTimes(1);
  await act(async () => {
    reject(new Error("ERR_INSUFFICIENT_RESOURCES"));
    await pending;
  });
  rerender({ state: load("failure") });
  await act(async () => {
    await result.current.hydrate("failure", "artifact-a");
  });
  expect(mocks.content).toHaveBeenCalledTimes(1);
  expect(result.current.getArtifactFailureReason("artifact-a")).toBe(
    "transient",
  );
  mocks.content.mockResolvedValueOnce(body());
  act(() => result.current.retryArtifact("failure", "artifact-a"));
  await waitFor(() =>
    expect(result.current.hydratedModel?.artifacts[0].content).toBe(
      "readable research",
    ),
  );
  expect(mocks.content).toHaveBeenCalledTimes(2);
});
it("restores a successful version on remount and invalidates it on version change", async () => {
  mocks.content.mockResolvedValueOnce(body());
  const initial = load("versions");
  const first = renderHook(() => useHydratedModel(initial));
  await act(async () => {
    await first.result.current.hydrate("versions", "artifact-a");
  });
  first.unmount();
  const second = renderHook(({ state }) => useHydratedModel(state), {
    initialProps: { state: load("versions") },
  });
  expect(second.result.current.hydratedModel?.artifacts[0].content).toBe(
    "readable research",
  );
  expect(mocks.content).toHaveBeenCalledTimes(1);
  second.rerender({ state: load("versions", "v2") });
  expect(
    second.result.current.hydratedModel?.artifacts[0].content,
  ).toBeUndefined();
  mocks.content.mockResolvedValueOnce(body("new version"));
  await act(async () => {
    await second.result.current.hydrate("versions", "artifact-a");
  });
  expect(second.result.current.hydratedModel?.artifacts[0].content).toBe(
    "new version",
  );
  expect(mocks.content).toHaveBeenCalledTimes(2);
});
it("does not inject an old task response after navigation", async () => {
  let resolve!: (value: unknown) => void;
  mocks.content.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { result, rerender } = renderHook(
    ({ state }) => useHydratedModel(state),
    { initialProps: { state: load("previous") } },
  );
  let pending!: Promise<void>;
  act(() => {
    pending = result.current.hydrate("previous", "artifact-a");
  });
  rerender({ state: load("next") });
  await act(async () => {
    resolve(body("previous task"));
    await pending;
  });
  expect(result.current.hydratedModel?.taskId).toBe("next");
  expect(result.current.hydratedModel?.artifacts[0].content).toBeUndefined();
});
it("does not overwrite a new version with a pending old version response", async () => {
  let resolve!: (value: unknown) => void;
  mocks.content.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { result, rerender } = renderHook(
    ({ state }) => useHydratedModel(state),
    {
      initialProps: { state: load("pending-version", "v1") },
    },
  );
  let pending!: Promise<void>;
  act(() => {
    pending = result.current.hydrate("pending-version", "artifact-a");
  });
  rerender({ state: load("pending-version", "v2") });
  mocks.content.mockResolvedValueOnce(body("version two"));
  await act(async () => {
    await result.current.hydrate("pending-version", "artifact-a");
  });
  await act(async () => {
    resolve(body("version one"));
    await pending;
  });
  expect(result.current.hydratedModel?.artifacts[0].content).toBe(
    "version two",
  );
  expect(mocks.content).toHaveBeenCalledTimes(2);
});
it("shares one request across consumers and stores only successful bodies in session cache", async () => {
  let resolve!: (value: unknown) => void;
  mocks.content.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const artifact = (
    load("shared").status === "ready"
      ? (load("shared") as { model: WorkbenchTaskModel }).model.artifacts[0]
      : null
  )!;
  const a = loadArtifactBody("shared", artifact);
  const b = loadArtifactBody("shared", artifact);
  expect(mocks.content).toHaveBeenCalledTimes(1);
  resolve(body());
  await Promise.all([a, b]);
  expect(
    sessionStorage.getItem(
      "openair.artifact-body.v2:" + artifactBodyKey("shared", artifact),
    ),
  ).toContain("readable research");
  mocks.content.mockResolvedValueOnce(body("forced"));
  expect((await loadArtifactBody("shared", artifact, true))?.content).toBe(
    "forced",
  );
});
it("scopes cached bodies to the signed-in principal", async () => {
  const artifact = (load("scope") as { model: WorkbenchTaskModel }).model
    .artifacts[0];
  mocks.content.mockResolvedValueOnce(body("account a"));
  await loadArtifactBody("scope", artifact);
  mocks.token = `e30.${btoa(JSON.stringify({ sub: "user-b", workspaceId: "workspace-a" }))}.signature`;
  mocks.content.mockResolvedValueOnce(body("account b"));
  expect((await loadArtifactBody("scope", artifact))?.content).toBe(
    "account b",
  );
  expect(mocks.content).toHaveBeenCalledTimes(2);
});

it("keeps display projection separate from original content when hydrating and restoring cache", async () => {
  const projection = {
    kind: "historical_draft_display" as const,
    label: "历史草稿整理展示",
    sourceArtifactId: "artifact-a",
    sourceArtifactIds: ["artifact-a"],
    originalStatus: "unfinalized" as const,
    content: "projected records",
  };
  mocks.content.mockResolvedValueOnce({ ...body("original draft"), displayProjection: projection });
  const initial = load("historical-projection");
  const first = renderHook(() => useHydratedModel(initial));
  await act(async () => { await first.result.current.hydrate("historical-projection", "artifact-a"); });
  expect(first.result.current.hydratedModel?.artifacts[0].content).toBe("original draft");
  expect(first.result.current.hydratedModel?.artifacts[0].displayProjection).toEqual(projection);
  first.unmount();
  const second = renderHook(() => useHydratedModel(initial));
  expect(second.result.current.hydratedModel?.artifacts[0].content).toBe("original draft");
  expect(second.result.current.hydratedModel?.artifacts[0].displayProjection).toEqual(projection);
});
