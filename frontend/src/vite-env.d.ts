/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Public origin of the 9XAIPal backend API (e.g. https://api.example.com).
   * Unset in local dev — requests stay relative and Vite proxies /api to :8000.
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
