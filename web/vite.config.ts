import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const api = { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api/, '') } }

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, proxy: api },
  preview: { host: '127.0.0.1', port: 4173, proxy: api },
  build: { target: 'es2022' },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
