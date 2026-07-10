import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "on-failure" }]],

  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5175",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
    },
    // Mobile viewport smoke test
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"] },
      testMatch: "**/smoke.spec.js",
    },
  ],

  // Start the dev server automatically in CI
  webServer: process.env.CI
    ? {
        command: "npm run preview",
        url: "http://localhost:4173",
        reuseExistingServer: false,
        timeout: 60_000,
        env: { VITE_API_URL: process.env.VITE_API_URL || "http://localhost:8000" },
      }
    : undefined,
});
