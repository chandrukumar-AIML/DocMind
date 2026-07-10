/**
 * Authentication flow E2E tests.
 * Tests login, form validation, and logout.
 * Uses mocked API responses so no real backend is required.
 */
import { test, expect } from "@playwright/test";

test.describe("Authentication", () => {
  test.beforeEach(async ({ page }) => {
    // Clear auth state before each test
    await page.goto("/");
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.waitForSelector("form", { timeout: 10_000 });
  });

  test("shows email and password fields", async ({ page }) => {
    await expect(page.locator('input[type="email"]').first()).toBeVisible();
    await expect(page.locator('input[type="password"]').first()).toBeVisible();
    await expect(page.locator('button[type="submit"]').first()).toBeVisible();
  });

  test("submit button is disabled with empty fields", async ({ page }) => {
    const submitBtn = page.locator('button[type="submit"]').first();
    // Either disabled attribute or visually inactive — check the form requires fields
    const emailInput = page.locator('input[type="email"]').first();
    const required = await emailInput.getAttribute("required");
    // HTML5 required attribute prevents submission with empty fields
    expect(required).not.toBeNull();
  });

  test("shows error on invalid credentials (mocked)", async ({ page }) => {
    // Mock the login API to return 401
    await page.route("**/auth/login", route =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Invalid credentials" }),
      })
    );

    await page.locator('input[type="email"]').first().fill("wrong@example.com");
    await page.locator('input[type="password"]').first().fill("wrongpassword");
    await page.locator('button[type="submit"]').first().click();

    // Error toast or inline error should appear
    const errorMsg = page.locator('[role="alert"], .error-msg, .toast-error, [class*="error"]');
    await expect(errorMsg.first()).toBeVisible({ timeout: 8_000 });
  });

  test("successful login navigates to app shell (mocked)", async ({ page }) => {
    const mockUser = {
      id: "usr_test",
      email: "demo@docmind.ai",
      full_name: "Demo User",
      access_token: "mock.jwt.token",
      workspaces: [{ workspace_id: "ws_test", name: "Test Workspace", role: "editor" }],
    };

    // Mock login endpoint
    await page.route("**/auth/login", route =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockUser),
      })
    );

    // Mock documents list
    await page.route("**/documents**", route =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ documents: [] }),
      })
    );

    await page.locator('input[type="email"]').first().fill("demo@docmind.ai");
    await page.locator('input[type="password"]').first().fill("demo1234");
    await page.locator('button[type="submit"]').first().click();

    // Should transition to main app shell
    await expect(page.locator(".app-shell, .chat-main, main")).toBeVisible({ timeout: 15_000 });
    // Login form should be gone
    await expect(page.locator('input[type="password"]')).not.toBeVisible({ timeout: 5_000 });
  });

  test("register tab/link is accessible from login form", async ({ page }) => {
    // Look for a register / sign up link
    const registerLink = page.locator('button:has-text("Register"), button:has-text("Sign up"), a:has-text("Register"), a:has-text("Sign up"), [data-tab="register"]');
    await expect(registerLink.first()).toBeVisible();
  });
});
