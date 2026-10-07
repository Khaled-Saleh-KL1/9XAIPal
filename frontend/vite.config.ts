import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Stamp dist/sw.js with a per-build id so every deploy changes the worker's
// bytes (the browser then installs it) and versions its caches.
function swBuildId(): Plugin {
  let outDir = 'dist';
  let root = process.cwd();
  return {
    name: 'sw-build-id',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir;
      root = config.root;
    },
    closeBundle() {
      const file = resolve(root, outDir, 'sw.js');
      const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      writeFileSync(file, readFileSync(file, 'utf8').replaceAll('__BUILD_ID__', id));
    },
  };
}

// Backend origin the dev server proxies /api and /static to. Overridable so the
// app can run alongside other projects that already hold :8000: set
// VITE_API_TARGET=http://localhost:8010 (and PORT for the dev server itself).
const apiTarget = process.env.VITE_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react(), swBuildId()],
  server: {
    port: Number(process.env.PORT ?? 5173),
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/static': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
});
