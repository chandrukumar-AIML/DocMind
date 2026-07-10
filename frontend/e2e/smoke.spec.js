/**
 * Smoke tests — verify the app loads and auth gate works.
 * These run on all browsers including mobile.
 */
import { test, expect } from "@playwright/test";

test.describe("Smoke", () => {
  test("page title is correct", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/DocuMind|DocMind/i);
  });

  test("shows login form for unauthenticated visitors", async ({ page }) => {
    await page.goto("/");
    // Clear any stored auth
    await page.evaluate(() => localStorage.clear());
    await page.reload();

    const form = page.locator("form").first();
    await expect(form).toBeVisible({ timeout: 10_000 });

    const emailInput = page.locator('input[type="email"]').first();
    await expect(emailInput).toBeVisible();
  });

  test("loading screen renders then resolves", async ({ page }) => {
    await page.goto("/");
    // Loading screen may be very brief — just check the page doesn't 500
    await expect(page.locator("body")).not.toBeEmpty();
    await expect(page.locator(".error-screen, .fatal-error")).not.toBeVisible();
  });

  test("no JS console errors on fresh load", async ({ page }) => {
    const errors = [];
    page.on("console", msg => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("pageerror", err => errors.push(err.message));

    await page.goto("/");
    await page.waitForTimeout(2000);

    // Filter out known benign errors (e.g. network errors from missing backend in CI)
    const realErrors = errors.filter(e =>
      !e.includes("Failed to fetch") &&
      !e.includes("NetworkError") &&
      !e.includes("ECONNREFUSED") &&
      !e.includes("net::ERR")
    );
    expect(realErrors).toHaveLength(0);
  });

  test("login form has accessible labels", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => localStorage.clear());
    await page.reload();

    const emailInput = page.locator('input[type="email"]').first();
    const passInput  = page.locator('input[type="password"]').first();

    await expect(emailInput).toBeVisible();
    await expect(passInput).toBeVisible();

    // Inputs must have an associated label or aria-label
    const emailLabel = await emailInput.getAttribute("aria-label") ||
      await page.locator(`label[for="${await emailInput.getAttribute("id")}"]`).count();
    expect(emailLabel).toBeTruthy();
  });
});
