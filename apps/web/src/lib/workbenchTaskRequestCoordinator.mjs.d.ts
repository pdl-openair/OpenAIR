export declare class WorkbenchTaskRequestCoordinator {
  ownerId: string;
  workspaceId: string;
  taskId: string;
  generation: number;
  subscribers: Set<unknown>;
  leases: number;
  eventSource: unknown;
  sseState: string;
  lastEventId: string | null;
  backoffUntil: number;
  consecutiveRateLimits: number;
  circuitState: string;
  inFlightRequests: Map<string, unknown>;
  managedStreams: Map<string, unknown>;
  fallbackPollTimer: unknown;
  visibilityState: string;
  abortControllers: Set<AbortController>;
  constructor(options: Record<string, unknown>);
  acquire(): () => void;
  isGenerationCurrent(generation: number, taskId?: string): boolean;
  subscribe(onEvent: (event: unknown) => void): () => void;
  subscribeManagedStream(
    resource: string,
    connect: (context: {
      signal: AbortSignal;
      onEvent: (event: unknown) => void;
      lastEventId: string | null;
      generation: number;
      taskId: string;
      connectionEpoch: number;
    }) => unknown,
    onEvent: (event: unknown) => void,
    options?: {
      onError?: (error: unknown) => void;
      onStaleChange?: (stale: boolean) => void;
      onFallbackPoll?: () => void;
      staleAfterMs?: number;
      fallbackPollIntervalMs?: number;
    },
  ): () => void;
  touchManagedStream(resource: string): void;
  markManagedStreamTerminal(resource: string): void;
  request(resource: string, request: (signal: AbortSignal) => unknown, options?: Record<string, unknown>): Promise<unknown>;
  cancel(resource: string): void;
  fetchTask(request: (signal: AbortSignal) => unknown, options?: Record<string, unknown>): Promise<unknown>;
  fetchCollaboration(request: (signal: AbortSignal) => unknown, options?: Record<string, unknown>): Promise<unknown>;
  authoritativeRefresh(request: (signal: AbortSignal) => unknown, options?: Record<string, unknown>): Promise<unknown>;
  setVisibility(state: string): void;
  rebind(taskId: string): void;
  markTerminal(status: string): void;
  close(): void;
}
export declare function getWorkbenchTaskRequestCoordinator(ownerId: string, workspaceId: string, taskId: string, options?: Record<string, unknown>): WorkbenchTaskRequestCoordinator;
export declare function resetWorkbenchTaskRequestCoordinators(): void;
export declare function workbenchTaskRequestCoordinatorKey(ownerId: string, workspaceId: string, taskId: string): string;
