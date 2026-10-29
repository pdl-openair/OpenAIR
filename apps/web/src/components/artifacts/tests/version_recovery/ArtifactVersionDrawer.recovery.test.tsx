import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({
  history: vi.fn(),
  content: vi.fn(),
  edit: vi.fn(),
  restore: vi.fn(),
}));
vi.mock("../../../../api/client", async (original) => ({
  ...(await original<typeof import("../../../../api/client")>()),
  fetchArtifactVersions: api.history,
  fetchArtifactContentById: api.content,
  editArtifactContent: api.edit,
  restoreArtifactVersion: api.restore,
}));
vi.mock("../../StreamdownMarkdown", () => ({
  StreamdownMarkdown: ({ content }: { content: string }) => (
    <div>{content}</div>
  ),
}));
import { ArtifactVersionDrawer } from "../../ArtifactVersionDrawer";
const versions = [
  {
    versionId: "v-old",
    versionNumber: 1,
    source: "initial",
    createdBy: "user",
    createdAt: "2026-10-09T00:00:00Z",
  },
  {
    versionId: "v-current",
    versionNumber: 2,
    source: "user_edit",
    createdBy: "user",
    createdAt: "2026-10-09T01:00:00Z",
  },
];
const changed = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getStyle(element),
  );
  api.history.mockResolvedValue({
    revision: 37,
    currentVersionId: "v-current",
    versions,
  });
  api.content.mockResolvedValue({ content: "current content" });
  api.edit.mockResolvedValue({});
  api.restore.mockResolvedValue({});
});
afterEach(() => vi.restoreAllMocks());
function open(editable = true) {
  return render(
    <ArtifactVersionDrawer
      open
      artifactId="artifact-1"
      editable={editable}
      canRegenerate={false}
      onClose={() => {}}
      onChanged={changed}
    />,
  );
}
describe("real version drawer recovery", () => {
  it("saves using fetched revision, then the next edit uses refreshed revision", async () => {
    open();
    await screen.findByDisplayValue("current content");
    const input = screen.getAllByRole("textbox")[0];
    fireEvent.change(input, { target: { value: "edit one" } });
    api.history.mockResolvedValue({
      revision: 38,
      currentVersionId: "v-new",
      versions,
    });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(api.edit).toHaveBeenNthCalledWith(
      1,
      "artifact-1",
      "edit one",
      undefined,
      37,
    );
    fireEvent.change(input, { target: { value: "edit two" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
    expect(api.edit).toHaveBeenNthCalledWith(
      2,
      "artifact-1",
      "edit two",
      undefined,
      38,
    );
  });
  it("restores the selected historical version with the fetched revision and refreshes history", async () => {
    open(false);
    const restore = await screen.findByRole("button", { name: "恢复此版本" });
    api.history.mockResolvedValue({
      revision: 38,
      currentVersionId: "v-restored",
      versions: [
        ...versions,
        {
          ...versions[0],
          versionId: "v-restored",
          versionNumber: 3,
          source: "restore",
        },
      ],
    });
    fireEvent.click(restore);
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(api.restore).toHaveBeenCalledWith("artifact-1", "v-old", 37);
    expect(api.history).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("v3")).toBeVisible();
    expect(screen.getByText("v1")).toBeVisible();
    expect(screen.getByText("v2")).toBeVisible();
  });
  it("conflict is visible and cannot report success or silently retry", async () => {
    api.restore.mockRejectedValue(new Error("版本冲突，请刷新后重试"));
    open(false);
    fireEvent.click(await screen.findByRole("button", { name: "恢复此版本" }));
    expect(await screen.findByText("版本冲突，请刷新后重试")).toBeVisible();
    expect(changed).not.toHaveBeenCalled();
    expect(api.restore).toHaveBeenCalledTimes(1);
    expect(api.history).toHaveBeenCalledTimes(1);
  });
  it("missing authoritative revision prevents restoration", async () => {
    api.history.mockResolvedValue({
      revision: null,
      currentVersionId: "v-current",
      versions,
    });
    open(false);
    fireEvent.click(await screen.findByRole("button", { name: "恢复此版本" }));
    expect(
      await screen.findByText("版本状态已失效，请重新打开版本历史后再试"),
    ).toBeVisible();
    expect(api.restore).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
  });
});
