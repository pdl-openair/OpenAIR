import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type SetStateAction,
} from "react";

/** A context transition invalidates old state, including an A -> B -> A revisit. */
export function useWorkbenchSessionState<T>(contextKey: string, initial: T) {
  const identity = useMemo(() => ({ contextKey }), [contextKey]);
  const currentIdentityRef = useRef<typeof identity | null>(null);
  useLayoutEffect(() => {
    currentIdentityRef.current = identity;
    return () => {
      currentIdentityRef.current = null;
    };
  }, [identity]);
  const [stored, setStored] = useState({ identity, value: initial });
  const value = stored.identity === identity ? stored.value : initial;
  const update = useCallback(
    (next: SetStateAction<T>) => {
      if (currentIdentityRef.current !== identity) return;
      setStored((previous) => {
        const current =
          previous.identity === identity ? previous.value : initial;
        return {
          identity,
          value:
            typeof next === "function"
              ? (next as (value: T) => T)(current)
              : next,
        };
      });
    },
    [identity, initial],
  );
  return [value, update] as const;
}

export function useWorkbenchSessionFields<T>(contextKey: string, initial: T) {
  const [state, update] = useWorkbenchSessionState(contextKey, initial);
  return {
    field<K extends keyof T>(key: K) {
      const setField = (next: SetStateAction<T[K]>) =>
        update((previous) => ({
          ...previous,
          [key]:
            typeof next === "function"
              ? (next as (value: T[K]) => T[K])(previous[key])
              : next,
        }));
      return [state[key], setField] as const;
    },
  };
}
