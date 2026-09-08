const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1080 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const base = process.env.ADMIN_PREVIEW_TEST_URL || "http://127.0.0.1:8765";
  if (!["localhost", "127.0.0.1"].includes(new URL(base).hostname))
    throw Error("Loopback preview only");
  await page.goto(base);
  await page.waitForFunction(() => !!window.AdminDemo);
  await page.getByRole("heading", { name: "همه‌چیز، تحت کنترل شما" }).waitFor();
  await page.screenshot({
    path: (process.env.ADMIN_SCREENSHOT_DIR || "/tmp") + "/admin-dashboard.png",
    fullPage: true,
  });
  await page.locator('[data-nav="appearance"]').first().click();
  await page.locator('[data-action="add-row"]').waitFor();
  await page.screenshot({
    path:
      (process.env.ADMIN_SCREENSHOT_DIR || "/tmp") + "/admin-menu-builder.png",
    fullPage: true,
  });
  await page.locator('[data-appearance="buttons"]').click();
  await page
    .locator("#button-text")
    .fill("متن آزمایشی <img src=x onerror=window.__xss=1>");
  await page.locator('[data-color="success"]').click();
  await page.locator('[data-action="save"]').click();
  await page.locator("#confirm-change").check();
  await page.locator('[data-action="publish"]').click();
  await page.locator('[data-action="add-row"]').waitFor();
  if (await page.evaluate(() => !!window.__xss))
    throw Error("Text executed as HTML");
  await page.locator('[data-nav="plans"]').first().click();
  await page.locator('[data-action="new-plan"]').click();
  await page.locator('[name="display_button_text"]').fill("پلن تست مرورگر");
  await page.locator('[data-action="save"]').click();
  await page.locator("#confirm-change").check();
  await page.locator('[data-action="publish"]').click();
  await page.getByText("پلن تست مرورگر", { exact: true }).waitFor();
  await page.locator('[data-nav="tickets"]').first().click();
  await page.locator('[data-action="open-ticket"]').first().click();
  await page.locator('[name="reply"]').fill("پاسخ آزمایشی مرورگر");
  await page.locator('[data-action="save"]').click();
  await page.locator("#confirm-change").check();
  await page.locator('[data-action="publish"]').click();
  await page.locator('[data-action="open-ticket"]').first().click();
  await page.getByText("پاسخ آزمایشی مرورگر", { exact: false }).waitFor();
  await page.locator('[data-action="close-modal"]').first().click();
  await page.locator('[data-nav="dashboard"]').click();
  await page.getByRole("heading", { name: "همه‌چیز، تحت کنترل شما" }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: (process.env.ADMIN_SCREENSHOT_DIR || "/tmp") + "/admin-mobile.png",
    fullPage: true,
  });
  if (
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    )
  )
    throw Error("Mobile horizontal overflow");
  if (errors.length) throw Error(errors.join("\n"));
  console.log(
    "PASS: desktop/mobile render, plan preview/publish, ticket reply/publish, no runtime exceptions, text-as-HTML execution or mobile overflow",
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
