import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const api = { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api/, '') } }

// The three.js scene is a dynamic import, so Vite emits it as its own chunk; the landing/dashboard shell loads first.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 5173, proxy: api },
  preview: { host: '127.0.0.1', port: 4173, proxy: api },
  build: { target: 'es2022', chunkSizeWarningLimit: 900 },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
