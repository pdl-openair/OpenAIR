/**
 * Reader-facing presentation order for sibling Artifacts of one scope.
 *
 * When a substage mints several outputs at once (report + tables + figures +
 * the raw result JSON), the reader wants the most synthesized view first:
 * 报告 → 表格 → 图 → 原始数据. The authored/mint order says when records were
 * persisted, not what is worth reading first, so both the footer selector and
 * the center-view default focus stable-sort by this rank.
 */

const FORMAT_PRESENTATION_RANK = new Map([
  // 结论性报告最先
  ["markdown", 0],
  ["md", 0],
  ["html", 1],
  // 结构化表格次之
  ["csv", 2],
  ["tsv", 2],
  // 图示再次
  ["png", 3],
  ["jpg", 3],
  ["jpeg", 3],
  ["gif", 3],
  ["webp", 3],
  ["svg", 3],
  // 原始机器记录靠后，数据文件（cif/txt/…）垫底
  ["json", 4],
]);

/** Unknown formats rank last; ties keep their authored order (stable sort). */
export function formatPresentationRank(format) {
  const key = typeof format === "string" ? format.trim().toLowerCase() : "";
  return FORMAT_PRESENTATION_RANK.get(key) ?? 5;
}
