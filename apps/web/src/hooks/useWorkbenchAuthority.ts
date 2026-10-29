import { useEffect, useMemo, useSyncExternalStore } from "react";

type AuthoritySnapshot<T> = {
  data: T | null;
  unavailable: boolean;
};

/** A remote authority has its own request lifetime, separate from UI state. */
function createAuthority<T>(
  read: () => Promise<T>,
  retainOnError: boolean,
  enabled: boolean,
) {
  let snapshot: AuthoritySnapshot<T> = { data: null, unavailable: false };
  let generation = 0;
  let active = false;
  const listeners = new Set<() => void>();
  const publish = (next: AuthoritySnapshot<T>) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  const refresh = async (): Promise<T | null> => {
    if (!enabled || !active) return null;
    const requestedGeneration = ++generation;
    try {
      const data = await read();
      if (!active || requestedGeneration !== generation) return null;
      publish({ data, unavailable: false });
      return data;
    } catch {
      if (!active || requestedGeneration !== generation) return null;
      publish({
        data: retainOnError ? snapshot.data : null,
        unavailable: true,
      });
      return null;
    }
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    refresh,
    start: () => {
      active = true;
      void refresh();
      return () => {
        active = false;
        generation += 1;
      };
    },
  };
}

/** The memoized reader binds task/revision identity; cleanup rejects late reads. */
export function useWorkbenchAuthority<T>({
  read,
  enabled,
  refreshSignal,
  retainOnError = false,
}: {
  read: () => Promise<T>;
  enabled: boolean;
  refreshSignal?: string;
  retainOnError?: boolean;
}) {
  const authority = useMemo(
    () => createAuthority(read, retainOnError, enabled),
    [read, retainOnError, enabled],
  );
  const snapshot = useSyncExternalStore(
    authority.subscribe,
    authority.getSnapshot,
    authority.getSnapshot,
  );
  useEffect(() => {
    if (!enabled) return;
    return authority.start();
  }, [authority, enabled, refreshSignal]);
  return { ...snapshot, refresh: authority.refresh };
}
