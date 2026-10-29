import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const repoRoot = path.resolve(__dirname, "../..");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The API loads <repo>/.env; use the same file for VITE_* settings.
  envDir: repoRoot,
  resolve: {
    alias: {
      "@openair/shared": path.resolve(__dirname, "../../packages/shared/src"),
      "@fixtures": path.resolve(repoRoot, "fixtures"),
    },
  },
  server: {
    port: 5173,
    /** 同时监听 IPv4 / IPv6，避免仅 [::1] 时 127.0.0.1 或部分「localhost」解析失败 */
    host: true,
    fs: {
      allow: [repoRoot],
    },
  },
});
