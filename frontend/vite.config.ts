import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Backend origin the dev server proxies /api and /static to. Overridable so the
// app can run alongside other projects that already hold :8000: set
// VITE_API_TARGET=http://localhost:8010 (and PORT for the dev server itself).
const apiTarget = process.env.VITE_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
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
});
