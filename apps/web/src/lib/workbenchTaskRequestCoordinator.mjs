import { RateLimitBackoff, parseRetryAfter } from "./rateLimitBackoff.mjs";

const coordinators = new Map();
const TERMINAL = new Set(["completed", "succeeded", "failed", "cancelled", "stopped", "deleted"]);

function keyOf(ownerId, workspaceId, taskId) { return `${ownerId}\0${workspaceId}\0${taskId}`; }

export class WorkbenchTaskRequestCoordinator {
  constructor(options) {
    this.ownerId = options.ownerId;
    this.workspaceId = options.workspaceId;
    this.taskId = options.taskId;
    this._registryKey = keyOf(this.ownerId, this.workspaceId, this.taskId);
    this.generation = 0;
    this.subscribers = new Set();
    this.leases = 0;
    this.eventSource = null;
    this.sseState = "closed";
    this.lastEventId = null;
    this.backoff = new RateLimitBackoff({ now: options.now, random: options.random, baseMs: options.baseBackoffMs, capMs: options.maxBackoffMs });
    this.backoffUntil = 0;
    this.consecutiveRateLimits = 0;
    this.circuitState = "closed";
    this.inFlightRequests = new Map();
    this.scheduledRequests = new Map();
    this.managedStreams = new Map();
    this.fallbackPollTimer = null;
    this.visibilityState = options.visibilityState ?? (globalThis.document?.visibilityState ?? "visible");
    this.abortControllers = new Set();
    this.options = options;
    this._probeTimer = null;
    this._probeOwner = null;
    this._nextProbeToken = 1;
    this._nextRequestEpoch = 1;
    this._legacyProbeToken = null;
    this._visibilityHandler = () => this.setVisibility(globalThis.document?.visibilityState ?? "visible");
    if (globalThis.document?.addEventListener) globalThis.document.addEventListener("visibilitychange", this._visibilityHandler);
  }

  _syncBackoff() {
    this.backoffUntil = this.backoff.backoffUntil;
    this.consecutiveRateLimits = this.backoff.consecutiveRateLimits;
    this.circuitState = this.backoff.circuitState;
  }

  _claimProbe(owner) {
    if (!this.backoff.beginProbe()) return null;
    const token = {
      id: this._nextProbeToken++,
      generation: this.generation,
      taskId: this.taskId,
      ...owner,
    };
    this._probeOwner = token;
    this._syncBackoff();
    return token;
  }

  _probeIsCurrent(token) {
    return Boolean(
      token
      && this._probeOwner === token
      && token.generation === this.generation
      && token.taskId === this.taskId,
    );
  }

  _settleProbe(token, outcome, retryAfterMs = 5_000) {
    if (!this._probeIsCurrent(token)) return false;
    this._probeOwner = null;
    if (outcome === "success") {
      this.backoff.success();
    } else if (outcome === "failure") {
      this.backoff.probeFailed(retryAfterMs);
    } else {
      this.backoff.probeInFlight = false;
      if (this.backoff.circuitState === "half-open") this.backoff.circuitState = "open";
    }
    this._syncBackoff();
    return true;
  }

  _abandonProbe(outcome = "aborted") {
    const token = this._probeOwner;
    if (!token) return;
    this._settleProbe(token, outcome);
  }

  _setTimeout(callback, delay) {
    const schedule = this.options.setTimeout ?? globalThis.window?.setTimeout ?? globalThis.setTimeout;
    return schedule(callback, delay);
  }

  _clearTimeout(timer) {
    const clear = this.options.clearTimeout ?? globalThis.window?.clearTimeout ?? globalThis.clearTimeout;
    clear(timer);
  }

  _clearTimer() { if (this._probeTimer !== null) { this._clearTimeout(this._probeTimer); this._probeTimer = null; } }
  _scheduleProbe() {
    this._clearTimer();
    if (!this.subscribers.size && !this.leases) return;
    const delay = Math.max(0, this.backoffUntil - (this.options.now?.() ?? Date.now()));
    this._probeTimer = this._setTimeout(() => {
      this._probeTimer = null;
      const canProbeWithSse = this.subscribers.size > 0 && typeof this.options.connectSse === "function";
      const probeToken = canProbeWithSse
        ? this._claimProbe({ kind: "legacy-sse", resource: "task-sse" })
        : null;
      if (probeToken) {
        this._connectSse(probeToken);
      }
    }, delay);
  }

  _abortProbeRequests() {
    for (const [resource, entry] of this.inFlightRequests) {
      const token = entry.probeToken;
      if (!token || token.kind !== "request" || !this._probeIsCurrent(token)) continue;
      this._settleProbe(token, "aborted");
      entry.probeToken = null;
      entry.controller?.abort?.();
      if (this.inFlightRequests.get(resource) === entry) this.inFlightRequests.delete(resource);
    }
  }

  _hasTransportConsumers() {
    if (this.subscribers.size || this.leases) return true;
    for (const entry of this.managedStreams.values()) {
      if (entry.subscribers.size) return true;
    }
    return false;
  }

  _cleanupUnobservedProbe() {
    if (this._hasTransportConsumers()) return;
    this._abortProbeRequests();
    this._clearTimer();
  }

  _abortInFlight() {
    this._abortProbeRequests();
    for (const controller of this.abortControllers) controller.abort();
    this.abortControllers.clear();
    this.inFlightRequests.clear();
  }

  _closeSse() {
    if (this._legacyProbeToken) {
      this._settleProbe(this._legacyProbeToken, "aborted");
      this._legacyProbeToken = null;
    }
    if (this.eventSource?.close) this.eventSource.close();
    this.eventSource = null;
    this.sseState = "closed";
  }

  _connectSse(probeToken = null) {
    if (!this.subscribers.size || this.eventSource || this.visibilityState === "hidden") return;
    if (this.backoff.isBlocked() && !probeToken) { this._scheduleProbe(); return; }
    const generation = this.generation;
    const connect = this.options.connectSse;
    if (typeof connect !== "function") return;
    this.sseState = "connecting";
    this._legacyProbeToken = probeToken;
    const source = connect({ taskId: this.taskId, lastEventId: this.lastEventId, generation });
    this.eventSource = source;
    if (source) source.onopen = () => {
      this.sseState = "open";
      if (probeToken) {
        this._settleProbe(probeToken, "success");
        if (this._legacyProbeToken === probeToken) this._legacyProbeToken = null;
      }
    };
    if (source) source.onmessage = (event) => this._handleSseEvent(event, generation);
    if (source) source.onerror = () => {
      if (generation !== this.generation) return;
      if (probeToken) {
        this._settleProbe(probeToken, "failure");
        if (this._legacyProbeToken === probeToken) this._legacyProbeToken = null;
      }
      this.sseState = "reconnecting";
      this._closeSse();
      if (!this.backoff.isBlocked()) this._connectSse();
      else this._scheduleProbe();
    };
  }

  _handleSseEvent(event, generation) {
    if (generation !== this.generation) return;
    const id = event?.lastEventId ?? event?.id ?? event?.data?.id;
    if (id) this.lastEventId = id;
    for (const subscriber of this.subscribers) subscriber(event);
    const data = typeof event?.data === "string" ? (() => { try { return JSON.parse(event.data); } catch { return event.data; } })() : event?.data;
    const status = data?.status ?? event?.status;
    if (TERMINAL.has(status)) this.close();
  }

  _managedStreamIsCurrent(entry) {
    return this.managedStreams.get(entry.resource) === entry
      && this.isGenerationCurrent(entry.generation, entry.taskId);
  }

  _clearManagedStreamTimer(entry, field) {
    if (entry[field] === null) return;
    this._clearTimeout(entry[field]);
    entry[field] = null;
  }

  _notifyManagedStreamStale(entry, stale) {
    if (entry.stale === stale) return;
    entry.stale = stale;
    for (const subscriber of entry.subscribers) subscriber.onStaleChange?.(stale);
  }

  _refreshManagedStreamOptions(entry) {
    const subscribers = [...entry.subscribers];
    entry.staleAfterMs = subscribers.reduce(
      (max, subscriber) => Math.max(max, Math.max(0, Number(subscriber.staleAfterMs) || 0)),
      0,
    );
    const fallbackIntervals = subscribers
      .map((subscriber) => Math.max(1_000, Number(subscriber.fallbackPollIntervalMs) || 2_000));
    entry.fallbackPollIntervalMs = fallbackIntervals.length ? Math.min(...fallbackIntervals) : 2_000;
    if (entry.staleAfterMs <= 0) {
      this._clearManagedStreamTimer(entry, "staleTimer");
      this._stopManagedStreamFallback(entry);
      this._notifyManagedStreamStale(entry, false);
    }
  }

  _stopManagedStreamFallback(entry) {
    this._clearManagedStreamTimer(entry, "fallbackTimer");
  }

  _armManagedStreamFallback(entry) {
    if (!this._managedStreamIsCurrent(entry)
      || entry.fallbackTimer !== null
      || !entry.stale
      || entry.terminalClosed
      || this.visibilityState === "hidden") return;
    entry.fallbackTimer = this._setTimeout(() => {
      entry.fallbackTimer = null;
      if (!this._managedStreamIsCurrent(entry)
        || !entry.stale
        || entry.terminalClosed
        || this.visibilityState === "hidden") return;
      for (const subscriber of entry.subscribers) subscriber.onFallbackPoll?.();
      this._armManagedStreamFallback(entry);
    }, Math.max(entry.fallbackPollIntervalMs, this.backoffUntil - (this.options.now?.() ?? Date.now())));
  }

  _armManagedStreamStale(entry) {
    this._clearManagedStreamTimer(entry, "staleTimer");
    if (!this._managedStreamIsCurrent(entry)
      || entry.staleAfterMs <= 0
      || entry.terminalClosed
      || this.visibilityState === "hidden") return;
    entry.staleTimer = this._setTimeout(() => {
      entry.staleTimer = null;
      if (!this._managedStreamIsCurrent(entry)
        || entry.terminalClosed
        || this.visibilityState === "hidden") return;
      this._notifyManagedStreamStale(entry, true);
      this._armManagedStreamFallback(entry);
    }, entry.staleAfterMs);
  }

  touchManagedStream(resource) {
    const entry = this.managedStreams.get(resource);
    if (!entry || !this._managedStreamIsCurrent(entry)) return;
    this._notifyManagedStreamStale(entry, false);
    this._stopManagedStreamFallback(entry);
    this._armManagedStreamStale(entry);
  }

  _disposeManagedStream(entry, remove = true) {
    if (entry.probeToken) {
      this._settleProbe(entry.probeToken, "aborted");
      entry.probeToken = null;
    }
    entry.connectionEpoch += 1;
    entry.controller?.abort?.();
    entry.controller = null;
    entry.promise = null;
    this._clearManagedStreamTimer(entry, "reconnectTimer");
    this._clearManagedStreamTimer(entry, "staleTimer");
    this._clearManagedStreamTimer(entry, "fallbackTimer");
    entry.resumeReconnect = false;
    entry.state = "closed";
    if (remove && this.managedStreams.get(entry.resource) === entry) {
      this.managedStreams.delete(entry.resource);
    }
  }

  _disposeAllManagedStreams() {
    for (const entry of this.managedStreams.values()) this._disposeManagedStream(entry, false);
    this.managedStreams.clear();
  }

  _managedStreamRetryDelay(entry, error) {
    const status = error?.status;
    const rateLimited = status === 429 || error?.code === "rate_limited";
    if (rateLimited) {
      const retryAfterMs = Number.isFinite(error?.retryAfterMs)
        ? error.retryAfterMs
        : Number.isFinite(error?.retryAfter)
          ? Math.max(0, error.retryAfter - (this.options.now?.() ?? Date.now()))
          : 5000;
      this.backoff.retryAfter(retryAfterMs);
      this._syncBackoff();
      return Math.max(0, this.backoffUntil - (this.options.now?.() ?? Date.now()));
    }
    entry.reconnectAttempt += 1;
    return Math.min(30_000, 2_000 * 2 ** Math.max(0, entry.reconnectAttempt - 1));
  }

  _scheduleManagedStreamReconnect(entry, delayMs = 0) {
    if (!this._managedStreamIsCurrent(entry)
      || !entry.subscribers.size
      || entry.terminalClosed) return;
    if (this.visibilityState === "hidden") {
      entry.resumeReconnect = true;
      return;
    }
    const now = this.options.now?.() ?? Date.now();
    const dueAt = Math.max(now + Math.max(0, delayMs), this.backoffUntil);
    if (entry.reconnectTimer !== null && entry.nextReconnectAt <= dueAt) return;
    this._clearManagedStreamTimer(entry, "reconnectTimer");
    entry.nextReconnectAt = dueAt;
    const generation = entry.generation;
    const taskId = entry.taskId;
    const epoch = entry.connectionEpoch;
    entry.reconnectTimer = this._setTimeout(() => {
      entry.reconnectTimer = null;
      if (!this._managedStreamIsCurrent(entry)
        || generation !== this.generation
        || taskId !== this.taskId
        || epoch !== entry.connectionEpoch
        || !entry.subscribers.size
        || entry.terminalClosed) return;
      if (this.visibilityState === "hidden") {
        entry.resumeReconnect = true;
        return;
      }
      this._startManagedStream(entry);
    }, Math.max(0, dueAt - now));
  }

  _startManagedStream(entry) {
    if (!this._managedStreamIsCurrent(entry)
      || !entry.subscribers.size
      || entry.promise
      || entry.controller
      || entry.terminalClosed) return;
    if (this.visibilityState === "hidden") {
      entry.resumeReconnect = true;
      return;
    }
    const now = this.options.now?.() ?? Date.now();
    const needsCircuitProbe = this.backoff.circuitState !== "closed";
    if (needsCircuitProbe) {
      if (now < this.backoffUntil || this.backoff.probeInFlight) {
        const delay = now < this.backoffUntil ? this.backoffUntil - now : 1_000;
        this._scheduleManagedStreamReconnect(entry, delay);
        return;
      }
    }

    this._clearManagedStreamTimer(entry, "reconnectTimer");
    entry.resumeReconnect = false;
    entry.state = "connecting";
    entry.connectionEpoch += 1;
    const epoch = entry.connectionEpoch;
    const generation = entry.generation;
    const taskId = entry.taskId;
    const controller = new AbortController();
    entry.controller = controller;
    const probeToken = needsCircuitProbe
      ? this._claimProbe({
          kind: "managed-stream",
          resource: entry.resource,
          connectionEpoch: epoch,
        })
      : null;
    if (needsCircuitProbe && !probeToken) {
      entry.controller = null;
      entry.state = "reconnecting";
      this._scheduleManagedStreamReconnect(entry, Math.max(1_000, this.backoffUntil - now));
      return;
    }
    entry.probeToken = probeToken;
    const emit = (event) => {
      if (!this._managedStreamIsCurrent(entry)
        || epoch !== entry.connectionEpoch
        || controller.signal.aborted) return;
      const id = event?.lastEventId ?? event?.id ?? event?.data?.id;
      if (id) {
        entry.lastEventId = id;
        this.lastEventId = id;
      }
      entry.state = "open";
      entry.reconnectAttempt = 0;
      if (probeToken) {
        this._settleProbe(probeToken, "success");
        if (entry.probeToken === probeToken) entry.probeToken = null;
      }
      for (const subscriber of entry.subscribers) subscriber.onEvent(event);
      const data = typeof event?.data === "string"
        ? (() => { try { return JSON.parse(event.data); } catch { return event.data; } })()
        : event?.data;
      const status = data?.status ?? event?.status;
      if (TERMINAL.has(status) || event?.type === "task_deleted") this.markManagedStreamTerminal(entry.resource);
    };

    let started;
    try {
      started = entry.connect({
        signal: controller.signal,
        onEvent: emit,
        lastEventId: entry.lastEventId,
        generation,
        taskId,
        connectionEpoch: epoch,
      });
    } catch (error) {
      started = Promise.reject(error);
    }
    const promise = Promise.resolve(started)
      .then(() => ({ error: null }))
      .catch((error) => ({ error }))
      .then(({ error }) => {
        if (!this._managedStreamIsCurrent(entry)
          || epoch !== entry.connectionEpoch
          || generation !== this.generation
          || taskId !== this.taskId) return;
        entry.controller = null;
        entry.promise = null;
        if (controller.signal.aborted || entry.terminalClosed || !entry.subscribers.size) return;
        const status = error?.status;
        if (status === 401 || status === 403 || status === 404) {
          entry.terminalClosed = true;
          entry.state = "closed";
          for (const subscriber of entry.subscribers) subscriber.onError?.(error);
          return;
        }
        if (error) for (const subscriber of entry.subscribers) subscriber.onError?.(error);
        let delay;
        if (probeToken) {
          const rateLimited = status === 429 || error?.code === "rate_limited";
          const retryAfterMs = rateLimited
            ? Number.isFinite(error?.retryAfterMs)
              ? error.retryAfterMs
              : Number.isFinite(error?.retryAfter)
                ? Math.max(0, error.retryAfter - (this.options.now?.() ?? Date.now()))
                : 5_000
            : 5_000;
          this._settleProbe(probeToken, "failure", retryAfterMs);
          if (entry.probeToken === probeToken) entry.probeToken = null;
          delay = Math.max(0, this.backoffUntil - (this.options.now?.() ?? Date.now()));
        } else {
          delay = this._managedStreamRetryDelay(entry, error);
        }
        entry.state = "reconnecting";
        this._scheduleManagedStreamReconnect(entry, delay);
      });
    entry.promise = promise;
  }

  subscribeManagedStream(resource, connect, onEvent, options = {}) {
    let entry = this.managedStreams.get(resource);
    if (!entry || !this._managedStreamIsCurrent(entry)) {
      if (entry) this._disposeManagedStream(entry);
      entry = {
        resource,
        taskId: this.taskId,
        generation: this.generation,
        subscribers: new Set(),
        connect,
        controller: null,
        promise: null,
        reconnectTimer: null,
        staleTimer: null,
        fallbackTimer: null,
        reconnectAttempt: 0,
        nextReconnectAt: 0,
        connectionEpoch: 0,
        probeToken: null,
        resumeReconnect: false,
        terminalClosed: false,
        state: "closed",
        lastEventId: this.lastEventId,
        stale: false,
        staleAfterMs: Math.max(0, Number(options.staleAfterMs) || 0),
        fallbackPollIntervalMs: Math.max(1_000, Number(options.fallbackPollIntervalMs) || 2_000),
      };
      this.managedStreams.set(resource, entry);
    }
    entry.connect = connect;
    const subscriber = {
      onEvent,
      onError: options.onError,
      onStaleChange: options.onStaleChange,
      onFallbackPoll: options.onFallbackPoll,
      staleAfterMs: options.staleAfterMs,
      fallbackPollIntervalMs: options.fallbackPollIntervalMs,
    };
    entry.subscribers.add(subscriber);
    this._refreshManagedStreamOptions(entry);
    subscriber.onStaleChange?.(entry.stale);
    if (entry.subscribers.size === 1) {
      this._startManagedStream(entry);
      this._armManagedStreamStale(entry);
    }
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      entry.subscribers.delete(subscriber);
      if (!entry.subscribers.size) {
        this._disposeManagedStream(entry);
        this._cleanupUnobservedProbe();
      } else this._refreshManagedStreamOptions(entry);
    };
  }

  markManagedStreamTerminal(resource) {
    const entry = this.managedStreams.get(resource);
    if (!entry) return;
    entry.terminalClosed = true;
    this._disposeManagedStream(entry, false);
  }

  acquire() {
    this.leases += 1;
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      this.leases = Math.max(0, this.leases - 1);
      if (this.leases === 0 && this.subscribers.size === 0) this.close();
    };
  }

  isGenerationCurrent(generation, taskId = this.taskId) {
    return generation === this.generation && taskId === this.taskId;
  }

  subscribe(onEvent) {
    this.subscribers.add(onEvent);
    if (this.subscribers.size === 1) this._connectSse();
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      this.subscribers.delete(onEvent);
      if (!this.subscribers.size) {
        this._closeSse();
        this._cleanupUnobservedProbe();
      }
    };
  }

  request(resource, request, options = {}) {
    const generation = this.generation;
    const taskId = this.taskId;
    const existing = this.inFlightRequests.get(resource);
    if (existing) return existing.promise;
    let probeToken = null;
    const requestEpoch = this._nextRequestEpoch++;
    if (this.visibilityState === "hidden" && options.allowWhenHidden !== true) {
      return Promise.reject(Object.assign(new Error("Task request paused while page is hidden"), { code: "page_hidden" }));
    }
    if (this.backoff.circuitState !== "closed") {
      const now = this.options.now?.() ?? Date.now();
      if (now < this.backoffUntil || this.backoff.probeInFlight) {
        this._scheduleProbe();
        return Promise.reject(Object.assign(new Error("Task request is rate limited"), { code: "rate_limited", retryAfter: this.backoffUntil }));
      }
      probeToken = this._claimProbe({ kind: "request", resource, requestEpoch });
      if (!probeToken) {
        this._scheduleProbe();
        return Promise.reject(Object.assign(new Error("Task request is rate limited"), { code: "rate_limited", retryAfter: this.backoffUntil }));
      }
    }
    const controller = new AbortController();
    this.abortControllers.add(controller);
    const requestEntry = {
      resource,
      controller,
      generation,
      taskId,
      requestEpoch,
      probeToken,
      promise: null,
    };
    let started;
    try { started = request(controller.signal); } catch (error) { started = Promise.reject(error); }
    const promise = Promise.resolve(started).then(async (response) => {
      if (!this.isGenerationCurrent(generation, taskId)) return undefined;
      if (controller.signal.aborted) {
        throw controller.signal.reason ?? new DOMException("aborted", "AbortError");
      }
      if (response?.status === 429) {
        let body = null; try { body = await response.clone().json(); } catch { /* empty */ }
        const retryAfterMs = parseRetryAfter({ headers: response.headers, body }, this.options.now?.() ?? Date.now());
        throw Object.assign(new Error("API rate limit exceeded"), { code: "rate_limited", status: 429, retryAfterMs });
      }
      if (response?.ok === false) throw Object.assign(new Error(`HTTP ${response.status}`), { status: response.status });
      if (options.parse) return options.parse(response);
      return response;
    }).catch((error) => {
      if (!this.isGenerationCurrent(generation, taskId)) {
        if (error?.name === "AbortError") throw error;
        return undefined;
      }
      const status = error?.status;
      const isRateLimited = status === 429 || error?.code === "rate_limited";
      if (isRateLimited) {
        const retryAfterMs = Number.isFinite(error?.retryAfterMs)
          ? error.retryAfterMs
          : Number.isFinite(error?.retryAfter)
            ? Math.max(0, error.retryAfter - (this.options.now?.() ?? Date.now()))
            : 5000;
        if (probeToken) {
          if (this._settleProbe(probeToken, "failure", retryAfterMs)) this._scheduleProbe();
        } else {
          this.backoff.retryAfter(retryAfterMs);
          this._syncBackoff();
          this._scheduleProbe();
        }
      } else if (probeToken && this._settleProbe(probeToken, "failure", 5000)) {
        this._scheduleProbe();
      }
      throw error;
    }).then((value) => {
      if (!this.isGenerationCurrent(generation, taskId)) return undefined;
      if (probeToken && this._settleProbe(probeToken, "success")) this._connectSse();
      return value;
    }).finally(() => {
      this.abortControllers.delete(controller);
      if (this.inFlightRequests.get(resource) === requestEntry) this.inFlightRequests.delete(resource);
    });
    requestEntry.promise = promise;
    this.inFlightRequests.set(resource, requestEntry);
    return promise;
  }


  _clearScheduledRequests(remove = false) {
    const clear = this.options.clearTimeout ?? clearTimeout;
    for (const [resource, entry] of this.scheduledRequests) {
      if (entry.timer !== null) clear(entry.timer);
      entry.timer = null;
      if (remove) this.scheduledRequests.delete(resource);
    }
  }

  _armScheduledRequest(resource) {
    const entry = this.scheduledRequests.get(resource);
    if (!entry || entry.timer !== null || this.visibilityState === "hidden") return;
    if (!this.isGenerationCurrent(entry.generation, entry.taskId)) {
      this.scheduledRequests.delete(resource);
      return;
    }
    const now = this.options.now?.() ?? Date.now();
    const delay = Math.max(0, entry.dueAt - now, this.backoffUntil - now);
    entry.timer = (this.options.setTimeout ?? setTimeout)(() => {
      entry.timer = null;
      const current = this.scheduledRequests.get(resource);
      if (current !== entry) return;
      if (!this.isGenerationCurrent(entry.generation, entry.taskId)) {
        this.scheduledRequests.delete(resource);
        return;
      }
      // A visibility change can race the timer callback. Retain the scheduled
      // request while hidden so setVisibility("visible") can re-arm it.
      if (this.visibilityState === "hidden") return;
      this.scheduledRequests.delete(resource);
      void entry.callback();
    }, delay);
  }

  schedule(resource, callback, delayMs = 0) {
    this.cancelSchedule(resource);
    const now = this.options.now?.() ?? Date.now();
    this.scheduledRequests.set(resource, {
      callback,
      dueAt: now + Math.max(0, Number(delayMs) || 0),
      timer: null,
      generation: this.generation,
      taskId: this.taskId,
    });
    this._armScheduledRequest(resource);
    return () => this.cancelSchedule(resource);
  }

  cancelSchedule(resource) {
    const entry = this.scheduledRequests.get(resource);
    if (!entry) return;
    if (entry.timer !== null) (this.options.clearTimeout ?? clearTimeout)(entry.timer);
    this.scheduledRequests.delete(resource);
  }

  cancel(resource) {
    const current = this.inFlightRequests.get(resource);
    if (!current) return;
    if (current.probeToken) {
      this._settleProbe(current.probeToken, "aborted");
      current.probeToken = null;
    }
    current.controller?.abort?.();
    if (this.inFlightRequests.get(resource) === current) this.inFlightRequests.delete(resource);
  }

  fetchTask(request, options) { return this.request("task", request, options); }
  fetchCollaboration(request, options) { return this.request("collaboration", request, options); }
  authoritativeRefresh(request, options) { return this.request("authoritative", request, options); }

  setVisibility(state) {
    const wasHidden = this.visibilityState === "hidden";
    this.visibilityState = state;
    if (state === "hidden") {
      this._clearTimer();
      this._abortProbeRequests();
      this._closeSse();
      for (const entry of this.managedStreams.values()) {
        entry.resumeReconnect = Boolean(entry.subscribers.size && !entry.terminalClosed);
        if (entry.probeToken) {
          this._settleProbe(entry.probeToken, "aborted");
          entry.probeToken = null;
        }
        entry.connectionEpoch += 1;
        entry.controller?.abort?.();
        entry.controller = null;
        entry.promise = null;
        this._clearManagedStreamTimer(entry, "reconnectTimer");
        this._clearManagedStreamTimer(entry, "staleTimer");
        this._clearManagedStreamTimer(entry, "fallbackTimer");
        entry.state = "paused";
      }
      this._clearScheduledRequests(false);
      if (this.fallbackPollTimer !== null) { (this.options.clearInterval ?? clearInterval)(this.fallbackPollTimer); this.fallbackPollTimer = null; }
      return;
    }
    if (wasHidden) {
      for (const resource of this.scheduledRequests.keys()) this._armScheduledRequest(resource);
      for (const entry of this.managedStreams.values()) {
        if (!this._managedStreamIsCurrent(entry) || !entry.subscribers.size || entry.terminalClosed) continue;
        this._armManagedStreamStale(entry);
        if (entry.stale) this._armManagedStreamFallback(entry);
        if (entry.resumeReconnect || (!entry.promise && !entry.controller)) this._startManagedStream(entry);
      }
      if (this.subscribers.size) {
        this.authoritativeRefresh(this.options.refresh ?? (() => Promise.resolve())).catch(() => {});
        this._connectSse();
      }
    }
  }

  rebind(taskId) {
    if (taskId === this.taskId) return;
    this._abandonProbe();
    this.generation += 1; this._abortInFlight(); this._closeSse(); this._disposeAllManagedStreams(); this._clearTimer(); this._clearScheduledRequests(true);
    if (this.fallbackPollTimer !== null) { (this.options.clearInterval ?? clearInterval)(this.fallbackPollTimer); this.fallbackPollTimer = null; }
    coordinators.delete(this._registryKey);
    this.taskId = taskId;
    this._registryKey = keyOf(this.ownerId, this.workspaceId, this.taskId);
    coordinators.set(this._registryKey, this);
    this.lastEventId = null; this.backoff = new RateLimitBackoff({ now: this.options.now, random: this.options.random }); this._syncBackoff();
    if (this.subscribers.size) this._connectSse();
  }

  markTerminal(status) { if (TERMINAL.has(status)) this.close(); }

  close() {
    this._abandonProbe();
    this.generation += 1;
    this._abortInFlight(); this._closeSse(); this._disposeAllManagedStreams(); this._clearTimer(); this._clearScheduledRequests(true);
    if (this.fallbackPollTimer !== null) { (this.options.clearInterval ?? clearInterval)(this.fallbackPollTimer); this.fallbackPollTimer = null; }
    if (globalThis.document?.removeEventListener) globalThis.document.removeEventListener("visibilitychange", this._visibilityHandler);
    this.subscribers.clear();
    this.leases = 0;
    coordinators.delete(this._registryKey);
  }
}

export function getWorkbenchTaskRequestCoordinator(ownerId, workspaceId, taskId, options = {}) {
  const key = keyOf(ownerId, workspaceId, taskId);
  let coordinator = coordinators.get(key);
  if (!coordinator) { coordinator = new WorkbenchTaskRequestCoordinator({ ...options, ownerId, workspaceId, taskId }); coordinators.set(key, coordinator); }
  return coordinator;
}

export function resetWorkbenchTaskRequestCoordinators() { for (const coordinator of coordinators.values()) coordinator.close(); coordinators.clear(); }
export function workbenchTaskRequestCoordinatorKey(ownerId, workspaceId, taskId) { return keyOf(ownerId, workspaceId, taskId); }
