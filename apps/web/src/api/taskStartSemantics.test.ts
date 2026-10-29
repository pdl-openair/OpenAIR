import { describe, expect, it } from "vitest";

import {
  durableStartIntentStorageKey,
  settleDurableStartIntent,
  taskStartStatusMessage,
} from "./taskStartSemantics";


function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}


describe("durable task start semantics", () => {
  it("keeps accepted and running intents recoverable", () => {
    const storage = memoryStorage();
    const key = durableStartIntentStorageKey("task-a");
    storage.setItem(key, "selection-a");

    settleDurableStartIntent("task-a", "selection-a", "accepted", storage);
    expect(storage.getItem(key)).toBe("selection-a");
    settleDurableStartIntent("task-a", "selection-a", "running", storage);
    expect(storage.getItem(key)).toBe("selection-a");
  });

  it("clears only a terminal intent and gives visible pending feedback", () => {
    const storage = memoryStorage();
    const key = durableStartIntentStorageKey("task-a");
    storage.setItem(key, "selection-a");

    expect(taskStartStatusMessage("accepted")).toContain("已接收");
    expect(taskStartStatusMessage("running")).toContain("分发研究工作");
    expect(taskStartStatusMessage("running")).not.toContain("模型规划");
    settleDurableStartIntent("task-a", "selection-a", "committed", storage);
    expect(storage.getItem(key)).toBeNull();
  });
});
