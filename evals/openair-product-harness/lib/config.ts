import os from "node:os";
import path from "node:path";

export const API_BASE_URL = process.env.OPENAIR_PRODUCT_HARNESS_API_BASE_URL ?? "http://127.0.0.1:8000";
export const WEB_BASE_URL = process.env.OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL ?? "http://127.0.0.1:5173";
export const RUN_ID = process.env.OPENAIR_PRODUCT_HARNESS_RUN_ID ?? new Date().toISOString().replace(/[:.]/g, "-");
export const RUN_DIR = process.env.OPENAIR_PRODUCT_HARNESS_RUN_DIR ?? path.join(os.tmpdir(), "openair-product-harness-runs", RUN_ID);
