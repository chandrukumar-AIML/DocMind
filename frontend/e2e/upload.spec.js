/**
 * Document upload E2E tests.
 * Tests the drag-and-drop / file picker → ingest → document list flow.
 */
import { test, expect } from "@playwright/test";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PDF = path.join(__dirname, "fixtures", "sample.pdf");

async function loginWithDocs(page, initialDocs = []) {
  const mockUser = {
    id: "usr_test",
    email: "demo@docmind.ai",
    full_name: "Demo User",
    access_token: "mock.jwt.token",
    workspaces: [{ workspace_id: "ws_test", name: "Test Workspace", role: "editor" }],
  };

  await page.route("**/auth/login", r =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockUser) })
  );
  await page.route("**/auth/me", r =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockUser) })
  );

  let docList = [...initialDocs];
  await page.route("**/documents**", r =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents: docList }) })
  );

  // Mock ingest endpoint — respond with success + update doc list for next poll
  await page.route("**/ingest/document**", async r => {
    docList = [...docList, { source_file: "uploaded.pdf", filename: "uploaded.pdf", page_count: 2 }];
    await r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ source_file: "uploaded.pdf", chunks_created: 12, status: "success" }),
    });
  });

  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();

  await page.locator('input[type="email"]').first().fill("demo@docmind.ai");
  await page.locator('input[type="password"]').first().fill("demo1234");
  await page.locator('button[type="submit"]').first().click();

  await page.waitForSelector(".app-shell, .chat-main", { timeout: 15_000 });
  return { docList };
}

test.describe("Document upload", () => {
  test("upload zone is visible in the sidebar", async ({ page }) => {
    await loginWithDocs(page);

    // Look for a dropzone or upload button in the sidebar
    const uploadZone = page.locator(
      "[class*='dropzone'], [class*='upload'], input[type='file'], button:has-text('Upload'), label:has-text('Upload')"
    ).first();
    await expect(uploadZone).toBeVisible({ timeout: 10_000 });
  });

  test("file input accepts PDF files", async ({ page }) => {
    await loginWithDocs(page);

    const fileInput = page.locator('input[type="file"]').first();
    await expect(fileInput).toBeAttached({ timeout: 10_000 });

    const accept = await fileInput.getAttribute("accept");
    // Should accept PDFs (accept might be blank = all, or include pdf)
    if (accept) {
      expect(accept).toMatch(/pdf|image|\*/i);
    }
  });

  test("rejected file type shows error toast", async ({ page }) => {
    await loginWithDocs(page);

    // Mock ingest to reject unsupported type
    await page.route("**/ingest/document**", r =>
      r.fulfill({
        status: 415,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Unsupported file type: .exe" }),
      })
    );

    const fileInput = page.locator('input[type="file"]').first();
    // Create a fake .exe buffer
    const buffer = Buffer.from("MZ fake exe content");
    await fileInput.setInputFiles({
      name: "malware.exe",
      mimeType: "application/octet-stream",
      buffer,
    });

    // An error toast or message should appear
    const errorEl = page.locator('[role="alert"], .toast-error, [class*="error"]');
    await expect(errorEl.first()).toBeVisible({ timeout: 8_000 });
  });

  test("document appears in sidebar after successful upload", async ({ page }) => {
    await loginWithDocs(page);

    const fileInput = page.locator('input[type="file"]').first();
    const pdfBuffer = Buffer.from("%PDF-1.4 fake pdf content");

    await fileInput.setInputFiles({
      name: "report.pdf",
      mimeType: "application/pdf",
      buffer: pdfBuffer,
    });

    // After upload, the doc list refreshes — our mock adds "uploaded.pdf"
    await expect(page.locator("text=uploaded.pdf")).toBeVisible({ timeout: 12_000 });
  });

  test("shows upload progress indicator during upload", async ({ page }) => {
    await loginWithDocs(page);

    // Slow down the ingest response to catch the progress indicator
    await page.route("**/ingest/document**", async r => {
      await new Promise(res => setTimeout(res, 1500));
      await r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ source_file: "slow.pdf", chunks_created: 5, status: "success" }),
      });
    });

    const fileInput = page.locator('input[type="file"]').first();
    await fileInput.setInputFiles({
      name: "slow.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 slow pdf"),
    });

    // Progress bar, spinner, or loading indicator should appear
    const progress = page.locator(
      "[role='progressbar'], [class*='progress'], [class*='uploading'], [class*='spinner'], .loading"
    );
    await expect(progress.first()).toBeVisible({ timeout: 3_000 });
  });
});
