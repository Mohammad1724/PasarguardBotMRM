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
  const nav = async (key) => {
    const item = page.locator(`.sidebar [data-nav="${key}"]`);
    const group = item.locator("xpath=ancestor::details");
    if ((await group.count()) && !(await group.evaluate((el) => el.open)))
      await group.locator("summary").click();
    await item.click();
  };
  const ownerKeys = await page
    .locator(".sidebar [data-nav]")
    .evaluateAll((items) => items.map((i) => i.dataset.nav));
  if (new Set(ownerKeys).size !== 12 || ownerKeys.length !== 12)
    throw Error("Missing or duplicate owner navigation actions");
  if (await page.locator(".nav-group[open]").count())
    throw Error("Dashboard groups should start compact");

  await page.screenshot({
    path: (process.env.ADMIN_SCREENSHOT_DIR || "/tmp") + "/admin-dashboard.png",
    fullPage: true,
  });
  await nav("appearance");
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
  let dialogSeen = false;
  page.once("dialog", async (dialog) => {
    dialogSeen = true;
    await dialog.dismiss();
  });
  await nav("users");
  if (!dialogSeen || !(await page.locator("#button-text").count()))
    throw Error(
      "Categorized navigation discarded unsaved changes without consent",
    );

  await page.locator('[data-color="success"]').click();
  await page.locator('[data-action="save"]').click();
  await page.locator("#confirm-change").check();
  await page.locator('[data-action="publish"]').click();
  await page.locator('[data-action="add-row"]').waitFor();
  if (await page.evaluate(() => !!window.__xss))
    throw Error("Text executed as HTML");
  await nav("plans");
  await page.locator('[data-action="new-plan"]').click();
  await page.locator('[name="display_button_text"]').fill("پلن تست مرورگر");
  await page.locator('[data-action="save"]').click();
  await page.locator("#confirm-change").check();
  await page.locator('[data-action="publish"]').click();
  await page.getByText("پلن تست مرورگر", { exact: true }).waitFor();
  await nav("tickets");
  await page.locator('[data-action="open-ticket"]').first().click();
  await page.locator('[name="reply"]').fill("پاسخ آزمایشی مرورگر");
  await page.locator('[data-action="save"]').click();
  await page.locator("#confirm-change").check();
  await page.locator('[data-action="publish"]').click();
  await page.locator('[data-action="open-ticket"]').first().click();
  await page.getByText("پاسخ آزمایشی مرورگر", { exact: false }).waitFor();
  await page.locator('[data-action="close-modal"]').first().click();
  await nav("dashboard");
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

  await page.locator('[data-action="toggle-nav"]').click();
  await page.locator(".sidebar.open").waitFor();
  for (const summary of await page
    .locator(".sidebar details:not([open]) summary")
    .all())
    await summary.click();
  await page
    .locator('.sidebar [data-action="logout"]')
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path:
      (process.env.ADMIN_SCREENSHOT_DIR || "/tmp") + "/admin-mobile-menu.png",
    fullPage: true,
  });
  await nav("users");
  await page.getByRole("heading", { name: "کاربران", exact: true }).waitFor();
  if (await page.locator(".sidebar.open").count())
    throw Error("Mobile menu did not close after navigation");
  // Isolated preview roles only; server authorization is covered by Python API tests.
  const roles = [
    [
      ["tickets.view", "users.view"],
      ["dashboard", "users", "tickets"],
      ["customers"],
    ],
    [["payments.manage"], ["dashboard", "settings"], ["system"]],
    [["plans.manage"], ["dashboard", "plans"], ["sales"]],
    [
      ["appearance.manage"],
      ["dashboard", "appearance", "texts"],
      ["appearance"],
    ],
    [["finance.view"], ["dashboard", "finance", "renewals"], ["sales"]],
    [["audit.view"], ["dashboard", "audit"], ["system"]],
    [[], ["dashboard"], []],
  ];
  for (const [permissions, keys, groups] of roles) {
    const restricted = await browser.newPage({
      viewport: { width: 1440, height: 1080 },
    });
    restricted.on("pageerror", (e) => errors.push(e.message));
    await restricted.route("**/preview-meta.json", async (route) => {
      const response = await route.fetch();
      const meta = await response.json();
      meta.actor = {
        id: 999,
        name: "نقش آزمایشی محدود",
        owner: false,
        permissions,
      };
      await route.fulfill({ response, json: meta });
    });
    await restricted.goto(base);
    await restricted
      .getByRole("heading", { name: "همه‌چیز، تحت کنترل شما" })
      .waitFor();
    const actual = await restricted
      .locator(".sidebar [data-nav]")
      .evaluateAll((items) => items.map((i) => i.dataset.nav));
    const actualGroups = await restricted
      .locator(".sidebar [data-nav-group]")
      .evaluateAll((items) => items.map((i) => i.dataset.navGroup));
    if (
      JSON.stringify(actual) !== JSON.stringify(keys) ||
      JSON.stringify(actualGroups) !== JSON.stringify(groups)
    )
      throw Error(
        "Role-based categories leaked or hid actions: " + permissions.join(","),
      );
    for (const group of await restricted.locator(".nav-group").all()) {
      await group.locator("summary").focus();
      await restricted.keyboard.press("Enter");
      await group.locator(".nav-item").first().waitFor({ state: "visible" });
    }
    await restricted.close();
  }
  if (errors.length) throw Error(errors.join("\n"));
  console.log(
    "PASS: grouped desktop/mobile navigation, 7 scoped role menus, keyboard expansion, dirty-edit protection, plan and ticket preview/publish, no runtime exceptions, text-as-HTML execution or mobile overflow",
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
