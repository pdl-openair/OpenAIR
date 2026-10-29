import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  discoverRecoveryRequest,
  editArtifactContent,
  fetchRecoveryRequest,
  restoreArtifactVersion,
  subscribeTaskEvents,
} from "../../client";

import {
  fetchCurrentTaskStartStatus,
  fetchTaskStartStatus,
} from "../../taskStartStatus";

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
function request() {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  return {
    url: new URL(url),
    method: init?.method ?? "GET",
    headers: new Headers(init?.headers),
    body: init?.body,
  };
}

describe("real recovery and version transport", () => {
  it("startup authority and request receipts use distinct GET routes and cannot impersonate each other", async () => {
    const authority = {
      taskId: "task/a",
      taskRevision: 42,
      state: "not_committed",
      startAllowed: true,
      responsibility: "USER",
      retryable: true,
    };
    const receipt = {
      taskId: "task/a",
      taskRevision: 42,
      selectionRequestId: "request/a",
      state: "committed",
      executionDecisionId: "decision-1",
    };
    fetchMock.mockResolvedValueOnce(json(authority));
    expect(await fetchCurrentTaskStartStatus("task/a")).toEqual(authority);
    expect(request().url.pathname).toBe(
      "/api/tasks/task%2Fa/execution-start-status",
    );
    expect(request().method).toBe("GET");
    fetchMock.mockResolvedValueOnce(json(receipt));
    expect(await fetchTaskStartStatus("task/a", "request/a")).toEqual(receipt);
    expect(request().url.pathname).toBe(
      "/api/tasks/task%2Fa/execution-selections/request%2Fa",
    );
    expect(request().method).toBe("GET");
    fetchMock.mockResolvedValueOnce(json(receipt));
    await expect(fetchCurrentTaskStartStatus("task/a")).rejects.toThrow(
      "invalid canonical task start status response",
    );
    fetchMock.mockResolvedValueOnce(
      json({ ...authority, selectionRequestId: "request/a" }),
    );
    await expect(fetchTaskStartStatus("task/a", "request/a")).rejects.toThrow(
      "invalid start request receipt response",
    );
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("edits content with the caller's CAS revision and exact content", async () => {
    fetchMock.mockResolvedValue(json({ id: "artifact/a" }));
    await editArtifactContent("artifact/a", "new content", "reason", 37);
    const sent = request();
    expect(sent.url.pathname).toBe("/api/artifacts/artifact%2Fa/content");
    expect(sent.method).toBe("PATCH");
    expect(sent.headers.get("If-Match-Revision")).toBe("37");
    expect(JSON.parse(sent.body)).toEqual({
      content: "new content",
      changeReason: "reason",
    });
  });
  it("restores the selected version with current CAS and propagates conflict without retry", async () => {
    fetchMock.mockResolvedValue(
      new Response("revision_conflict", { status: 409 }),
    );
    await expect(
      restoreArtifactVersion("artifact/a", "version/old", 38),
    ).rejects.toThrow("revision_conflict");
    expect(request().url.pathname).toBe(
      "/api/artifacts/artifact%2Fa/versions/version%2Fold/restore",
    );
    expect(request().method).toBe("POST");
    expect(request().headers.get("If-Match-Revision")).toBe("38");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("reads an existing recovery request and discovers by exact task/run/revision using GET", async () => {
    const receipt = { recoveryRequestId: "request/a", state: "confirmed" };
    fetchMock.mockImplementation(async () => json(receipt));
    expect(await fetchRecoveryRequest("task/a", "request/a")).toEqual(receipt);
    expect(request().url.pathname).toBe(
      "/api/tasks/task%2Fa/recovery-requests/request%2Fa",
    );
    expect(request().method).toBe("GET");
    await discoverRecoveryRequest("task/a", "run/a", 42);
    expect(request().url.pathname).toBe(
      "/api/tasks/task%2Fa/recovery-requests",
    );
    expect(Object.fromEntries(request().url.searchParams)).toEqual({
      sourceRunId: "run/a",
      expectedTaskRevision: "42",
    });
    expect(request().method).toBe("GET");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it.each(["disconnect", "malformed"])(
    "%s cannot become a successful recovery receipt",
    async (kind) => {
      if (kind === "disconnect")
        fetchMock.mockRejectedValue(new Error("disconnected"));
      else fetchMock.mockResolvedValue(new Response("broken JSON"));
      await expect(
        fetchRecoveryRequest("task", "request"),
      ).rejects.toMatchObject({ responseUnknown: true });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );
  it("sends reconnect cursor and rejects mismatched SSE identities across chunk boundaries", async () => {
    const good = {
      id: "accepted-2",
      type: "snapshot",
      taskId: "task/a",
      revision: 9,
      payload: {},
    };
    const frames = `id: bad-id\nevent: snapshot\ndata: ${JSON.stringify(good)}\n\nid: accepted-2\nevent: wrong-type\ndata: ${JSON.stringify(good)}\n\nid: accepted-2\nevent: snapshot\ndata: ${JSON.stringify(good)}\n\n`;
    const bytes = new TextEncoder().encode(frames);
    fetchMock.mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(bytes.slice(0, 17));
            controller.enqueue(bytes.slice(17, 81));
            controller.enqueue(bytes.slice(81));
            controller.close();
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    const received = vi.fn();
    await subscribeTaskEvents("task/a", received, undefined, "accepted-1");
    expect(request().headers.get("Last-Event-ID")).toBe("accepted-1");
    expect(received).toHaveBeenCalledTimes(1);
    expect(received).toHaveBeenCalledWith(good);
  });
});
