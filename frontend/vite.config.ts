import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// The API serves the built bundle (ADR-0013). In dev, Vite serves the app and
// proxies every API route to the FastAPI server, so the client can use the same
// relative URLs in both — there is no API base URL to get wrong.
const API = process.env.API_URL ?? 'http://localhost:8000';
const apiRoutes = ['/research', '/watchlist', '/companies', '/sweeps', '/runs', '/health'];

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  server: {
    proxy: Object.fromEntries(apiRoutes.map(r => [r, { target: API, changeOrigin: true }])),
  },
  test: { environment: 'jsdom', include: ['tests/**/*.test.{ts,tsx}'] },
});
