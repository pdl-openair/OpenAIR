import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LegacyTaskRouteRedirect } from "../../LegacyTaskRouteRedirect";
function Harness() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="route">{location.pathname + location.search}</output>
      <button onClick={() => navigate(-1)}>back</button>
      <Routes>
        <Route path="/legacy/:taskId" element={<LegacyTaskRouteRedirect />} />
        <Route path="/legacy" element={<LegacyTaskRouteRedirect />} />
        <Route path="/workbench/*" element={<div>workbench</div>} />
        <Route path="/origin" element={<div>origin</div>} />
      </Routes>
    </>
  );
}
describe("legacy Task route identity", () => {
  it("preserves encoded Task identity and query while replacing the legacy history entry", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          "/origin",
          "/legacy/task%2F%E7%A0%94%E7%A9%B6?artifact=artifact-a&planningSessionId=session-a",
        ]}
        initialIndex={1}
      >
        <Harness />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent(
        "/workbench/task%2F%E7%A0%94%E7%A9%B6?artifact=artifact-a&planningSessionId=session-a",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "back" }));
    await waitFor(() =>
      expect(screen.getByTestId("route")).toHaveTextContent("/origin"),
    );
  });
  it("does not invent a Task when the legacy link has no Task identity", async () => {
    render(
      <MemoryRouter initialEntries={["/legacy"]}>
        <Harness />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("route").textContent).toBe("/workbench"),
    );
  });
});
