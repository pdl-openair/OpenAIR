import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { message } from "antd";
import { fetchResourceSaveStatus, saveResource, unsaveResource } from "./api";

export function useResourceSaveState(resourceIds: string[]) {
  const idsKey = useMemo(() => [...new Set(resourceIds.filter(Boolean))].sort().join("\0"), [resourceIds]);
  const stableIds = useMemo(() => (idsKey ? idsKey.split("\0") : []), [idsKey]);
  const [savedIds, setSavedIds] = useState<Set<string>>(() => new Set());
  const [pendingIds, setPendingIds] = useState<Set<string>>(() => new Set());
  const pendingRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (stableIds.length === 0) {
      setSavedIds(new Set());
      return;
    }
    const controller = new AbortController();
    fetchResourceSaveStatus(stableIds, controller.signal)
      .then(result => {
        if (!controller.signal.aborted) setSavedIds(new Set(result.savedResourceIds));
      })
      .catch(error => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!controller.signal.aborted) setSavedIds(new Set());
      });
    return () => controller.abort();
  }, [idsKey]);

  const isSaved = useCallback((resourceId: string) => savedIds.has(resourceId), [savedIds]);
  const isPending = useCallback((resourceId: string) => pendingIds.has(resourceId), [pendingIds]);

  const toggleSave = useCallback(async (resourceId: string, nextSaved: boolean): Promise<boolean> => {
    const id = resourceId.trim();
    if (!id || pendingRef.current.has(id)) return false;
    pendingRef.current = new Set(pendingRef.current).add(id);
    setPendingIds(new Set(pendingRef.current));
    try {
      const result = nextSaved ? await saveResource(id) : await unsaveResource(id);
      setSavedIds(previous => {
        const next = new Set(previous);
        if (result.saved) next.add(id);
        else next.delete(id);
        return next;
      });
      return result.saved === nextSaved;
    } catch {
      message.error(nextSaved ? "保存失败，请稍后重试。" : "取消保存失败，请稍后重试。");
      return false;
    } finally {
      pendingRef.current = new Set([...pendingRef.current].filter(value => value !== id));
      setPendingIds(new Set(pendingRef.current));
    }
  }, []);

  const markUnsavedLocally = useCallback((resourceId: string) => {
    setSavedIds(previous => {
      if (!previous.has(resourceId)) return previous;
      const next = new Set(previous);
      next.delete(resourceId);
      return next;
    });
  }, []);

  return { isSaved, isPending, toggleSave, markUnsavedLocally, savedIds };
}
