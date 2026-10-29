import { useCallback, useEffect, useRef, useState } from "react";

export interface PlatformInventoryState<T> {
  data: T | null;
  /** 仅在尚无任何数据（首次加载）时为 true；轮询刷新不闪加载态。 */
  loading: boolean;
  /** true = 后端/平台不可达，当前展示本地一次性快照兜底。 */
  usingFallback: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * 平台清单通用加载 hook：实时访问优先，失败回落本地快照。
 * fetcher 用 ref 透传（调用方传内联 lambda 不会触发重跑），重跑只由 reload 驱动。
 * 带超时竞速（默认 20s）：后端/平台长时间无响应时尽快回落快照，页面不长时间转圈。
 */
const DEFAULT_TIMEOUT_MS = 20_000;

export function usePlatformInventory<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  fallback: T,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): PlatformInventoryState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [usingFallback, setUsingFallback] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;
  const hasDataRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    if (!hasDataRef.current) setLoading(true);
    fetcherRef
      .current(controller.signal)
      .then((value) => {
        if (!active) return;
        hasDataRef.current = true;
        setData(value);
        setUsingFallback(false);
        setError(null);
      })
      .catch((err: unknown) => {
        // 卸载后的拒绝（含 abort）由 active 拦截；超时/网络失败立即回落快照
        if (!active) return;
        hasDataRef.current = true;
        setData(fallbackRef.current);
        setUsingFallback(true);
        // 超时 abort 的原始报错（signal is aborted without reason）对用户无意义，换固定文案
        if (timedOut) {
          setError(`请求超时（${Math.round(timeoutMs / 1000)} 秒）`);
        } else if (err instanceof DOMException && err.name === "AbortError") {
          setError("请求已中止");
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        clearTimeout(timeoutId);
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [retryKey, timeoutMs]);

  const reload = useCallback(() => setRetryKey((key) => key + 1), []);

  return { data, loading, usingFallback, error, reload };
}
