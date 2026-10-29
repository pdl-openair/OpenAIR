const fs = require("fs");
const path = require("path");

const repoRoot = path.resolve(__dirname, "..");
const taskPath = path.join(repoRoot, "packages", "shared", "src", "task.ts");
const dictPath = path.join(repoRoot, "packages", "shared", "src", "i18n", "dict.ts");

function readUtf8(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function extractUnionValues(source, typeName) {
  const match = source.match(new RegExp(`export\\s+type\\s+${typeName}\\s*=([\\s\\S]*?);`));
  if (!match) {
    throw new Error(`Unable to find exported union type: ${typeName}`);
  }
  return [...match[1].matchAll(/"([^"]+)"/g)].map((item) => item[1]).sort();
}

function extractRecordKeys(source, constName) {
  const match = source.match(new RegExp(`const\\s+${constName}\\s*:\\s*Record<[^>]+>\\s*=\\s*{([\\s\\S]*?)};`));
  if (!match) {
    throw new Error(`Unable to find label record: ${constName}`);
  }
  return [...match[1].matchAll(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:/gm)].map((item) => item[1]).sort();
}

function diff(expected, actual) {
  return {
    missing: expected.filter((value) => !actual.includes(value)),
    extra: actual.filter((value) => !expected.includes(value)),
  };
}

function assertSameKeys(expectedName, expected, actualName, actual) {
  const result = diff(expected, actual);
  if (result.missing.length || result.extra.length) {
    throw new Error(
      [
        `${actualName} does not match ${expectedName}.`,
        `missing_from_${actualName}=${JSON.stringify(result.missing)}`,
        `extra_in_${actualName}=${JSON.stringify(result.extra)}`,
      ].join("\n")
    );
  }
}

const taskSource = readUtf8(taskPath);
const dictSource = readUtf8(dictPath);

const taskStatus = extractUnionValues(taskSource, "TaskStatus");
const zhStatusLabels = extractRecordKeys(dictSource, "ZH_STATUS_LABELS");
const enStatusLabels = extractRecordKeys(dictSource, "EN_STATUS_LABELS");

assertSameKeys("TaskStatus", taskStatus, "ZH_STATUS_LABELS", zhStatusLabels);
assertSameKeys("TaskStatus", taskStatus, "EN_STATUS_LABELS", enStatusLabels);

const output = [
  "TASK_STATUS_LABELS_ENUM_PARITY=PASS",
  `TaskStatus=${JSON.stringify(taskStatus)}`,
  `ZH_STATUS_LABELS=${JSON.stringify(zhStatusLabels)}`,
  `EN_STATUS_LABELS=${JSON.stringify(enStatusLabels)}`,
].join("\n");

console.log(output);

const outputFlagIndex = process.argv.indexOf("--output");
if (outputFlagIndex !== -1) {
  const outputPath = process.argv[outputFlagIndex + 1];
  if (!outputPath) {
    throw new Error("--output requires a path");
  }
  fs.writeFileSync(path.resolve(outputPath), `${output}\n`, "utf8");
}
