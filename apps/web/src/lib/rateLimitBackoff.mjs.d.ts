export declare function clampRetryAfterMs(value: number, minMs?: number, maxMs?: number): number;
export declare function parseRetryAfter(response: unknown, now?: number): number;
export declare function nextBackoffMs(consecutiveRateLimits: number, options?: Record<string, unknown>): number;
export declare class RateLimitBackoff {
  backoffUntil: number;
  consecutiveRateLimits: number;
  circuitState: "closed" | "open" | "half-open";
  probeInFlight: boolean;
  constructor(options?: Record<string, unknown>);
  isBlocked(): boolean;
  retryAfter(retryAfterMs: number): number;
  beginProbe(): boolean;
  success(): void;
  probeFailed(retryAfterMs: number): void;
}
export declare const retryAfterBounds: { MIN_RETRY_AFTER_MS: number; MAX_RETRY_AFTER_MS: number };
