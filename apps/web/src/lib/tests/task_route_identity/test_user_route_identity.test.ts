import { beforeEach, describe, expect, it } from "vitest";
import {
  readCurrentArtifact,
  readCurrentTask,
  rememberCurrentArtifact,
  rememberCurrentTask,
} from "../../userRouteState";

describe("durable Task route identity", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("restores the same Task and Artifact for a same-Task refresh or re-entry", () => {
    rememberCurrentTask("task-a");
    rememberCurrentArtifact("artifact-a");

    expect(readCurrentTask()).toBe("task-a");
    expect(readCurrentArtifact()).toBe("artifact-a");

    rememberCurrentTask("task-a");
    expect(readCurrentTask()).toBe("task-a");
    expect(readCurrentArtifact()).toBe("artifact-a");
  });

  it("clears the prior Artifact whenever the selected Task changes", () => {
    rememberCurrentTask("task-a");
    rememberCurrentArtifact("artifact-a");

    rememberCurrentTask("task-b");

    expect(readCurrentTask()).toBe("task-b");
    expect(readCurrentArtifact()).toBeUndefined();
  });
});
