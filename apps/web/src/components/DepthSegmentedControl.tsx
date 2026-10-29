import { Tooltip } from "antd";
import type { CSSProperties } from "react";
import { cn } from "../lib/cn";

export type DepthSegmentOption<T extends string> = {
  value: T;
  label: string;
};

export type DepthSegmentedControlProps<T extends string> = {
  value: T;
  options: readonly DepthSegmentOption<T>[];
  onChange?: (value: T) => void;
  disabled?: boolean;
  rootClassName?: string;
  className?: string;
  testId?: string;
  ariaLabel?: string;
  startLabel?: string;
  endLabel?: string;
};

export function DepthSegmentedControl<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
  className,
  rootClassName,
  testId,
  ariaLabel,
  startLabel,
  endLabel
}: DepthSegmentedControlProps<T>) {
  const maxLevel = options.length;
  const selectedIndex = Math.max(
    0,
    options.findIndex(option => option.value === value)
  );
  const segmentedStyle = {
    "--segment-count": maxLevel,
    "--segment-index": selectedIndex,
    "--segment-gap": "0.25rem",
    "--segment-pad": "0.25rem"
  } as CSSProperties;

  return (
    <div className={cn("flex items-center gap-2", rootClassName, disabled && "opacity-60")}>
      {startLabel ? <span className="shrink-0 text-sm text-text-muted">{startLabel}</span> : null}
      <div
        className={cn(
          "relative grid flex-1 gap-1 rounded-xl border border-border bg-surface-muted p-1",
          maxLevel === 2 && "grid-cols-2",
          maxLevel === 3 && "grid-cols-3",
          maxLevel === 4 && "grid-cols-4",
          maxLevel > 4 && "grid-flow-col auto-cols-fr",
          className
        )}
        style={segmentedStyle}
        data-testid={testId}
        role="radiogroup"
        aria-label={ariaLabel}
        aria-disabled={disabled || undefined}
      >
        <span
          className={cn(
            "pointer-events-none absolute top-[var(--segment-pad)] left-[var(--segment-pad)] z-10 h-4 rounded-md shadow-sm transition-transform duration-200 ease-out",
            disabled ? "border-border-strong bg-text-muted" : "border-brand bg-brand"
          )}
          style={{
            width:
              "calc((100% - (var(--segment-pad) * 2) - (var(--segment-gap) * (var(--segment-count) - 1))) / var(--segment-count))",
            transform: "translateX(calc(var(--segment-index) * (100% + var(--segment-gap))))"
          }}
          aria-hidden
        />
        {options.map(option => {
          const selected = option.value === value;
          return (
            <Tooltip key={option.value} title={option.label} mouseEnterDelay={0.2}>
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                aria-label={option.label}
                disabled={disabled}
                className={cn(
                  "relative z-raised h-4 min-w-0 rounded-md border transition-colors duration-150",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-strong",
                  selected
                    ? "border-transparent bg-transparent"
                    : "border-transparent bg-transparent hover:border-border hover:bg-surface-soft",
                  disabled && "cursor-not-allowed hover:border-transparent hover:bg-transparent"
                )}
                onClick={() => {
                  if (disabled || selected) return;
                  onChange?.(option.value);
                }}
              />
            </Tooltip>
          );
        })}
      </div>
      {endLabel ? <span className="shrink-0 text-sm text-text-muted">{endLabel}</span> : null}
    </div>
  );
}
