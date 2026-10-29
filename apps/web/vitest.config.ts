import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const repoRoot = path.resolve(__dirname, "../..");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@openair/shared": path.resolve(__dirname, "../../packages/shared/src"),
      "@fixtures": path.resolve(repoRoot, "fixtures"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.{test,spec}.{ts,tsx}", "src/**/tests/**/*.test.mjs"],
  },
});
