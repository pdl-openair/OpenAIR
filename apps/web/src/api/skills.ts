/**
 * Skills API — system public skills + user private skills CRUD.
 */
import { getApiBaseUrl, buildAuthHeaders } from "./http";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SystemSkill {
  skill_id: string;
  claude_name: string;
  display_name: string;
  domain: string;
  stages: string[];
  substages: string[];
  skill_type: string;
  status: string;
  description: string;
}

export interface UserSkill {
  skill_name: string;
  description: string;
  created_at: string;
  files: string[];
}

export interface UserSkillDetail extends UserSkill {
  skill_yaml: Record<string, unknown> | null;
  skill_md_preview: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail = text;
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) {
        detail = body.detail;
      }
    } catch {
      // keep raw text
    }
    throw new Error(detail || `请求失败：HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// System skills (public, read-only)
// ---------------------------------------------------------------------------

export async function fetchSystemSkills(): Promise<SystemSkill[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/skills/system`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<SystemSkill[]>(res);
}

// ---------------------------------------------------------------------------
// User skills (private, authenticated)
// ---------------------------------------------------------------------------

export async function fetchUserSkills(): Promise<UserSkill[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/skills/user`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<UserSkill[]>(res);
}

export async function getUserSkill(skillName: string): Promise<UserSkillDetail> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/skills/user/${encodeURIComponent(skillName)}`,
    { headers: buildAuthHeaders() },
  );
  return parseJson<UserSkillDetail>(res);
}

/** A pending upload entry: the file plus its path relative to the skill root.
 *  Carrying the relative path (not just the basename) preserves subdirectory
 *  structure (scripts/, schemas/…) so a code-based skill tree survives upload. */
export interface SkillUploadEntry {
  file: File;
  relPath: string;
}

export async function uploadUserSkill(
  skillName: string,
  entries: SkillUploadEntry[],
): Promise<UserSkill> {
  const form = new FormData();
  form.append("skill_name", skillName);
  for (const entry of entries) {
    // The third arg sets the part's filename → the backend reads it as the
    // relative path and reconstructs the tree under user-skills/{owner}/{name}/.
    form.append("files", entry.file, entry.relPath);
  }

  const res = await fetch(`${getApiBaseUrl()}/api/skills/user`, {
    method: "POST",
    headers: buildAuthHeaders(),
    body: form,
  });
  return parseJson<UserSkill>(res);
}

export async function deleteUserSkill(skillName: string): Promise<void> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/skills/user/${encodeURIComponent(skillName)}`,
    {
      method: "DELETE",
      headers: buildAuthHeaders(),
    },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `删除失败：HTTP ${res.status}`);
  }
}
