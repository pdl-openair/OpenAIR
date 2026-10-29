/** 与 fixtures/recorded_runs 下 task.json、stages.json 等对齐（snake_case，勿改 fixture 文件） */
export interface FixtureTaskJson {
  task_id: string;
  title: string;
  user_goal: string;
  domain: string;
  current_stage: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface FixtureSubStageJson {
  sub_stage: string;
  status: string;
  artifact_refs: string[];
}

export interface FixtureStageJson {
  stage: string;
  status: string;
  summary: string | null;
  risks: string | null;
  next_steps: string | null;
  artifact_refs: string[];
  sub_stages: FixtureSubStageJson[];
}

export interface FixtureArtifactEvidenceRefJson {
  id: string;
  claim: string;
  source_title: string;
  source_url: string;
  excerpt: string;
  source_identifier?: string;
  locator?: string;
}

export interface FixtureArtifactJson {
  artifact_id: string;
  type: string;
  title: string;
  format: string;
  path?: string;
  related_stage: string;
  related_skill_id?: string;
  evidence_refs?: FixtureArtifactEvidenceRefJson[];
}

export interface FixtureCapabilityCallJson {
  call_id: string;
  skill_id: string;
  runtime?: string;
  status: string;
  started_at?: string;
  ended_at?: string | null;
  artifact_refs?: string[];
  result_summary?: string | null;
}
