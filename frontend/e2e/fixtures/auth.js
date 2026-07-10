/**
 * Shared auth helpers for E2E tests.
 * Uses the demo credentials that are always available in CI.
 */

export const TEST_USER = {
  email: process.env.E2E_EMAIL || "demo@docmind.ai",
  password: process.env.E2E_PASSWORD || "demo1234",
};

/**
 * Log in via the UI login form and wait for the app shell to appear.
 */
export async function loginViaUI(page) {
  await page.goto("/");
  await page.waitForSelector('[data-testid="login-form"], .login-form, form', { timeout: 15_000 });

  const emailInput = page.locator('input[type="email"], input[name="email"]').first();
  const passInput  = page.locator('input[type="password"]').first();
  const submitBtn  = page.locator('button[type="submit"]').first();

  await emailInput.fill(TEST_USER.email);
  await passInput.fill(TEST_USER.password);
  await submitBtn.click();

  // Wait for the main app shell to appear after login
  await page.waitForSelector(".app-shell, .chat-main", { timeout: 20_000 });
}

/**
 * Inject a mock JWT directly into localStorage so tests skip the login form.
 * Use this in beforeEach for tests that don't test the auth flow itself.
 */
export async function injectMockAuth(page) {
  await page.goto("/");
  await page.evaluate(() => {
    const mockToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0LXVzZXItaWQiLCJlbWFpbCI6ImRlbW9AZG9jbWluZC5haSIsImV4cCI6OTk5OTk5OTk5OX0.mock";
    localStorage.setItem("dm_access_token", mockToken);
    localStorage.setItem("dm_user", JSON.stringify({
      id: "test-user-id",
      email: "demo@docmind.ai",
      full_name: "Demo User",
    }));
  });
}
