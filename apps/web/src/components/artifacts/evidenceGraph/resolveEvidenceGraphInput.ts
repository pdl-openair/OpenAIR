import type {
  WorkbenchArtifact,
  WorkbenchCapabilityCall,
  WorkbenchTaskModel,
} from "../../../model/workbenchTask";

const ASSESSMENT_ARTIFACT_TYPE = "evidence_assessment_and_research_synthesis";
const RESEARCH_RECORDS_ARTIFACT_TYPE = "research_records";
const ASSESSMENT_SKILL_IDS = new Set([
  "ot_v3_evidence_assessment_synthesis",
  "ot_v3_evidence_assessment_synthesis_skill",
]);

function normalizedIdentity(value?: string | null): string {
  return (value ?? "").trim().toLowerCase().replace(/-/g, "_");
}

function artifactType(artifact: WorkbenchArtifact): string {
  return normalizedIdentity(artifact.artifactType ?? artifact.type);
}

function producerCall(
  artifact: WorkbenchArtifact,
  model?: WorkbenchTaskModel,
): WorkbenchCapabilityCall | null {
  if (!model) return null;
  if (artifact.sourceCapabilityCallId) {
    return (
      model.capabilityCalls.find(
        (call) => call.id === artifact.sourceCapabilityCallId,
      ) ?? null
    );
  }
  return (
    model.capabilityCalls.find(
      (call) =>
        call.outputArtifactRefs?.includes(artifact.id) ||
        call.artifactRefs?.includes(artifact.id),
    ) ?? null
  );
}

export function isEvidenceAssessmentArtifact(
  artifact: WorkbenchArtifact,
  model?: WorkbenchTaskModel,
): boolean {
  if (artifactType(artifact) === ASSESSMENT_ARTIFACT_TYPE) return true;
  const skillId = normalizedIdentity(
    artifact.relatedSkillId ?? producerCall(artifact, model)?.name,
  );
  return ASSESSMENT_SKILL_IDS.has(skillId);
}

/**
 * Resolve all ResearchRecord artifacts formally supplied to the producer
 * call that created this evidence assessment. We intentionally do not fall
 * back to "the latest" research_records artifact: that would create a new
 * provenance association that is not present in the task graph.
 */
export function resolveEvidenceGraphResearchRecordsArtifacts(
  assessmentArtifact: WorkbenchArtifact,
  model?: WorkbenchTaskModel,
): WorkbenchArtifact[] {
  if (!model || !isEvidenceAssessmentArtifact(assessmentArtifact, model)) {
    return [];
  }

  const call = producerCall(assessmentArtifact, model);
  if (!call) return [];

  const inputIds = new Set(call.inputArtifactRefs ?? []);
  for (const inputCallRef of call.inputCallRefs ?? []) {
    const upstream = model.capabilityCalls.find(
      (candidate) => candidate.id === inputCallRef,
    );
    for (const artifactId of [
      ...(upstream?.outputArtifactRefs ?? []),
      ...(upstream?.artifactRefs ?? []),
    ]) {
      inputIds.add(artifactId);
    }
  }
  if (inputIds.size === 0) return [];

  return model.artifacts.filter(
    (artifact) =>
      inputIds.has(artifact.id) &&
      artifactType(artifact) === RESEARCH_RECORDS_ARTIFACT_TYPE,
  );
}
