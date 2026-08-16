import { defineConfig, devices } from "playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: "http://127.0.0.1:4175",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: "tablet",
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: "mobile",
      use: {
        ...devices["iPhone 13"],
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
      },
    },
  ],
  webServer: [
    {
      command: ".venv/bin/python -m tests.workbench_e2e_server",
      cwd: "../..",
      url: "http://127.0.0.1:18788/health",
      env: {
        STORYMOTION_E2E_ROOT: "/tmp/storymotion-studio-playwright-e2e",
      },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4175",
      url: "http://127.0.0.1:4175",
      env: {
        STORYMOTION_API_URL: "http://127.0.0.1:18788",
      },
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
