import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../api/http";

const api = vi.hoisted(() => ({
  deleteTask: vi.fn(),
  fetchSkillPacks: vi.fn(),
  fetchTaskListPage: vi.fn(),
  renameTask: vi.fn()
}));

vi.mock("../../../api/client", () => ({
  ...api
}));

vi.mock("../../../context/AuthContext", () => ({
  useAuth: () => ({
    user: {
      ownerId: "owner-1",
      username: "tester",
      displayName: "Tester",
      email: "tester@example.com"
    },
    logout: vi.fn()
  })
}));

import TaskBoardPage from "../../TaskBoardPage";

function renderBoard() {
  return render(
    <MemoryRouter>
      <TaskBoardPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  api.fetchSkillPacks.mockResolvedValue([]);
  api.deleteTask.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TaskBoard load failures", () => {
  it.each([
    ["401", new ApiError("sensitive", 401), "会话已失效，请重新登录。"],
    ["403", new ApiError("sensitive", 403), "你无权访问这些项目。"],
    ["500", new ApiError("sensitive", 500), "项目暂时无法加载，请稍后重试。"],
    ["network", new TypeError("network detail"), "项目暂时无法加载，请稍后重试。"],
    [
      "contract",
      new ApiError("sensitive", 200, "response_contract_invalid"),
      "项目数据暂时不完整，请刷新后重试。"
    ]
  ])("shows a stable %s error without fixture or empty-board projection", async (_name, error, expected) => {
    api.fetchTaskListPage.mockRejectedValue(error);
    renderBoard();

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(screen.queryByText("sensitive")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fixture-disclosure")).not.toBeInTheDocument();
    expect(screen.queryByText("该筛选下暂无任务")).not.toBeInTheDocument();
  });
});
