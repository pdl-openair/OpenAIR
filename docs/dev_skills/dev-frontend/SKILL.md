---
name: dev-frontend
description: >-
  开发者修改前端页面或组件时触发。当用户说"改页面"、"前端开发"、
  "改 workbench"、"改看板"、"改任务详情"、"前端组件"、"frontend"、
  "页面开发"、"apps/web"时使用本 skill。
  引导开发者按产品规则实现页面，防止硬编码特定领域字段或违反页面规范。
  即使用户只是改 CSS 或组件，只要涉及 apps/web/ 就应该触发。
---

# Dev: Frontend Page Development

## When to use

开发者需要修改或新增 `apps/web/` 下的前端页面或组件。

## Rules source

Read before modifying:

- `docs/02_产品与Workflow/OpenAIR/待专项更新/page_scope.md` -- four core pages and priorities
- `docs/02_产品与Workflow/OpenAIR/待专项更新/product_boundary.md` -- what the platform does and does not do
- `docs/02_产品与Workflow/OpenAIR/待专项更新/task_board_and_artifact_management.md` -- task stage display (the doc defines a four-stage model), board card fields
- `docs/02_产品与Workflow/OpenAIR/待专项更新/workbench_human_ai_collaboration.md` -- workbench layout, result blocks
- `docs/02_产品与Workflow/OpenAIR/待专项更新/task_board_and_artifact_management.md` -- board display, artifact rules

## Pre-checks

1. Identify which page is being modified (workbench / board / task detail / situation board)
2. Read the corresponding product doc above
3. Verify no domain-specific fields are hardcoded as the only path
4. Check that shared types from `packages/shared/` are used correctly
5. Confirm all colors use tokens from `apps/web/src/index.css`, preferring existing tokens (see **Color tokens** below)

## Color tokens (mandatory)

**All colors must come from `apps/web/src/index.css`.** Do not hardcode hex, rgb, rgba, or hsl in components, inline styles, or Tailwind arbitrary values.

### Prefer existing tokens (mandatory)

**Before adding a new variable, search `index.css` and reuse an existing token whenever the color role matches.** Only introduce `--wb-*` when no existing token covers the semantic need (e.g. a new status color), not because a page needs a slightly different shade.

Typical mappings:

| Need                    | Existing tokens                                                                       |
| ----------------------- | ------------------------------------------------------------------------------------- |
| Page / shell background | `shell-bg`, `bg`, `surface`, `surface-soft`, `shell-surface-subtle`                   |
| Card / panel            | `surface`, `surface-soft`, `surface-muted`                                            |
| Primary brand           | `brand`, `brand-soft`, `brand-soft-2`                                                 |
| Body text               | `text`, `shell-text`, `shell-text-strong`, `shell-text-secondary`, `shell-text-muted` |
| Borders                 | `border`, `shell-border`, `shell-divider`, `border-strong`                            |
| Accent / links          | `accent`, `accent-strong`, `accent-soft`                                              |
| Status                  | `success`, `warning`, `danger`, `info-soft`, `purple`                                 |

When two values look close, pick the existing token — do not fork a near-duplicate.

### How to use

1. **Tailwind classes (preferred)** — tokens are mapped in `@theme inline` as `--color-*`. Use semantic utilities, e.g. `bg-shell-bg`, `text-brand`, `border-shell-border`, `bg-surface`, `text-shell-text-muted`.
2. **CSS `var()`** — when Tailwind utilities are insufficient, reference `--wb-*` directly, e.g. `var(--wb-brand)`, `var(--wb-shell-text-secondary)`.
3. **Ant Design** — keep seed tokens in `apps/web/src/lib/antDesignTheme.ts` aligned with the same `--wb-*` values; use `ConfigProvider` `cssVar` (see `main.tsx`).

### Forbidden

- `#594aff`, `rgb(...)`, `rgba(...)` in JSX/TSX class names or `style`
- Tailwind arbitrary colors: `text-[#...]`, `bg-[rgba(...)]`, `border-[#...]`
- New page-level `.css` files for colors (only `index.css` defines the palette)

### When a token is missing

Only after confirming no existing token fits:

1. Add `--wb-<semantic-name>` under `:root` (light) and `:root[data-theme="dark"]` (dark) in `index.css`
2. Map it in `@theme inline` as `--color-<semantic-name>: var(--wb-<semantic-name>)`
3. Use the new Tailwind utility (e.g. `--color-foo` → `bg-foo`, `text-foo`)

New tokens must use reusable semantic names (shell/brand/surface/text/status), not page-specific names like `--wb-not-found-card-bg`.

## Output

Page/component implementation that follows product rules, uses shared types, and uses only `index.css` color tokens.
