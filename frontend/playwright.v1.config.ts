import { defineConfig, devices } from "@playwright/test";


export default defineConfig({
  testDir: "./e2e",
  testMatch: "knowledge_map_v1_browser.spec.ts",
  timeout: 60_000,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5175",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: {
      executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    },
  },
  webServer: {
    command: "npm.cmd run dev -- --host 127.0.0.1 --port 5175",
    cwd: process.cwd(),
    url: "http://127.0.0.1:5175/e2e-fixtures/knowledge-map-v1.html",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
