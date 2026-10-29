import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Typography } from "antd";
import { cn } from "../../lib/cn";

type WorkbenchFocusSummaryItemProps = {
  testId: string;
  label: string;
  text: string;
  itemClassName?: string;
};

/**
 * Character-count threshold for the expand-button heuristic.
 *
 * When CSS `-webkit-line-clamp` is active, `scrollHeight > clientHeight` can
 * under-report on narrow containers. As a supplementary signal, if the text
 * content length >= this value we assume overflow is likely.
 *
 * Tuned for CJK-heavy content at font-size 11px in a 4-column grid (~100 px
 * per card). English-only text may trigger later due to narrower glyphs, but
 * the primary domOverflow check covers that case.
 */
const FOCUS_SUMMARY_LINE_CLAMP_CHAR_HINT = 18;

function itemSpanClass(itemClassName?: string): string | undefined {
  if (!itemClassName) return undefined;
  if (itemClassName.includes("asset") || itemClassName.includes("progress")) {
    return "col-span-2 max-[1320px]:col-auto";
  }
  return itemClassName;
}

function measureOverflow(element: HTMLElement | null): boolean {
  if (!element) return false;
  const style = getComputedStyle(element);
  const lineClamp = Number.parseInt(style.webkitLineClamp || style.getPropertyValue("-webkit-line-clamp") || "0", 10);
  const textContent = (element.textContent ?? "").replace(/\s+/g, " ").trim();
  const domOverflow =
    element.scrollHeight > element.clientHeight + 1
    || element.scrollWidth > element.clientWidth + 1;
  const lineClampHint = lineClamp > 0 && textContent.length >= FOCUS_SUMMARY_LINE_CLAMP_CHAR_HINT;
  return domOverflow || lineClampHint;
}

export function WorkbenchFocusSummaryItem({
  testId,
  label,
  text,
  itemClassName,
}: WorkbenchFocusSummaryItemProps) {
  const valueRef = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [canExpand, setCanExpand] = useState(false);

  const refreshOverflow = useCallback(() => {
    if (expanded) return;
    setCanExpand(measureOverflow(valueRef.current));
  }, [expanded]);

  useLayoutEffect(() => {
    refreshOverflow();
  }, [text, refreshOverflow]);

  useEffect(() => {
    const node = valueRef.current;
    if (!node) return undefined;
    const observer = new ResizeObserver(() => refreshOverflow());
    observer.observe(node);
    return () => observer.disconnect();
  }, [refreshOverflow]);

  useEffect(() => {
    setExpanded(false);
  }, [text]);

  const showExpandControl = canExpand || expanded;

  return (
    <section
      className={cn(
        "min-w-0 rounded-lg border border-border bg-surface-soft px-1.75 py-1.5",
        itemSpanClass(itemClassName),
      )}
      data-testid={testId}
    >
      <Typography.Text type="secondary" className="text-xs">
        {label}
      </Typography.Text>
      <div className="flex min-w-0 flex-col items-start">
        <Typography.Paragraph
          ref={valueRef}
          className={cn(
            "mt-0.5 mb-0 w-full overflow-hidden text-xs leading-snug text-text [-webkit-box-orient:vertical] line-clamp-1 max-[1320px]:line-clamp-2",
            expanded && "block! overflow-visible! line-clamp-none whitespace-normal",
          )}
        >
          {text}
        </Typography.Paragraph>
        {showExpandControl && !expanded ? (
          <Button
            type="link"
            size="small"
            className="h-auto min-h-0 p-0 m-0 text-2xs leading-tight"
            aria-expanded={false}
            aria-label={`展开${label}`}
            data-testid={`${testId}-expand`}
            onClick={() => setExpanded(true)}
          >
            展开
          </Button>
        ) : null}
        {expanded ? (
          <Button
            type="link"
            size="small"
            className="h-auto min-h-0 p-0 m-0 text-2xs leading-tight"
            aria-expanded
            aria-label={`收起${label}`}
            data-testid={`${testId}-collapse`}
            onClick={() => {
              setExpanded(false);
              requestAnimationFrame(() => refreshOverflow());
            }}
          >
            收起
          </Button>
        ) : null}
      </div>
    </section>
  );
}
