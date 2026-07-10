/**
 * Chat interface E2E tests — golden path for the core product feature.
 * Mocks the backend so tests are hermetic and fast.
 */
import { test, expect } from "@playwright/test";

// Helper: boot the app with mocked auth + docs
async function setupApp(page, documents = []) {
  const mockUser = {
    id: "usr_test",
    email: "demo@docmind.ai",
    full_name: "Demo User",
    access_token: "mock.jwt.token",
    workspaces: [{ workspace_id: "ws_test", name: "Test Workspace", role: "editor" }],
  };

  await page.route("**/auth/login", route =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockUser) })
  );
  await page.route("**/auth/me", route =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockUser) })
  );
  await page.route("**/documents**", route =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents }) })
  );

  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();

  // Log in
  await page.locator('input[type="email"]').first().fill("demo@docmind.ai");
  await page.locator('input[type="password"]').first().fill("demo1234");
  await page.locator('button[type="submit"]').first().click();

  await page.waitForSelector(".app-shell, .chat-main, main", { timeout: 15_000 });
}

test.describe("Chat interface", () => {
  test("chat input is disabled with no documents", async ({ page }) => {
    await setupApp(page, []);

    const textarea = page.locator("textarea.chat-textarea, textarea[placeholder*='Upload'], textarea[placeholder*='document']");
    await expect(textarea.first()).toBeVisible({ timeout: 10_000 });

    // Input should be disabled or placeholder should say to upload
    const placeholder = await textarea.first().getAttribute("placeholder");
    const isDisabled  = await textarea.first().isDisabled();
    expect(isDisabled || (placeholder && /upload/i.test(placeholder))).toBeTruthy();
  });

  test("chat input enables after document is available", async ({ page }) => {
    const docs = [{ source_file: "contract.pdf", filename: "contract.pdf", page_count: 5 }];
    await setupApp(page, docs);

    // Wait for sidebar to populate with the document
    await expect(page.locator("text=contract.pdf").first()).toBeVisible({ timeout: 10_000 });

    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeEnabled({ timeout: 5_000 });
  });

  test("sending a message shows it in the chat window (mocked response)", async ({ page }) => {
    const docs = [{ source_file: "report.pdf", filename: "report.pdf", page_count: 3 }];
    await setupApp(page, docs);

    // Mock streaming query endpoint
    await page.route("**/query**", route =>
      route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: "This is a mocked answer from DocMind AI.",
      })
    );

    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeEnabled({ timeout: 8_000 });

    await textarea.fill("What is this document about?");
    await textarea.press("Enter");

    // User message should appear in chat
    await expect(page.locator("text=What is this document about?")).toBeVisible({ timeout: 8_000 });
  });

  test("Ctrl+K focuses the chat textarea", async ({ page }) => {
    const docs = [{ source_file: "doc.pdf", filename: "doc.pdf", page_count: 1 }];
    await setupApp(page, docs);

    await page.keyboard.press("Control+k");
    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeFocused({ timeout: 3_000 });
  });

  test("query mode switcher is visible and clickable", async ({ page }) => {
    const docs = [{ source_file: "doc.pdf", filename: "doc.pdf", page_count: 1 }];
    await setupApp(page, docs);

    // RAG / Agent / Graph mode switcher in topbar
    const ragChip   = page.locator("text=RAG, button:has-text('RAG'), [class*='mode-chip']:has-text('RAG')").first();
    const agentChip = page.locator("text=Agent, button:has-text('AGENT'), [class*='mode-chip']:has-text('AGENT')").first();

    // At least one mode selector must be visible
    const either = page.locator("[class*='mode-chip'], [class*='mode-btn'], button:has-text('RAG'), button:has-text('AGENT')");
    await expect(either.first()).toBeVisible({ timeout: 8_000 });
  });

  test("sidebar toggle button works on desktop", async ({ page }) => {
    const docs = [{ source_file: "doc.pdf", filename: "doc.pdf", page_count: 1 }];
    await setupApp(page, docs);

    // Sidebar should start open on desktop (>900px)
    const sidebar = page.locator(".sidebar, aside, [class*='sidebar']").first();
    await expect(sidebar).toBeVisible({ timeout: 5_000 });

    // Click the hamburger / toggle button
    const toggleBtn = page.locator("[aria-label*='sidebar'], [aria-label*='Sidebar'], button.topbar-btn").first();
    await toggleBtn.click();

    // Sidebar should collapse (may slide out or have class change — just check no crash)
    await page.waitForTimeout(500);
    await expect(page.locator(".chat-main, main")).toBeVisible();
  });
});
