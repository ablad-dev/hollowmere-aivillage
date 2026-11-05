import { defineConfig } from 'vite';

const apiPort = process.env.VITE_API_PORT ?? '8000';

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': `http://localhost:${apiPort}`,
      '/ws': { target: `ws://localhost:${apiPort}`, ws: true },
    },
  },
});
