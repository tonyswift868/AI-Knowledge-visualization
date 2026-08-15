import { defineConfig, devices } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const python = path.resolve(root, '../.venv/Scripts/python.exe')
const backendUrl = 'http://127.0.0.1:8012'
const frontendUrl = 'http://127.0.0.1:5176'
const isolatedRuntime = path.resolve(
  process.env.MENTORFLOW_E2E_TEMP_ROOT
    ?? `E:/Codex/Temp/mentorflow-playwright-${process.pid}-${Date.now()}`,
)
mkdirSync(isolatedRuntime, { recursive: true })
const backendEnv = {
  ...process.env,
  MENTORFLOW_DB: path.join(isolatedRuntime, 'app.db'),
  MENTORFLOW_ORM_DB: path.join(isolatedRuntime, 'mentorflow_v2.db'),
  MENTORFLOW_ACTIVE_UPLOAD_ROOT: path.join(isolatedRuntime, 'active-teaching', 'uploads'),
}

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [['list']],
  use: { baseURL: frontendUrl, trace: 'on-first-retry', ...devices['Desktop Chrome'], launchOptions: { executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' } },
  webServer: [
    { command: `"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8012`, cwd: path.resolve(root, '../backend'), url: `${backendUrl}/api/health`, reuseExistingServer: false, timeout: 120_000, env: backendEnv },
    { command: 'npm.cmd run dev -- --host 127.0.0.1 --port 5176 --strictPort', cwd: root, url: frontendUrl, reuseExistingServer: false, timeout: 120_000, env: { ...process.env, MENTORFLOW_API_TARGET: backendUrl } },
  ],
})
