import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchStartSessionStatus } from "../../../api/client";
import { PageHeaderProvider } from "../../../context/PageHeaderContext";
import {
  readCurrentTask,
  readResearchStartPending,
  rememberResearchStartPending,
} from "../../../lib/userRouteState";
const mocks = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("../../../api/client", () => ({
  fetchResearchStartSession: mocks.fetch,
}));
import ResearchStartWaitingPage from "../../ResearchStartWaitingPage";

function completion(
  overrides: Partial<ResearchStartSessionStatus> = {},
): ResearchStartSessionStatus {
  return {
    sessionId: "session-a",
    status: "completed",
    reservedTaskId: "task-a",
    result: {
      outcome: "task_created",
      sessionId: "session-a",
      taskId: "task-a",
    },
    ...overrides,
  };
}
function Harness() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="route">{location.pathname + location.search}</output>
      <button onClick={() => navigate("/waiting/session-b")}>
        switch session
      </button>
      <Routes>
        <Route
          path="/waiting/:sessionId"
          element={<ResearchStartWaitingPage />}
        />
        <Route
          path="/workbench/:taskId"
          element={<div>current workbench</div>}
        />
      </Routes>
    </>
  );
}
function renderWaiting() {
  return render(
    <MemoryRouter initialEntries={["/waiting/session-a"]}>
      <PageHeaderProvider>
        <Harness />
      </PageHeaderProvider>
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  rememberResearchStartPending({
    status: "accepted",
    startedAt: 1,
    input: "研究问题",
    clientRequestId: "request-a",
    intentFingerprint: "intent-a",
    sessionId: "session-a",
    reservedTaskId: "task-a",
  });
  mocks.fetch.mockResolvedValue(completion());
});
describe("research completion Task and session identity", () => {
  it("opens only the completed reserved Task and preserves its planning session", async () => {
    renderWaiting();
    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/workbench/task-a?planningSessionId=session-a",
      ),
    );
    expect(readCurrentTask()).toBe("task-a");
    expect(readResearchStartPending()).toBeNull();
    expect(mocks.fetch).toHaveBeenCalledWith("session-a");
  });
  it.each([
    {
      name: "different session",
      response: completion({ sessionId: "other-session" }),
      message: "读取到的研究会话与当前页面不一致，已停止打开任务。",
    },
    {
      name: "different reservation",
      response: completion({ reservedTaskId: "task-b" }),
      message: "当前研究会话返回了不一致的任务身份，已停止打开任务。",
    },
    {
      name: "different result Task",
      response: completion({
        result: {
          outcome: "task_created",
          sessionId: "session-a",
          taskId: "task-b",
        },
      }),
      message: "完成结果与当前研究会话预留的任务不一致，已停止打开任务。",
    },
  ])("does not navigate on $name", async ({ response, message }) => {
    mocks.fetch.mockResolvedValue(response);
    renderWaiting();
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.getByTestId("route")).toHaveTextContent("/waiting/session-a");
    expect(readCurrentTask()).toBeUndefined();
    expect(readResearchStartPending()?.reservedTaskId).toBe("task-a");
  });
  it("ignores Session A completion after Session B has opened its own Task", async () => {
    let resolve!: (value: ResearchStartSessionStatus) => void;
    const first = new Promise<ResearchStartSessionStatus>((yes) => {
      resolve = yes;
    });
    mocks.fetch.mockImplementation((id: string) =>
      id === "session-a"
        ? first
        : Promise.resolve(
            completion({
              sessionId: "session-b",
              reservedTaskId: "task-b",
              result: {
                outcome: "task_created",
                sessionId: "session-b",
                taskId: "task-b",
              },
            }),
          ),
    );
    renderWaiting();
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith("session-a"));
    fireEvent.click(screen.getByRole("button", { name: "switch session" }));
    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/workbench/task-b?planningSessionId=session-b",
      ),
    );
    await act(async () => resolve(completion()));
    expect(screen.getByTestId("route")).toHaveTextContent(
      "/workbench/task-b?planningSessionId=session-b",
    );
    expect(readCurrentTask()).toBe("task-b");
  });
});
