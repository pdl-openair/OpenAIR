import {
  researchPresentationFieldLabel,
  researchPresentationScalarText,
} from "@openair/shared";
import { useState } from "react";
import { isSystemDiagnosticField } from "./structuredArtifactProjection";

const INTERNAL_FIELDS = new Set([
  "workUnitId",
  "work_unit_id",
  "sourceArtifactId",
  "source_artifact_id",
  "schemaVersion",
  "schema_version",
  "cache_key",
  "module_name",
  "machine_path",
  "internal_error",
]);

function visibleEntries(value: object): [string, unknown][] {
  return Object.entries(value).filter(
    ([key]) => !INTERNAL_FIELDS.has(key) && !isSystemDiagnosticField(key),
  );
}

/** Preserve scientific values without interpreting their status or exposing diagnostics. */
export function hasScientificResultContent(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "boolean") return true;
  if (Array.isArray(value)) return value.some(hasScientificResultContent);
  if (typeof value === "object")
    return visibleEntries(value).some(([, child]) =>
      hasScientificResultContent(child),
    );
  return false;
}

export function ScientificResultDisplayValue({ value }: { value: unknown }) {
  if (!hasScientificResultContent(value)) return null;
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return (
      <span className="whitespace-pre-wrap break-words">
        {researchPresentationScalarText(value)}
      </span>
    );
  }
  if (Array.isArray(value)) {
    return (
      <ul className="m-0 grid gap-2 pl-5">
        {value.map((item, index) =>
          hasScientificResultContent(item) ? (
            <li key={index}>
              <ScientificResultDisplayValue value={item} />
            </li>
          ) : null,
        )}
      </ul>
    );
  }
  const entries = visibleEntries(value as Record<string, unknown>);
  const primary = entries.find(([key]) => key === "text");
  const rest = entries.filter(
    ([key, child]) => key !== "text" && hasScientificResultContent(child),
  );
  return (
    <div className="grid gap-2">
      {primary ? <ScientificResultDisplayValue value={primary[1]} /> : null}
      {rest.length ? (
        <dl className="m-0 grid gap-2 border-l border-border pl-3">
          {rest.map(([key, child]) => {
            const title = researchPresentationFieldLabel(key) ?? key;
            return (
              <div key={key} className="grid gap-1">
                <dt className="text-sm font-medium text-text break-words">
                  {title}
                </dt>
                <dd className="m-0">
                  <ScientificResultDisplayValue value={child} />
                </dd>
              </div>
            );
          })}
        </dl>
      ) : null}
    </div>
  );
}

/** An optional source view supplements, rather than replacing, specialized renderers. */
export function ScientificResultSourceDetails({
  document,
}: {
  document: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  if (!hasScientificResultContent(document)) return null;
  return (
    <section className="mt-4 grid gap-2 text-sm text-text-secondary">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="w-fit cursor-pointer border-none bg-transparent p-0 text-left text-text-muted"
      >
        {open ? "收起完整结构化内容" : "查看完整结构化内容"}
      </button>
      {open ? <ScientificResultDisplayValue value={document} /> : null}
    </section>
  );
}
