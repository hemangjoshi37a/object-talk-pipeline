import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite dev server proxies all /api and /files requests to the FastAPI backend.
// In production, FastAPI serves the built static files directly.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
      '/files': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
});
