import { defineConfig, devices } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";


const root = process.cwd();
const python = path.resolve(root, "../.venv/Scripts/python.exe");
const activeTestRoot = path.resolve(root, "../test-results/active-teaching");
mkdirSync(activeTestRoot, { recursive: true });

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: {
    timeout: 30_000,
  },
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: {
      executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    },
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8010`,
      cwd: path.resolve(root, "../backend"),
      env: {
        ...process.env,
        MENTORFLOW_ORM_DB: path.resolve(activeTestRoot, "active-teaching-e2e.db"),
        MENTORFLOW_ACTIVE_UPLOAD_ROOT: path.resolve(activeTestRoot, "active-teaching-uploads"),
      },
      url: "http://127.0.0.1:8010/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm.cmd run dev -- --host 127.0.0.1 --port 5174",
      cwd: root,
      env: {
        ...process.env,
        MENTORFLOW_API_TARGET: "http://127.0.0.1:8010",
      },
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
