/// <reference types="vite/client" />

declare module "@fixtures/**/*.json" {
  const value: unknown;
  export default value;
}

declare module "@fixtures/**/*.md?raw" {
  const content: string;
  export default content;
}

interface ImportMetaEnv {
  readonly VITE_OPENAIR_API_BASE_URL: string;
  readonly VITE_OPENAIR_AUTH_TOKEN?: string;
  readonly VITE_OPENAIR_GIT_BRANCH?: string;
  readonly VITE_OPENAIR_GIT_COMMIT?: string;
  readonly VITE_OPENAIR_GIT_COMMIT_SHORT?: string;
  readonly VITE_OPENAIR_WEB_BUILD_TIME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
