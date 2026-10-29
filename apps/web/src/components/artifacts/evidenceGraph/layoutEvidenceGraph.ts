import type { EvidenceGraphProjection } from "./projectEvidenceGraph";

export type EvidenceSide = "left" | "right";

export interface EvidencePlacement {
  recordId: string;
  side: EvidenceSide;
  x: number;
  y: number;
}

export interface JudgmentPlacement {
  viewKey: string;
  x: number;
  y: number;
}

export interface AuxiliaryPlacement {
  kind: "conflict" | "gap";
  viewKey: string;
  x: number;
  y: number;
}

export interface EvidenceGraphLayout {
  evidence: EvidencePlacement[];
  judgments: JudgmentPlacement[];
  auxiliary: AuxiliaryPlacement[];
  width: number;
  height: number;
}

export const EVIDENCE_NODE_WIDTH = 200;
export const JUDGMENT_NODE_WIDTH = 260;
export const AUXILIARY_NODE_WIDTH = 220;

const LEFT_X = 0;
const HORIZONTAL_GAP_LEFT = 60;
const HORIZONTAL_GAP_RIGHT = 80;
const JUDGMENT_X = LEFT_X + EVIDENCE_NODE_WIDTH + HORIZONTAL_GAP_LEFT;
const RIGHT_X = JUDGMENT_X + JUDGMENT_NODE_WIDTH + HORIZONTAL_GAP_RIGHT;
const CANVAS_WIDTH = RIGHT_X + EVIDENCE_NODE_WIDTH;
const JUDGMENT_START_Y = 40;
const JUDGMENT_SPACING_Y = 190;
const EVIDENCE_MIN_SPACING_Y = 120;
const AUX_START_X = 0;
const AUX_COLUMN_GAP = 290;
const AUX_ROW_GAP = 170;
const AUX_COLUMNS = 3;

function stableSide(recordId: string): EvidenceSide {
  const lastHexDigit = Number.parseInt(recordId.slice(-1), 16);
  return Number.isFinite(lastHexDigit) && lastHexDigit % 2 === 1
    ? "right"
    : "left";
}

function spreadPlacements(
  rows: Array<{ recordId: string; anchorY: number; side: EvidenceSide }>,
): EvidencePlacement[] {
  const output: EvidencePlacement[] = [];
  for (const side of ["left", "right"] as const) {
    let previousY = -Infinity;
    const sideRows = rows
      .filter((row) => row.side === side)
      .sort(
        (left, right) =>
          left.anchorY - right.anchorY ||
          left.recordId.localeCompare(right.recordId),
      );

    for (const row of sideRows) {
      const y = Math.max(30, row.anchorY, previousY + EVIDENCE_MIN_SPACING_Y);
      previousY = y;
      output.push({
        recordId: row.recordId,
        side,
        x: side === "left" ? LEFT_X : RIGHT_X,
        y,
      });
    }
  }
  return output.sort((left, right) =>
    left.recordId.localeCompare(right.recordId),
  );
}

/**
 * Stable, presentation-only layout. It creates no scientific identities or
 * relations; it only positions facts already present in EvidenceGraphProjection.
 */
export function buildEvidenceGraphLayout(
  projection: EvidenceGraphProjection,
): EvidenceGraphLayout {
  const judgmentIndex = new Map(
    projection.judgmentNodes.map((judgment, index) => [
      judgment.viewKey,
      index,
    ]),
  );
  const judgmentPlacements: JudgmentPlacement[] = projection.judgmentNodes.map(
    (judgment, index) => ({
      viewKey: judgment.viewKey,
      x: JUDGMENT_X,
      y: JUDGMENT_START_Y + index * JUDGMENT_SPACING_Y,
    }),
  );

  const connectedJudgments = new Map<string, number[]>();
  for (const relation of projection.relations) {
    const index = judgmentIndex.get(relation.targetJudgmentKey);
    if (index === undefined) continue;
    const current = connectedJudgments.get(relation.sourceRecordId) ?? [];
    current.push(index);
    connectedJudgments.set(relation.sourceRecordId, current);
  }

  const fallbackStartY = Math.max(
    60,
    JUDGMENT_START_Y +
      Math.max(0, projection.judgmentNodes.length - 1) * JUDGMENT_SPACING_Y,
  );
  let standaloneIndex = 0;
  const evidenceRows = projection.evidenceNodes.map((record) => {
    const connected = connectedJudgments.get(record.recordId) ?? [];
    const anchorY =
      connected.length > 0
        ? JUDGMENT_START_Y +
          (connected.reduce((sum, index) => sum + index, 0) /
            connected.length) *
            JUDGMENT_SPACING_Y
        : fallbackStartY + (standaloneIndex += 1) * EVIDENCE_MIN_SPACING_Y;
    return {
      recordId: record.recordId,
      side: stableSide(record.recordId),
      anchorY,
    };
  });
  const evidencePlacements = spreadPlacements(evidenceRows);

  const mainBottom = Math.max(
    judgmentPlacements.reduce((max, row) => Math.max(max, row.y + 190), 0),
    evidencePlacements.reduce((max, row) => Math.max(max, row.y + 130), 0),
    320,
  );
  const auxiliaryStartY = mainBottom + 130;
  const auxiliary: AuxiliaryPlacement[] = [
    ...projection.conflicts.map((row) => ({
      kind: "conflict" as const,
      viewKey: row.viewKey,
    })),
    ...projection.gaps.map((row) => ({
      kind: "gap" as const,
      viewKey: row.viewKey,
    })),
  ].map((row, index) => ({
    ...row,
    x: AUX_START_X + (index % AUX_COLUMNS) * AUX_COLUMN_GAP,
    y: auxiliaryStartY + Math.floor(index / AUX_COLUMNS) * AUX_ROW_GAP,
  }));

  const height = Math.max(
    mainBottom + 80,
    auxiliary.reduce((max, row) => Math.max(max, row.y + 170), 0) + 50,
  );

  return {
    evidence: evidencePlacements,
    judgments: judgmentPlacements,
    auxiliary,
    width: CANVAS_WIDTH,
    height,
  };
}
