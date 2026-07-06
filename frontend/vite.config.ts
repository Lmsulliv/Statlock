/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Dev proxy: the SPA always calls same-origin "/api/...", and Vite forwards
// those to the FastAPI backend on :8000. This sidesteps CORS entirely (the
// browser sees one origin), so the Python side needs no changes. In production
// the same "/api" paths sit behind a single origin too.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  // Component tests run in a jsdom DOM with jest-dom matchers registered by the
  // setup file. globals:true lets tests use describe/it/expect without imports.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
  },
})
