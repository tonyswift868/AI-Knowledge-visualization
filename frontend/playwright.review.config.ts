import { defineConfig, devices } from '@playwright/test'

const backendUrl = 'http://127.0.0.1:8013'
const frontendUrl = 'http://127.0.0.1:5177'
const python = process.env.MENTORFLOW_PYTHON ?? 'python'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'review-demo.spec.ts',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: frontendUrl,
    trace: 'on-first-retry',
    ...devices['Desktop Chrome'],
    launchOptions: { executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' },
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn app.review_product_v1.api:app --host 127.0.0.1 --port 8013`,
      cwd: '../backend',
      url: `${backendUrl}/api/review/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'npm.cmd run dev -- --host 127.0.0.1 --port 5177 --strictPort',
      cwd: '.',
      url: `${frontendUrl}/review-demo.html`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { ...process.env, VITE_REVIEW_API_BASE: `${backendUrl}/api/review` },
    },
  ],
})
