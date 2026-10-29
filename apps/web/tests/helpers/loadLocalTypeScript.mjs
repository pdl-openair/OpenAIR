import { readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const pnpmStore = fileURLToPath(new URL("../../../../node_modules/.pnpm/", import.meta.url));

export function loadLocalTypeScript() {
  const folder = readdirSync(pnpmStore, { withFileTypes: true })
    .find((entry) => entry.isDirectory() && entry.name.startsWith("typescript@"));
  if (!folder) throw new Error("TypeScript is missing from the local pnpm store.");
  return require(join(pnpmStore, folder.name, "node_modules", "typescript", "lib", "typescript.js"));
}
