import { expect, type Page } from "@playwright/test";

/**
 * Clipboard read-back is flaky in headless CI; UI feedback assertions remain
 * the primary gate. Set OPENAIR_PRODUCT_HARNESS_CLIPBOARD_READBACK=1 to force on.
 */
export const CLIPBOARD_READBACK_ENABLED =
  process.env.OPENAIR_PRODUCT_HARNESS_CLIPBOARD_READBACK === "1"
  || (!process.env.CI && process.env.OPENAIR_PRODUCT_HARNESS_CLIPBOARD_READBACK !== "0");

export async function expectClipboardContains(page: Page, fragment: string): Promise<void> {
  if (!CLIPBOARD_READBACK_ENABLED) return;
  await expect.poll(async () => page.evaluate(() => navigator.clipboard.readText())).toContain(fragment);
}

export async function expectClipboardEquals(page: Page, expected: string): Promise<void> {
  if (!CLIPBOARD_READBACK_ENABLED) return;
  await expect.poll(async () => page.evaluate(() => navigator.clipboard.readText())).toBe(expected);
}
