import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': process.env.MENTORFLOW_API_TARGET || 'http://127.0.0.1:8000' } },
  build: { rollupOptions: { input: { main: 'index.html', reviewDemo: 'review-demo.html' } } },
  test: { include: ['src/**/*.{test,spec}.{ts,tsx}'], environment: 'jsdom', setupFiles: './src/test/setup.ts' },
})
