const MIN_RETRY_AFTER_MS = 1_000;
const MAX_RETRY_AFTER_MS = 5 * 60_000;

export function clampRetryAfterMs(value, minMs = MIN_RETRY_AFTER_MS, maxMs = MAX_RETRY_AFTER_MS) {
  return Math.min(maxMs, Math.max(minMs, Math.round(value)));
}

export function parseRetryAfter(response, now = Date.now()) {
  const header = response?.headers?.get?.("Retry-After");
  if (header) {
    const seconds = Number.parseInt(header.trim(), 10);
    if (Number.isFinite(seconds)) return clampRetryAfterMs(seconds * 1000);
    const date = Date.parse(header);
    if (Number.isFinite(date)) return clampRetryAfterMs(date - now);
  }
  const body = response?.retryAfter ?? response?.body?.retryAfter;
  if (typeof body === "number" && Number.isFinite(body)) return clampRetryAfterMs(body * 1000);
  if (typeof body === "string" && body.trim() && Number.isFinite(Number(body))) {
    return clampRetryAfterMs(Number(body) * 1000);
  }
  return 5_000;
}

export function nextBackoffMs(consecutiveRateLimits, options = {}) {
  const base = options.baseMs ?? 1_000;
  const cap = options.capMs ?? 5 * 60_000;
  const jitterRatio = options.jitterRatio ?? 0.2;
  const random = options.random ?? Math.random;
  const exponential = Math.min(cap, base * 2 ** Math.max(0, consecutiveRateLimits - 1));
  const jitter = exponential * jitterRatio * (random() * 2 - 1);
  return Math.min(cap, Math.max(base, Math.round(exponential + jitter)));
}

export class RateLimitBackoff {
  constructor(options = {}) {
    this.now = options.now ?? (() => Date.now());
    this.random = options.random ?? Math.random;
    this.baseMs = options.baseMs ?? 1_000;
    this.capMs = options.capMs ?? 5 * 60_000;
    this.backoffUntil = 0;
    this.consecutiveRateLimits = 0;
    this.circuitState = "closed";
    this.probeInFlight = false;
  }

  isBlocked() { return this.now() < this.backoffUntil || this.circuitState === "open"; }

  retryAfter(retryAfterMs) {
    const now = this.now();
    this.consecutiveRateLimits += 1;
    const exponential = nextBackoffMs(this.consecutiveRateLimits, {
      baseMs: this.baseMs,
      capMs: this.capMs,
      random: this.random,
    });
    const delay = Math.max(clampRetryAfterMs(retryAfterMs), exponential);
    this.backoffUntil = Math.max(this.backoffUntil, now + Math.min(this.capMs, delay));
    this.circuitState = "open";
    this.probeInFlight = false;
    return this.backoffUntil;
  }

  beginProbe() {
    if (this.now() < this.backoffUntil || this.circuitState === "closed" || this.probeInFlight) return false;
    this.probeInFlight = true;
    this.circuitState = "half-open";
    return true;
  }

  success() {
    this.backoffUntil = 0;
    this.consecutiveRateLimits = 0;
    this.circuitState = "closed";
    this.probeInFlight = false;
  }

  probeFailed(retryAfterMs) {
    this.probeInFlight = false;
    this.retryAfter(retryAfterMs);
  }
}

export const retryAfterBounds = { MIN_RETRY_AFTER_MS, MAX_RETRY_AFTER_MS };
