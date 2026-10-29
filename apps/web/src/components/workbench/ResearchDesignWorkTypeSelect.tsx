import { Select } from "antd";
import type { SubStageCatalogEntry } from "@openair/shared";

export function ResearchDesignWorkTypeSelect({
  value,
  options,
  onSelect,
  testId,
}: {
  value?: string;
  options: SubStageCatalogEntry[];
  onSelect: (entry: SubStageCatalogEntry) => void;
  testId: string;
}) {
  return (
    <Select
      aria-label="工作类型"
      placeholder="选择工作类型"
      value={value}
      options={options.map((entry) => ({
        value: entry.key,
        label: entry.label,
      }))}
      onChange={(key) => {
        const entry = options.find((item) => item.key === key);
        if (entry) onSelect(entry);
      }}
      className="w-full"
      data-testid={testId}
    />
  );
}
