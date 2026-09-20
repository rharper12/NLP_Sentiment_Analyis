/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute API origin for a deployed build; falls back to the dev proxy at /api. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
