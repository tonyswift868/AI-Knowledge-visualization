import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({ plugins: [react()], server: { port: 5173, proxy: { '/api': process.env.MENTORFLOW_API_TARGET || 'http://127.0.0.1:8000' } }, test: { include: ['src/test/**/*.{test,spec}.{ts,tsx}'], environment: 'jsdom', setupFiles: './src/test/setup.ts' } })
