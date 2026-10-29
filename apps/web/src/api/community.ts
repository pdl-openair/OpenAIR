/**
 * Community (C1) API client.
 * Mirrors services/api/app/routes/community.py. Uses http.ts buildAuthHeaders +
 * parseJson (shares the global 401 handler).
 */
import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

export type CommunityResourceType = "task" | "artifact";

export interface CommunityPublication {
  publicationId: string;
  resourceType: CommunityResourceType;
  resourceId: string;
  publisherOwnerId: string;
  publisherDisplayName: string | null;
  publisherWorkspaceId: string;
  title: string;
  summary: string;
  /** Publisher's recommended fork granularity: 'full' (whole project) | 'template' (spec only). */
  recommendedGranularity: "full" | "template";
  createdAt: string;
  commentCount: number;
}

export interface CommunityPublicationPage {
  publications: CommunityPublication[];
  total: number;
  page: number;
  pageSize: number;
}

export interface CommunityComment {
  commentId: string;
  publicationId: string;
  authorOwnerId: string;
  authorDisplayName: string | null;
  body: string;
  parentCommentId: string | null;
  createdAt: string;
}

export interface CommunityCommentPage {
  comments: CommunityComment[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PublishToCommunityPayload {
  resourceType: CommunityResourceType;
  resourceId: string;
  title?: string;
  summary?: string;
  recommendedGranularity?: "full" | "template";
}

export interface ForkPublicationPayload {
  title?: string;
  includeArtifacts?: boolean;
  targetTaskId?: string;
}

export interface ForkPublicationResult {
  publicationId: string;
  resourceType: CommunityResourceType;
  taskId?: string;
  artifactId?: string;
}

export interface AddCommentPayload {
  body: string;
  parentCommentId?: string;
}

export interface FetchCommunityPublicationsParams {
  resourceType?: CommunityResourceType;
  q?: string;
  page?: number;
  pageSize?: number;
}

export async function fetchCommunityPublications(
  params: FetchCommunityPublicationsParams = {},
  signal?: AbortSignal,
): Promise<CommunityPublicationPage> {
  const query = new URLSearchParams();
  if (params.resourceType) query.set("resourceType", params.resourceType);
  if (params.q && params.q.trim()) query.set("q", params.q.trim());
  query.set("page", String(params.page ?? 1));
  query.set("pageSize", String(params.pageSize ?? 20));
  const res = await fetch(`${getApiBaseUrl()}/api/community/publications?${query.toString()}`, {
    headers: buildAuthHeaders(),
    signal,
  });
  return parseJson<CommunityPublicationPage>(res);
}

export async function fetchCommunityPublication(
  publicationId: string,
  signal?: AbortSignal,
): Promise<CommunityPublication> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/community/publications/${encodeURIComponent(publicationId)}`,
    { headers: buildAuthHeaders(), signal },
  );
  return parseJson<CommunityPublication>(res);
}

export interface CommunityTaskPreviewSubStage {
  subStage: string;
  label: string | null;
  status: string | null;
}

export interface CommunityTaskPreviewStage {
  stageKey: string;
  label: string | null;
  status: string | null;
  summary: string | null;
  subStages: CommunityTaskPreviewSubStage[];
}

export interface CommunityTaskPreviewArtifact {
  artifactId: string;
  title: string;
  artifactType: string | null;
  format: string | null;
}

export interface CommunityTaskPreview {
  goalSummary: string | null;
  domain: string | null;
  skillPack: string | null;
  stages: CommunityTaskPreviewStage[];
  artifacts: CommunityTaskPreviewArtifact[];
}

export interface CommunityArtifactPreview {
  title: string;
  mimeType: string | null;
  format: string | null;
  artifactType: string | null;
  content: string | null;
  contentTruncated: boolean;
}

export interface CommunityPreview {
  publicationId: string;
  resourceType: CommunityResourceType;
  task: CommunityTaskPreview | null;
  artifact: CommunityArtifactPreview | null;
}

export async function fetchCommunityPreview(
  publicationId: string,
  signal?: AbortSignal,
): Promise<CommunityPreview> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/community/publications/${encodeURIComponent(publicationId)}/preview`,
    { headers: buildAuthHeaders(), signal },
  );
  return parseJson<CommunityPreview>(res);
}

export async function fetchCommunityArtifactBody(
  publicationId: string,
  artifactId: string,
  signal?: AbortSignal,
): Promise<CommunityArtifactPreview> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/community/publications/${encodeURIComponent(publicationId)}/artifacts/${encodeURIComponent(artifactId)}/body`,
    { headers: buildAuthHeaders(), signal },
  );
  return parseJson<CommunityArtifactPreview>(res);
}

export async function publishToCommunity(payload: PublishToCommunityPayload): Promise<CommunityPublication> {
  const res = await fetch(`${getApiBaseUrl()}/api/community/publications`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return parseJson<CommunityPublication>(res);
}

export async function forkPublication(
  publicationId: string,
  payload: ForkPublicationPayload,
): Promise<ForkPublicationResult> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/community/publications/${encodeURIComponent(publicationId)}/fork`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    },
  );
  return parseJson<ForkPublicationResult>(res);
}

export async function fetchComments(
  publicationId: string,
  params: { page?: number; pageSize?: number } = {},
  signal?: AbortSignal,
): Promise<CommunityCommentPage> {
  const query = new URLSearchParams();
  query.set("page", String(params.page ?? 1));
  query.set("pageSize", String(params.pageSize ?? 50));
  const res = await fetch(
    `${getApiBaseUrl()}/api/community/publications/${encodeURIComponent(publicationId)}/comments?${query.toString()}`,
    { headers: buildAuthHeaders(), signal },
  );
  return parseJson<CommunityCommentPage>(res);
}

export async function addComment(
  publicationId: string,
  payload: AddCommentPayload,
): Promise<CommunityComment> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/community/publications/${encodeURIComponent(publicationId)}/comments`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    },
  );
  return parseJson<CommunityComment>(res);
}
