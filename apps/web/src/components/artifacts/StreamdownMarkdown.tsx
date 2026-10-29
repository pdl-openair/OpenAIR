import { code } from "@streamdown/code";
import { cjk } from "@streamdown/cjk";
import { math } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import { Streamdown } from "streamdown";
import { Component, type ReactNode } from "react";
import { prepareMarkdownForRender } from "../../lib/prepareMarkdownForRender";
import "katex/dist/katex.min.css";
import "streamdown/styles.css";

const STREAMDOWN_PLUGINS = { code, mermaid, math, cjk } as const;

// Avoid Streamdown's lazy code/diagram renderers after a module-load failure.
const PLAIN_CODE_COMPONENTS = { pre: "pre", code: "code" } as const;

class MarkdownRenderBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** Match Streamdown Playground interactive chrome for tables / code / diagrams. */
const STREAMDOWN_CONTROLS = {
  table: { copy: true, download: true, fullscreen: true },
  code: { copy: true, download: true },
  mermaid: { copy: true, download: true, fullscreen: true, panZoom: true },
} as const;

export interface StreamdownMarkdownProps {
  content: string;
  className?: string;
  /** Optional test id on the outer wrapper. */
  testId?: string;
}

/**
 * Workbench / artifact Markdown preview via Streamdown (GFM + Shiki + KaTeX + Mermaid + CJK).
 * Artifacts are complete documents, so we render in static mode without streaming chrome.
 * Styles are scoped to track https://streamdown.ai/playground as closely as the host allows.
 */
export function StreamdownMarkdown({
  content,
  className,
  testId,
}: StreamdownMarkdownProps) {
  const markdown = prepareMarkdownForRender(content);
  return (
    <div className={className ?? "wb-artifact-md"} data-testid={testId}>
      <MarkdownRenderBoundary
        key={content}
        fallback={
          <Streamdown
            mode="static"
            components={PLAIN_CODE_COMPONENTS}
            plugins={STREAMDOWN_PLUGINS}
            controls={STREAMDOWN_CONTROLS}
            linkSafety={{ enabled: false }}
            className="wb-artifact-streamdown"
          >
            {markdown}
          </Streamdown>
        }
      >
        <Streamdown
          mode="static"
          plugins={STREAMDOWN_PLUGINS}
          controls={STREAMDOWN_CONTROLS}
          linkSafety={{ enabled: false }}
          shikiTheme={["github-light", "github-dark"]}
          className="wb-artifact-streamdown"
        >
          {markdown}
        </Streamdown>
      </MarkdownRenderBoundary>
    </div>
  );
}
