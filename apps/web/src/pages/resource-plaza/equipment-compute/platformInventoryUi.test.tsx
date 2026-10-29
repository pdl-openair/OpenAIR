import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import EquipmentResourcesPage from "../EquipmentResourcesPage";
import { fallbackFluxInventory } from "./data";
import { fetchFluxInventory } from "./api";
import { usePlatformInventory } from "./usePlatformInventory";
import type { FluxSnapshot } from "./types";

vi.mock("./api", () => ({
  fetchFluxInventory: vi.fn(),
  fetchJccInventory: vi.fn(),
  requestJccRefresh: vi.fn(),
}));

const liveFlux: FluxSnapshot = {
  observedAt: "2026-10-18T12:00:00+00:00",
  templates: [
    {
      id: "1",
      templateName: "实时模板A",
      taskType: "4",
      labId: "lab-1",
      labName: null,
      experimentDesc: null,
      estimateDays: 3,
      priority: "medium",
    },
    {
      id: "2",
      templateName: "实时模板B",
      taskType: "1",
      labId: "lab-2",
      labName: null,
      experimentDesc: null,
      estimateDays: 5,
      priority: "low",
    },
  ],
  templateParams: { "1": [], "2": [] },
  pvdSubRecipes: [
    {
      id: 1,
      subRecipeTypeId: 2,
      subRecipeId: "2",
      subRecipeName: "LL Auto Pump.dat",
      groupName: "Auto Pump",
      enabled: "1",
      sortOrder: 10,
      remark: null,
    },
  ],
  partialErrors: [],
};

function ProbeInventory({ timeoutMs }: { timeoutMs?: number } = {}) {
  const { data, usingFallback, error, reload } = usePlatformInventory<
    FluxSnapshot | null
  >(
    (signal) =>
      fetchFluxInventory(signal).catch(() => {
        if (signal.aborted) throw new DOMException("aborted", "AbortError");
        throw new Error("platform unreachable");
      }),
    null,
    timeoutMs,
  );
  return (
    <div>
      <span data-testid="probe-source">
        {usingFallback ? "fallback" : data ? "live" : "loading"}
      </span>
      <span data-testid="probe-count">
        {data ? data.templates.length : fallbackFluxInventory.templates.length}
      </span>
      <span data-testid="probe-error">{error ?? ""}</span>
      <button type="button" onClick={reload}>
        reload
      </button>
    </div>
  );
}

describe("usePlatformInventory", () => {
  it("实时访问成功时展示 live 数据", async () => {
    vi.mocked(fetchFluxInventory).mockResolvedValueOnce(liveFlux);
    render(<ProbeInventory />);
    expect(await screen.findByTestId("probe-source")).toHaveTextContent("live");
    expect(screen.getByTestId("probe-count")).toHaveTextContent("2");
  });

  it("实时访问失败时回落本地快照并标记 usingFallback", async () => {
    vi.mocked(fetchFluxInventory).mockRejectedValueOnce(
      new Error("platform unreachable"),
    );
    render(<ProbeInventory />);
    expect(await screen.findByTestId("probe-source")).toHaveTextContent(
      "fallback",
    );
  });

  it("超时中断回落快照并展示固定超时文案（不泄漏原始 abort 报错）", async () => {
    vi.mocked(fetchFluxInventory).mockImplementationOnce(
      (signal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(
              new DOMException("signal is aborted without reason", "AbortError"),
            ),
          );
        }),
    );
    render(<ProbeInventory timeoutMs={30} />);
    await waitFor(() =>
      expect(screen.getByTestId("probe-source")).toHaveTextContent("fallback"),
    );
    expect(screen.getByTestId("probe-error")).toHaveTextContent("请求超时");
    expect(screen.getByTestId("probe-error")).not.toHaveTextContent(
      "aborted without reason",
    );
  });

  it("reload 触发重新拉取", async () => {
    vi.mocked(fetchFluxInventory).mockResolvedValueOnce(liveFlux);
    render(<ProbeInventory />);
    await screen.findByTestId("probe-source");
    vi.mocked(fetchFluxInventory).mockResolvedValueOnce({
      ...liveFlux,
      templates: liveFlux.templates.slice(0, 1),
    });
    await act(async () => {
      screen.getByRole("button", { name: "reload" }).click();
    });
    await screen.findByText("1", { selector: '[data-testid="probe-count"]' });
  });
});

describe("EquipmentResourcesPage", () => {
  it("渲染云实验平台卡片（实时接入）", async () => {
    vi.mocked(fetchFluxInventory).mockResolvedValueOnce(liveFlux);
    render(
      <MemoryRouter>
        <EquipmentResourcesPage />
      </MemoryRouter>,
    );
    expect(
      await screen.findByTestId("flux-platform-card"),
    ).toBeInTheDocument();
    expect(screen.getByText("实时接入")).toBeInTheDocument();
    expect(screen.getByText("实时模板A")).toBeInTheDocument();
  });

  it("实时失败时展示快照兜底标记与本地快照数据", async () => {
    vi.mocked(fetchFluxInventory).mockRejectedValueOnce(
      new Error("platform unreachable"),
    );
    render(
      <MemoryRouter>
        <EquipmentResourcesPage />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText("快照兜底", { selector: ".ant-tag" }),
    ).toBeInTheDocument();
    // 兜底数据来自打包内 fluxSnapshot.json（真实采集，5 个模板）
    expect(screen.getByText("实验模板")).toBeInTheDocument();
    expect(screen.getByText("flux任务流程")).toBeInTheDocument();
  });
});
