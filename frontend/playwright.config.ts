import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'

const root = process.cwd()
const python = path.resolve(root, '../.venv/Scripts/python.exe')

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'on-first-retry', ...devices['Desktop Chrome'], launchOptions: { executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' } },
  webServer: [
    { command: `"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000`, cwd: path.resolve(root, '../backend'), url: 'http://127.0.0.1:8000/api/health', reuseExistingServer: true, timeout: 120_000 },
    { command: 'npm.cmd run dev -- --host 127.0.0.1', cwd: root, url: 'http://127.0.0.1:5173', reuseExistingServer: true, timeout: 120_000 },
  ],
})
