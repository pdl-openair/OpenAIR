import {
  fetchArtifactContent,
  type ArtifactContentResponse,
} from "../api/client";
import { getApiBaseUrl, getAuthToken } from "../api/http";
import type { WorkbenchArtifact } from "../model/workbenchTask";

const PREFIX = "openair.artifact-body.v2:";
const LIMIT = 16;
const persisted = new Set<string>();
const bodies = new Map<string, ArtifactContentResponse>();
const opaquePrincipals = new Map<string, number>();
const pending = new Map<string, Promise<ArtifactContentResponse | null>>();

function principal(): string {
  const token = getAuthToken();
  if (!token) return "anonymous";
  try {
    const claims = JSON.parse(
      atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
    ) as { sub?: string; workspaceId?: string };
    if (claims.sub)
      return JSON.stringify([claims.sub, claims.workspaceId ?? ""]);
  } catch {
    /* Non-JWT development credentials use memory only. */
  }
  if (!opaquePrincipals.has(token))
    opaquePrincipals.set(token, opaquePrincipals.size + 1);
  return `opaque-${opaquePrincipals.get(token)}`;
}

export function artifactBodyKey(
  taskId: string,
  artifact: WorkbenchArtifact,
): string {
  return JSON.stringify([
    getApiBaseUrl(),
    principal(),
    taskId,
    artifact.id,
    artifact.currentVersionId ?? "",
    artifact.versionCount ?? 0,
    artifact.createdAt ?? "",
  ]);
}

export function readArtifactBody(
  key: string,
): ArtifactContentResponse | undefined {
  const memory = bodies.get(key);
  if (memory) {
    try {
      if (!persisted.has(key) || sessionStorage.getItem(PREFIX + key) !== null)
        return memory;
      bodies.delete(key);
      persisted.delete(key);
    } catch {
      return memory;
    }
  }
  if (key.includes('"opaque-')) return undefined;
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    if (!raw) return undefined;
    const value = JSON.parse(raw) as ArtifactContentResponse;
    if (
      typeof value.content !== "string" ||
      typeof value.artifactId !== "string"
    )
      return undefined;
    bodies.set(key, value);
    persisted.add(key);
    while (bodies.size > LIMIT) bodies.delete(bodies.keys().next().value!);
    return value;
  } catch (error) {
    console.warn(
      "Artifact body cache could not be read",
      error instanceof Error ? error.name : "storage error",
    );
    return undefined;
  }
}

function remember(key: string, value: ArtifactContentResponse): void {
  bodies.delete(key);
  bodies.set(key, value);
  while (bodies.size > LIMIT) bodies.delete(bodies.keys().next().value!);
  if (key.includes('"opaque-')) return;
  try {
    const keys = Object.keys(sessionStorage).filter((item) =>
      item.startsWith(PREFIX),
    );
    while (keys.length >= LIMIT) sessionStorage.removeItem(keys.shift()!);
    sessionStorage.setItem(PREFIX + key, JSON.stringify(value));
    persisted.add(key);
  } catch (error) {
    console.warn(
      "Artifact body cache could not be saved",
      error instanceof Error ? error.name : "storage error",
    );
  }
}

export async function loadArtifactBody(
  taskId: string,
  artifact: WorkbenchArtifact,
  force = false,
): Promise<ArtifactContentResponse | null> {
  const key = artifactBodyKey(taskId, artifact);
  if (!force) {
    const cached = readArtifactBody(key);
    if (cached) return cached;
  }
  const existing = pending.get(key);
  if (existing) return existing;
  const request = fetchArtifactContent(taskId, artifact.id).then((value) => {
    if (value) remember(key, value);
    return value;
  });
  pending.set(key, request);
  try {
    return await request;
  } finally {
    pending.delete(key);
  }
}
