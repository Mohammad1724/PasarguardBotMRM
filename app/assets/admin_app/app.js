/* No credentials in URLs/localStorage. Demo transport exists only on the separate preview server. */
"use strict";
const $ = (s) => document.querySelector(s),
  esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
const fa = (v) =>
    new Intl.NumberFormat("fa-IR").format(
      typeof v === "string" && /^-?\d+$/.test(v) ? BigInt(v) : (v ?? 0),
    ),
  date = (v) =>
    v
      ? new Intl.DateTimeFormat("fa-IR", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          timeZone: "Asia/Tehran",
        }).format(new Date(v * 1000))
      : "—";
const icons = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  sliders: "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6",
  menu: "M4 5h16 M4 12h16 M4 19h16",
  layers: "M12 3 2 8l10 5 10-5-10-5 M2 12l10 5 10-5 M2 16l10 5 10-5",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M16 3a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  server: "M3 3h18v7H3z M3 14h18v7H3z M6 6h.01 M6 17h.01 M10 6h7 M10 17h7",
  ticket:
    "M21 15a4 4 0 0 1-4 4H7l-4 3V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3z M7 8h10 M7 12h6",
  wallet:
    "M20 8V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h16v12H5a3 3 0 0 1-3-3V6 M21 13h-6v4h6",
  shield: "M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6z M8 12l3 3 5-6",
  history: "M3 11a9 9 0 1 1 2 7 M3 4v7h7 M12 7v5l3 2",
  plus: "M12 5v14 M5 12h14",
  arrow: "M18 12H6 M11 7l-5 5 5 5",
  check: "M5 12l4 4L19 6",
  search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  refresh: "M20 7v5h-5 M4 17v-5h5 M6 6a8 8 0 0 1 13 2 M18 18a8 8 0 0 1-13-2",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4",
  logout: "M9 4H3v16h6 M9 12h12 M17 8l4 4-4 4",
  edit: "m16 3 5 5-12 12-6 1 1-6z M14 5l5 5",
  file: "M14 2H4v20h16V8z M14 2v6h6 M8 13h8 M8 17h6",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
};
const ico = (k) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[k] || icons.grid}"/></svg>`;
const titles = {
  dashboard: "نمای کلی",
  settings: "تنظیمات ربات",
  appearance: "منو و ظاهر",
  texts: "متن‌های ربات",
  plans: "مدیریت پلن‌ها",
  users: "کاربران",
  services: "سرویس‌ها",
  tickets: "تیکت‌های پشتیبانی",
  finance: "گزارش مالی",
  renewals: "رسیدهای تمدید خودکار",
  grants: "تیم و دسترسی‌ها",
  audit: "تاریخچه تغییرات",
};
const sectionNames = {
  core_settings: "عمومی و قابلیت‌ها",
  payment_settings: "پرداخت و کیف پول",
  purchase_settings: "خرید و تمدید",
  service_tools_settings: "ابزارهای سرویس",
  reseller_settings: "نمایندگی",
};
const labels = {
  bot_mode: "فعال‌بودن ربات",
  sale_mode: "فروش سرویس",
  single_panel_buy_mode: "خرید تک‌پنل",
  channel_lock: "عضویت اجباری کانال",
  backup_interval_hours: "فاصله بکاپ خودکار (ساعت)",
  cx_tickets_enabled: "سیستم تیکت",
  cx_onboarding_enabled: "راهنمای شروع مشتری",
  cx_conversion_enabled: "تبدیل تست به اشتراک",
  cx_followup_enabled: "پیگیری رضایت‌محور تست",
  auto_renew_enabled: "تمدید خودکار از کیف پول",
  profile_mode: "نمایش پروفایل",
  help_mode: "نمایش راهنما",
  support_mode: "نمایش پشتیبانی",
  advanced_settings_mode: "تنظیمات پیشرفته کاربر",
  gift_mode: "کد هدیه",
  pay_mode: "پرداخت کارت‌به‌کارت",
  pay_phone_verify: "تأیید شماره برای پرداخت",
  arz_mode: "پرداخت ارزی",
  manual_card_visibility: "نمایش کارت بانکی",
  manual_auto_confirm: "تأیید خودکار کارت‌به‌کارت",
  manual_card_random_mode: "انتخاب تصادفی کارت",
  manual_deposit_min: "حداقل شارژ دستی (تومان)",
  manual_deposit_max: "حداکثر شارژ دستی (تومان)",
  crypto_deposit_min: "حداقل شارژ ارزی",
  crypto_deposit_max: "حداکثر شارژ ارزی",
  manual_bonus_enabled: "پاداش شارژ دستی",
  manual_bonus_percent: "درصد پاداش شارژ دستی",
  crypto_bonus_enabled: "پاداش شارژ ارزی",
  crypto_bonus_percent: "درصد پاداش شارژ ارزی",
  arz_usd: "نرخ دلار",
  arz_trx: "نرخ TRX",
  arz_ton: "نرخ TON",
  zarinpal_mode: "پرداخت زرین‌پال",
  zarinpal_sandbox: "حالت آزمایشی زرین‌پال",
  zarinpal_deposit_min: "حداقل پرداخت زرین‌پال",
  zarinpal_deposit_max: "حداکثر پرداخت زرین‌پال",
  stars_mode: "پرداخت استارز",
  stars_rate: "ارزش هر استار (تومان)",
  stars_deposit_min: "حداقل پرداخت استارز",
  stars_deposit_max: "حداکثر پرداخت استارز",
  referral_enabled: "برنامه معرفی دوستان",
  referral_percent: "درصد پاداش معرفی",
  referral_first_bonus: "پاداش اولین شارژ معرف",
  referral_min_deposit: "حداقل شارژ برای پاداش",
  extension_mode: "افزایش زمان",
  upg_mode: "افزایش حجم",
  tamdid_mode: "تمدید سرویس",
  test_mode: "ارائه سرویس تست",
  test_panel_id: "شناسه پنل تست",
  test_phone_verify: "تأیید شماره برای تست",
  direct_pay_purchase_mode: "خرید پس از پرداخت مستقیم",
  direct_pay_renew_mode: "تمدید پس از پرداخت مستقیم",
  qr_mode: "نمایش QR",
  sub_mode: "نمایش ساب",
  other_links_mode: "سایر لینک‌ها",
  client_list_mode: "فهرست کلاینت‌ها",
  usage_chart_mode: "نمودار مصرف",
  change_link_mode: "تغییر لینک",
  copy_link_mode: "کپی لینک",
  transfer_config_mode: "انتقال سرویس",
  info_mode: "اطلاعات سرویس",
  del_service_mode: "حذف سرویس",
  reseller_sale_mode: "فروش نمایندگی",
  reseller_min_wallet_balance: "حداقل موجودی نماینده",
};
const statusLabels = {
  open: "باز",
  in_progress: "در حال بررسی",
  waiting: "منتظر مشتری",
  closed: "بسته",
  ban: "مسدود",
  pending: "در انتظار",
  completed: "تکمیل‌شده",
  approved: "تأییدشده",
  rejected: "ردشده",
  applying: "در حال تطبیق",
  review: "نیازمند بررسی",
  applied: "اعمال‌شده",
  refunded: "بازپرداخت‌شده",
  published: "منتشرشده",
  draft: "پیش‌نویس",
  cancelled: "لغوشده",
  enabled: "روشن",
  off: "خاموش",
  paused: "متوقف",
};
const badge = (s, label) =>
  `<span class="badge ${["ban", "rejected", "review"].includes(s) ? "danger" : ["pending", "draft", "applying", "waiting"].includes(s) ? "warning" : ["closed", "cancelled", "off"].includes(s) ? "neutral" : ""}">${esc(label || statusLabels[s] || s || "فعال")}</span>`;
const S = {
  token: null,
  meta: null,
  page: "dashboard",
  listPage: 1,
  q: "",
  edit: null,
  save: null,
  draft: null,
  dirty: false,
  layout: [],
  buttonConfigs: {},
  route: 0,
  openGroups: new Set(),
};
const can = (p) => S.meta?.actor.owner || S.meta?.actor.permissions.includes(p);
const btn = (text, action, kind = "", extra = "") =>
  `<button class="btn ${kind}" data-action="${action}" ${extra}>${text}</button>`;
const toast = (message, error = false) => {
  const t = $("#toast");
  t.textContent = message;
  t.className = error ? "error" : "";
  t.style.display = "block";
  clearTimeout(S.toastTimer);
  S.toastTimer = setTimeout(() => (t.style.display = "none"), 5000);
};
async function api(path, body) {
  if (window.AdminDemo) return window.AdminDemo.request(path, body);
  const r = await fetch("/admin/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      ...(S.token ? { Authorization: "Bearer " + S.token } : {}),
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "omit",
    cache: "no-store",
  });
  let d;
  try {
    d = await r.json();
  } catch {
    throw Error("پاسخ سرور معتبر نیست. تنظیمات HTTPS و API را بررسی کنید.");
  }
  if (!r.ok) {
    if (r.status === 401) S.token = null;
    const error = Error(d.detail || "عملیات انجام نشد.");
    error.status = r.status;
    throw error;
  }
  return d;
}
function modal(title, html, footer = "") {
  const d = $("#dialog");
  d.innerHTML = `<div class="dialog-head"><h3>${esc(title)}</h3><button data-action="close-modal" aria-label="بستن">×</button></div><div class="dialog-content">${html}</div>${footer ? `<div class="editor-footer">${footer}</div>` : ""}`;
  if (!d.open) d.showModal();
}
const navigationGroups = [
  {
    key: "customers",
    title: "کاربران و پشتیبانی",
    icon: "users",
    items: [
      ["users", "users", "users.view"],
      ["services", "server", "services.view"],
      ["tickets", "ticket", "tickets.view"],
    ],
  },
  {
    key: "sales",
    title: "فروش و مالی",
    icon: "wallet",
    items: [
      ["plans", "layers", "plans.manage"],
      ["finance", "wallet", "finance.view"],
      ["renewals", "refresh", "finance.view"],
    ],
  },
  {
    key: "appearance",
    title: "ظاهر و محتوا",
    icon: "menu",
    items: [
      ["appearance", "menu", "appearance.manage"],
      ["texts", "file", "appearance.manage"],
    ],
  },
  {
    key: "system",
    title: "مدیریت سیستم",
    icon: "shield",
    items: [
      ["settings", "sliders", "settings.manage"],
      ["grants", "shield", "owner"],
      ["audit", "history", "audit.view"],
    ],
  },
];
function navigationItem([key, icon]) {
  const active = S.page === key;
  return `<button class="nav-item ${active ? "active" : ""}" data-nav="${key}" ${active ? 'aria-current="page"' : ""}>${ico(icon)}<span>${titles[key]}</span>${active ? '<span class="chevron">‹</span>' : ""}</button>`;
}
function groupedNavigation() {
  return (
    navigationItem(["dashboard", "grid"]) +
    navigationGroups
      .map((group) => {
        const items = group.items.filter(([key, _, permission]) =>
          permission === "owner"
            ? S.meta.actor.owner
            : can(permission) || (key === "settings" && can("payments.manage")),
        );
        if (!items.length) return "";
        const active = items.some(([key]) => key === S.page);
        return `<details class="nav-group ${active ? "active" : ""}" data-nav-group="${group.key}" ${S.openGroups.has(group.key) ? "open" : ""}><summary>${ico(group.icon)}<span>${group.title}</span><small class="group-count">${fa(items.length)}</small><span class="group-arrow" aria-hidden="true">‹</span></summary><div class="nav-group-items">${items.map(navigationItem).join("")}</div></details>`;
      })
      .join("")
  );
}
function shell() {
  const group = navigationGroups.find((group) =>
    group.items.some(([key]) => key === S.page),
  );
  $("#app").innerHTML =
    `<aside class="sidebar"><div class="brand"><div class="brand-mark">پ</div><div><strong>پاسارگارد</strong><small>CONTROL CENTER</small></div></div><div class="nav-label">فضای کاری شما</div><nav aria-label="دسته‌های مدیریت">${groupedNavigation()}</nav><div class="sidebar-bottom"><small><span class="system-dot"></span>${window.AdminDemo ? "محیط پیش‌نمایش آزمایشی" : "نشست امن تلگرام"}</small><div class="profile"><div class="avatar">${ico("shield")}</div><div><strong>${esc(S.meta.actor.name)}</strong><small>${S.meta.actor.owner ? "دسترسی مالک" : "دسترسی محدود"} · نسخه ۳</small></div><button data-action="logout" title="خروج" aria-label="خروج">${ico("logout")}</button></div></div></aside><div class="main"><header class="topbar"><div class="breadcrumbs"><button class="mobile-menu" data-action="toggle-nav" aria-label="منو">${ico("menu")}</button><span>مرکز مدیریت</span><span> / </span>${group ? `<span class="breadcrumb-group">${group.title}</span><span class="breadcrumb-group"> / </span>` : ""}<strong>${titles[S.page]}</strong></div><div class="top-actions"><span class="chip">${new Intl.DateTimeFormat("fa-IR", { dateStyle: "long" }).format(new Date())}</span><span class="chip"><span class="system-dot"></span>${window.AdminDemo ? "داده آزمایشی" : "متصل به ربات"}</span><button data-action="refresh" title="تازه‌سازی" aria-label="تازه‌سازی">${ico("refresh")}</button></div></header><main class="workspace" id="workspace"><div class="empty">در حال دریافت اطلاعات…</div></main></div>`;
}
const head = (title, desc, action = "") =>
  `<div class="page-head"><div><h1>${esc(title)}</h1><p>${esc(desc)}</p></div>${action}</div>`;
const banner = () =>
  window.AdminDemo
    ? `<div class="banner warning">${ico("shield")}<span>این یک پیش‌نمایش تعاملی با داده آزمایشی است؛ هیچ تغییر یا پرداختی روی ربات واقعی انجام نمی‌شود.</span></div>`
    : "";
function footer() {
  return '<div class="footnote">پاسارگارد · مرکز مدیریت نسخه سوم — تغییرات مهم، فقط با تأیید شما</div>';
}
async function navigate(page, force = false) {
  if (
    S.dirty &&
    !force &&
    !confirm("تغییرات ذخیره نشده است. از این صفحه خارج می‌شوید؟")
  )
    return;
  S.dirty = false;
  S.page = page;
  const group = navigationGroups.find((group) =>
    group.items.some(([key]) => key === page),
  );
  if (group) S.openGroups.add(group.key);
  S.edit = null;
  S.save = null;
  S.listPage = 1;
  S.q = "";
  const route = ++S.route;
  shell();
  try {
    if (page === "dashboard") await dashboard(route);
    else if (page === "settings") await settings();
    else if (page === "appearance") await appearance();
    else if (page === "texts") await textPage();
    else await listPage();
  } catch (e) {
    $("#workspace").innerHTML =
      head(titles[page], "") +
      `<div class="card empty">${ico("shield")}${esc(e.message)}<br>${btn("تلاش مجدد", "refresh")}</div>`;
  }
}
async function dashboard(route) {
  const d = await api("/dashboard");
  if (route !== S.route) return;
  const metrics = [
    ["users", "کاربران ربات", "users", "نفر"],
    ["services", "سرویس‌های ثبت‌شده", "server", "سرویس"],
    ["wallet_liability", "موجودی کیف پول‌ها", "wallet", "تومان"],
    ["tickets", "تیکت‌های باز", "ticket", "تیکت"],
    ["plans", "پلن‌های ثبت‌شده", "layers", "پلن"],
  ]
    .filter(([k]) => k in d.counts)
    .slice(0, 4);
  let recent = [];
  if (can("audit.view"))
    recent = (await api("/list/audit?page=1")).items.slice(0, 4);
  const points = d.activity || [];
  const max = Math.max(...points, 1),
    coords = points
      .map((v, i) => `${15 + i * 80},${155 - (v / max) * 125}`)
      .join(" ");
  const total = points.reduce((a, b) => a + b, 0);
  $("#workspace").innerHTML =
    head(
      "همه‌چیز، تحت کنترل شما",
      "یک نگاه به وضعیت ربات؛ یک مسیر روشن برای هر تغییر.",
      can("plans.manage")
        ? btn(ico("plus") + "ساخت پلن جدید", "new-plan", "primary")
        : "",
    ) +
    banner() +
    `<section class="stats">${metrics.map(([k, t, i, u]) => `<div class="stat"><div class="stat-top"><span>${t}</span><div class="stat-icon">${ico(i)}</div></div><div class="stat-value">${fa(d.counts[k])}<span class="stat-unit">${u}</span></div><small><span class="system-dot"></span>${window.AdminDemo ? "مقادیر نمونه برای پیش‌نمایش" : "داده ثبت‌شده در پایگاه ربات"}</small></div>`).join("")}</section><section class="grid-main"><div class="card"><div class="card-head"><div><h3>فعالیت کیف پول</h3><p>تعداد درخواست‌های شارژ ثبت‌شده، مستقل از وضعیت پرداخت</p></div><span class="chip">۷ روز اخیر</span></div><div class="card-body">${can("finance.view") ? `<div class="chart-total">${fa(total)} <span class="stat-unit">درخواست</span></div><div class="chart-wrap"><svg class="chart" viewBox="0 0 510 175" preserveAspectRatio="none"><defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#5dbb96" stop-opacity=".25"/><stop offset="100%" stop-color="#5dbb96" stop-opacity="0"/></linearGradient></defs>${[30, 70, 110, 150].map((y) => `<path d="M0 ${y}H510" stroke="#eef3f1" stroke-dasharray="4 4"/>`).join("")}<polygon points="15,170 ${coords} 495,170" fill="url(#fill)" stroke="none"/><polyline points="${coords}" stroke="#329776" fill="none" stroke-width="2.5"/></svg><div class="chart-days">${Array.from({ length: 7 }, (_, i) => `<span>${i === 6 ? "۲۴ ساعت اخیر" : fa(7 - i) + " روز پیش"}</span>`).join("")}</div></div>` : '<div class="empty">مشاهده این بخش نیازمند مجوز مالی است.</div>'}</div></div><div class="card hero"><div class="card-body"><div class="eyebrow">طراحی تجربه مشتری</div><h2>ربات شما،<br>با سلیقه شما.</h2><p>متن‌ها، رنگ‌ها و چیدمان منوی اصلی را تغییر دهید. قبل از انتشار، نتیجه را ببینید.</p>${can("appearance.manage") ? btn("شخصی‌سازی منو " + ico("arrow"), "goto-appearance") : ""}</div></div></section><section class="grid-bottom"><div class="card"><div class="card-head"><div><h3>آخرین تغییرات</h3><p>ردپای روشن تصمیم‌های تیم شما</p></div>${can("audit.view") ? btn("مشاهده همه ‹", "goto-audit", "ghost") : ""}</div>${recent.length ? `<div class="table-wrap"><table><thead><tr><th>تغییر</th><th>ادمین</th><th>وضعیت</th><th>زمان</th></tr></thead><tbody>${recent.map((r) => `<tr><td>${esc(entityTitle(r.entity))}<div class="sub">${esc(r.reason)}</div></td><td dir="ltr">#${r.actor_id}</td><td>${badge(r.status)}</td><td>${date(r.created_at)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">تغییری برای نمایش وجود ندارد یا مجوز تاریخچه ندارید.</div>'}</div><div class="card"><div class="card-head"><h3>دسترسی سریع</h3><span class="subtle">مسیرهای پرکاربرد</span></div><div class="card-body quick">${[
      [
        "settings",
        "sliders",
        "تنظیمات ربات",
        "قابلیت‌ها و رفتار ربات",
        "settings.manage",
      ],
      ["plans", "layers", "مدیریت پلن‌ها", "قیمت، حجم و زمان", "plans.manage"],
      ["tickets", "ticket", "پشتیبانی", "رسیدگی به درخواست‌ها", "tickets.view"],
      ["grants", "shield", "تیم مدیریت", "هر نفر، دسترسی مشخص", "owner"],
    ]
      .filter(([, , , , p]) => (p === "owner" ? S.meta.actor.owner : can(p)))
      .map(
        ([p, i, t, s]) =>
          `<button data-nav="${p}">${ico(i)}${t}<small>${s}</small></button>`,
      )
      .join("")}</div></div></section>` +
    footer();
}
function entityTitle(e) {
  return (
    {
      buttons: "متن و استایل دکمه",
      layout: "چیدمان منو",
      texts: "متن ربات",
      plans: "پلن فروش",
      settings: "تنظیمات",
      grants: "دسترسی ادمین",
      users: "وضعیت کاربر",
      wallet: "موجودی کیف پول",
      services: "تمدید خودکار",
      tickets: "تیکت",
    }[e] || e
  );
}
function saveFooter(text = "بررسی و تأیید تغییرات") {
  return `<div class="editor-footer"><small>تا تأیید نهایی، چیزی در ربات تغییر نمی‌کند.</small>${btn("انصراف", "refresh")}${btn(ico("check") + text, "save", "primary")}</div>`;
}
async function loadDoc(entity, target) {
  const doc = await api(`/document/${entity}/${encodeURIComponent(target)}`);
  S.edit = { entity, target: String(target), ...doc };
  return structuredClone(doc.value);
}
async function review(value, reason) {
  if (!S.edit) throw Error("اطلاعات فرم در دسترس نیست.");
  const d = await api("/changes", {
    entity: S.edit.entity,
    target: S.edit.target,
    version: S.edit.version,
    value,
    reason: reason || "تغییر از مرکز مدیریت",
  });
  showDraft(d);
}
function showDraft(d) {
  S.draft = d;
  const wallet = d.entity === "wallet";
  const preview = wallet
    ? `<div class="diff"><div><label>موجودی فعلی</label><h2>${fa(d.before.amount)} <small>تومان</small></h2></div><div><label>موجودی جدید</label><h2>${fa(d.after.amount)} <small>تومان</small></h2></div></div><p class="note">اختلاف موجودی: <strong>${fa(BigInt(d.after.amount) - BigInt(d.before.amount))} تومان</strong><br>دلیل: ${esc(d.reason)}</p>`
    : `<div class="diff"><div><label>قبل از تغییر</label><pre>${esc(JSON.stringify(d.before, null, 2))}</pre></div><div><label>بعد از تغییر</label><pre>${esc(JSON.stringify(d.after, null, 2))}</pre></div></div>`;
  modal(
    wallet ? "تأیید موجودی کاربر #" + d.target : "بررسی نهایی تغییر",
    `<div class="banner warning">${ico("shield")}<span>${esc(d.warning || "این تغییر فقط پس از تأیید نهایی اعمال می‌شود.")}</span></div>${preview}<label class="confirm-label"><input type="checkbox" id="confirm-change">تغییرات را بررسی کردم و با اعمال آن‌ها موافقم.</label>`,
    btn("لغو پیش‌نویس", "discard") +
      btn("تأیید و انتشار", "publish", "primary", "disabled"),
  );
}
async function settings(section) {
  const sections = Object.keys(sectionNames).filter((k) =>
    can(k === "payment_settings" ? "payments.manage" : "settings.manage"),
  );
  section = section || sections[0];
  const v = await loadDoc("settings", section);
  S.settingsSection = section;
  $("#workspace").innerHTML =
    head(
      "تنظیمات ربات",
      "تنظیمات روزمره را از یک نقطه مدیریت کنید؛ کلیدهای محرمانه فقط روی سرور می‌مانند.",
    ) +
    banner() +
    `<div class="tabs">${sections.map((k) => `<button class="${k === section ? "active" : ""}" data-section="${k}">${sectionNames[k]}</button>`).join("")}</div><div class="card"><div class="card-head"><div><h3>${sectionNames[section]}</h3><p>تغییرات این صفحه به همان تنظیمات ربات متصل است.</p></div>${ico("sliders")}</div><form id="editor-form" class="card-body">${Object.entries(
      v,
    )
      .map(
        ([k, value]) =>
          `<div class="setting-row"><div class="label"><strong>${esc(labels[k] || k)}</strong><small>${esc(k)}</small></div>${typeof value === "boolean" ? `<input class="switch" type="checkbox" name="${k}" ${value ? "checked" : ""} aria-label="${esc(labels[k] || k)}">` : k === "manual_card_visibility" ? `<select name="${k}" aria-label="نمایش کارت"><option value="">پیش‌فرض</option value="all" ${value === "all" ? "selected" : ""}>همه کاربران</option value="safe_mode" ${value === "safe_mode" ? "selected" : ""}>فقط سیف‌مود</option></select>` : `<input type="number" name="${k}" value="${esc(value)}" min="0" step="1" aria-label="${esc(labels[k] || k)}">`}</div>`,
      )
      .join("")}</form>${saveFooter()}</div>` +
    footer();
  S.save = () => {
    const f = $("#editor-form"),
      data = {};
    for (const [k, value] of Object.entries(v)) {
      const el = f.elements.namedItem(k);
      data[k] =
        typeof value === "boolean"
          ? el.checked
          : k === "manual_card_visibility"
            ? el.value || null
            : Number(el.value);
    }
    return review(data, "تغییر " + sectionNames[section]);
  };
}
const defaultRows = [
  ["bt.menu_get_trial"],
  ["bt.menu_my_services", "bt.menu_buy_service"],
  ["bt.menu_my_resellers", "bt.menu_buy_reseller"],
  ["bt.menu_profile", "bt.menu_add_balance"],
  ["bt.menu_gift"],
  ["bt.menu_support", "bt.menu_uptime", "bt.menu_help"],
  ["bt.menu_advanced_settings"],
];
const buttonLabel = (k) =>
  S.buttonConfigs[k]?.text ||
  S.meta.buttons.find((x) => x.key === k)?.default ||
  k;
function phone(rows = defaultRows, override) {
  return `<div class="phone"><div class="phone-top"><div class="avatar">پ</div><div><strong>ربات پاسارگارد</strong><small>bot</small></div></div><div class="phone-conversation"><div class="bubble">به پاسارگارد خوش آمدید 🌿<br>از منوی زیر، گزینه مورد نظر را انتخاب کنید.<small>۱۲:۴۱ ✓✓</small></div></div><div class="phone-keyboard">${rows
    .map(
      (row) =>
        `<div class="phone-row">${row
          .map((k) => {
            let b =
              override?.key === k
                ? override
                : S.buttonConfigs[k] || { text: buttonLabel(k) };
            return `<div class="phone-button ${["primary", "success", "danger"].includes(b.style) ? b.style : ""}" ${override?.key === k ? 'id="button-preview"' : ""}>${esc(b.text)}</div>`;
          })
          .join("")}</div>`,
    )
    .join(
      "",
    )}</div></div><p class="preview-caption">پیش‌نمایش تقریبی؛ نمایش واقعی وابسته به تلگرام و شرایط دسترسی هر مشتری است.</p>`;
}
async function appearance(tab = "layout", key) {
  S.appearanceTab = tab;
  $("#workspace").innerHTML =
    head(
      "ربات را به سبک خودتان بچینید",
      "متن دکمه‌ها، استایل رسمی تلگرام و چیدمان منوی اصلی؛ بدون تغییر عملکرد دکمه‌ها.",
    ) +
    banner() +
    `<div class="tabs"><button data-appearance="layout" class="${tab === "layout" ? "active" : ""}">چیدمان منوی اصلی</button><button data-appearance="buttons" class="${tab === "buttons" ? "active" : ""}">متن و رنگ دکمه‌ها</button></div><div id="appearance-editor"></div>`;
  if (tab === "layout") {
    const v = await loadDoc("layout", "home");
    S.layout = v.rows.length
      ? structuredClone(v.rows)
      : structuredClone(defaultRows);
    S.layoutReset = !v.rows.length;
    const configs = await Promise.all(
      S.meta.home_keys.map(async (k) => [
        k,
        (await api("/document/buttons/" + k)).value,
      ]),
    );
    S.buttonConfigs = Object.fromEntries(configs);
    drawLayout();
    S.save = () =>
      review(
        { rows: S.layoutReset ? [] : S.layout.filter((r) => r.length) },
        "انتشار چیدمان منوی اصلی",
      );
  } else {
    key = key || S.meta.buttons[0].key;
    const v = await loadDoc("buttons", key);
    S.buttonStyle = v.style;
    $("#appearance-editor").innerHTML =
      `<div class="builder-grid"><div class="card"><div class="card-head"><h3>ویرایش دکمه</h3>${ico("edit")}</div><form class="card-body" id="editor-form"><label>دکمه مورد نظر</label><select id="button-key">${S.meta.buttons.map((b) => `<option value="${esc(b.key)}" ${b.key === key ? "selected" : ""}>${esc(b.title)}</option>`).join("")}</select><div class="divider"></div><label>متن دکمه</label><input name="text" id="button-text" maxlength="64" value="${esc(v.text)}"><div class="divider"></div><label>استایل تلگرام</label><div class="color-choices">${[
        [null, "#f1f4f4", "پیش‌فرض"],
        ["primary", "#5e98cf", "آبی"],
        ["success", "#51a67b", "سبز"],
        ["danger", "#d67474", "قرمز"],
      ]
        .map(
          ([s, c, t]) =>
            `<button type="button" class="color-choice ${s === v.style ? "selected" : ""}" data-color="${s || ""}" title="${t}" aria-label="${t}" style="background:${c}"></button>`,
        )
        .join(
          "",
        )}</div><p class="note">چهار استایل رسمی؛ رنگ دلخواه HEX فقط برای رابط خود مینی‌اپ ممکن است.</p><div class="divider"></div><label>شناسه ایموجی سفارشی (اختیاری)</label><input name="icon" value="${esc(v.icon || "")}" inputmode="numeric" placeholder="شناسه رسمی ایموجی، نه نام یا لینک"><p class="note">استفاده از ایموجی سفارشی تابع محدودیت‌های تلگرام است.</p></form>${saveFooter()}</div><div>${phone([[key]], { key, ...v })}</div></div>`;
    S.save = () =>
      review(
        {
          text: $("#button-text").value,
          style: S.buttonStyle,
          icon: $("#editor-form").elements.icon.value || null,
        },
        "ویرایش دکمه " + key,
      );
  }
}
function drawLayout() {
  const used = S.layout.flat();
  $("#appearance-editor").innerHTML =
    `<div class="builder-grid"><div class="card"><div class="card-head"><div><h3>چیدمان دکمه‌ها</h3><p>بکشید و رها کنید؛ یا از فلش‌های روی دکمه استفاده کنید.</p></div><span class="chip">${fa(used.length)} دکمه</span></div><div class="card-body">${S.layout.map((row, i) => `<div class="menu-row" data-row="${i}"><small>${fa(i + 1)}</small>${row.map((k) => `<div class="menu-token" draggable="true" data-drag-key="${esc(k)}"><span class="handle">⠿</span><span>${esc(buttonLabel(k))}</span><div><button data-move="${esc(k)}" title="ردیف بالاتر" aria-label="ردیف بالاتر">↑</button><button data-left="${esc(k)}" title="جابه‌جایی در ردیف" aria-label="جابه‌جایی در ردیف">←</button>${!["bt.menu_my_services", "bt.menu_add_balance"].includes(k) ? `<button data-remove="${esc(k)}" title="مخفی‌کردن" aria-label="مخفی‌کردن">×</button>` : ""}</div></div>`).join("")}${!row.length ? '<span class="subtle">دکمه را اینجا رها کنید</span>' : ""}</div>`).join("")}<div class="menu-tools"><select id="add-key" aria-label="دکمه جدید"><option value="">انتخاب دکمه پنهان…</option>${S.meta.home_keys
      .filter((k) => !used.includes(k))
      .map((k) => `<option value="${k}">${esc(buttonLabel(k))}</option>`)
      .join(
        "",
      )}</select>${btn(ico("plus"), "add-key")}</div><div class="menu-tools">${btn("ردیف جدید", "add-row")}${btn("چیدمان پیش‌فرض", "reset-layout", "ghost")}</div><p class="note">«سرویس‌های من» و «کیف پول» قابل حذف نیستند. دکمه مدیریت مالک ثابت می‌ماند. نمایش بقیه دکمه‌ها همچنان تابع تنظیمات و مجوز کاربر است.</p></div>${saveFooter()}</div><div>${phone(S.layout)}</div></div>`;
}
async function textPage(key) {
  const texts = Object.entries(S.meta.texts);
  key = key || texts[0][0];
  const v = await loadDoc("texts", key);
  $("#workspace").innerHTML =
    head(
      "کلمات شما، صدای ربات شما",
      "فقط متن‌های ثبت‌شده قابل ویرایش‌اند؛ متغیرهای ضروری حفظ می‌شوند.",
    ) +
    banner() +
    `<div class="card"><div class="card-head"><h3>ویرایش متن</h3>${ico("file")}</div><form class="card-body" id="editor-form"><label>انتخاب پیام</label><select id="text-key">${texts.map(([k, t]) => `<option value="${esc(k)}" ${key === k ? "selected" : ""}>${esc(t.title)}</option>`).join("")}</select><div class="divider"></div><label>متن پیام · حداکثر ۳۰۰۰ نویسه</label><textarea name="text" maxlength="3000">${esc(v.text)}</textarea><p class="note">متغیرهای مجاز: ${esc(
      Object.keys(S.meta.texts[key].placeholders || {})
        .map((k) => "{" + k + "}")
        .join(" • ") || "این پیام متغیر ندارد.",
    )}<br>HTML دلخواه اجرا نمی‌شود؛ قالب‌بندی پیام تابع ربات و تلگرام است.</p></form>${saveFooter()}</div>`;
  S.save = () =>
    review(
      { text: $("#editor-form").elements.text.value },
      "ویرایش متن " + key,
    );
  $("#editor-form").insertAdjacentHTML(
    "beforeend",
    btn("بازگشت به متن پیش‌فرض", "reset-text", "ghost", 'type="button"'),
  );
}
const listColumns = {
  plans: ["نام پلن", "حجم / زمان", "قیمت", "پنل", "وضعیت", "عملیات"],
  users: ["کاربر", "وضعیت", "عضویت", "کیف پول", "عملیات"],
  services: ["سرویس", "مالک", "پنل", "انقضا", "وضعیت محلی", "عملیات"],
  tickets: ["تیکت", "کاربر", "موضوع", "وضعیت", "آخرین فعالیت", "عملیات"],
  finance: ["تراکنش", "کاربر", "مبلغ (تومان)", "روش", "وضعیت", "زمان"],
  renewals: ["درخواست", "سرویس", "مبلغ (تومان)", "ماه", "وضعیت", "زمان"],
  grants: ["ادمین", "نام نقش", "دسترسی‌ها", "آخرین تغییر", "عملیات"],
  audit: ["تغییر", "ادمین", "بخش", "وضعیت", "زمان", "عملیات"],
};
function tableRow(e, r) {
  const action = (label, kind, id) =>
    btn(label, kind, "", `data-id="${esc(id)}"`);
  if (e === "plans")
    return [
      `<strong>${esc(r.display_button_text || "پلن #" + r.id)}</strong><div class="sub">${r.plan_type === "volume" ? "حجمی" : "مصرف منصفانه"}</div>`,
      `${fa(r.storage)} گیگ · ${fa(r.duration)} روز`,
      fa(r.price),
      `#${r.panel_code}`,
      badge(r.enabled ? "enabled" : "off", r.enabled ? "فعال" : "غیرفعال"),
      `<div class="inline-actions">${action("ویرایش", "edit-plan", r.id)}${action("تکثیر", "duplicate-plan", r.id)}</div>`,
    ];
  if (e === "users")
    return [
      `<strong dir="ltr">#${r.id}</strong>`,
      badge(r.status),
      date(r.time_s),
      "amount" in r ? fa(r.amount) : "محدود",
      `<div class="inline-actions">${can("users.manage") ? action("وضعیت", "edit-user", r.id) : ""}${S.meta.actor.owner ? action("تغییر موجودی", "edit-wallet", r.id) : ""}</div>`,
    ];
  if (e === "services")
    return [
      `<strong>${esc(r.username)}</strong><div class="sub">#${r.code}${r.is_test ? " · تست" : ""}</div>`,
      `#${r.id}`,
      `#${r.in_panel}`,
      date(r.expiration_time),
      badge(r.enable ? "enabled" : "off", r.enable ? "فعال" : "غیرفعال"),
      can("services.manage")
        ? action("تمدید خودکار", "edit-service", r.code)
        : "—",
    ];
  if (e === "tickets")
    return [
      `#${r.id}`,
      `#${r.user_id}`,
      esc(
        {
          connection: "اتصال",
          payment: "پرداخت",
          renewal: "تمدید",
          other: "سایر",
        }[r.topic] || r.topic,
      ),
      badge(r.status),
      date(r.updated_at),
      action("مشاهده", "open-ticket", r.id),
    ];
  if (e === "finance")
    return [
      `#${r.id}`,
      `#${r.user_id}`,
      fa(r.amount),
      esc(r.method),
      badge(r.status),
      date(r.created_at),
    ];
  if (e === "renewals")
    return [
      esc(r.token.slice(0, 10)),
      `#${r.service_code}`,
      fa(r.price),
      esc(r.month),
      badge(r.status),
      date(r.created_at),
    ];
  if (e === "grants")
    return [
      `#${r.user_id}`,
      esc(r.name),
      `${fa(r.permissions.length)} مجوز`,
      date(r.updated_at),
      action("ویرایش دسترسی", "edit-grant", r.user_id),
    ];
  return [
    `<strong>${esc(r.reason)}</strong><div class="sub">${esc(r.token.slice(0, 10))}</div>`,
    `#${r.actor_id}`,
    esc(entityTitle(r.entity)),
    badge(r.status),
    date(r.created_at),
    action("جزئیات", "open-audit", r.token),
  ];
}
async function listPage() {
  const e = S.page;
  const d = await api(
    `/list/${e}?page=${S.listPage}&q=${encodeURIComponent(S.q)}`,
  );
  S.listItems = d.items;
  const descr = {
    plans:
      "پلن‌های فروش؛ تغییر قیمت یا غیرفعال‌سازی، خریدهای قبلاً پرداخت‌شده را پاک نمی‌کند.",
    users:
      "مسدودسازی اینجا دسترسی به ربات را تغییر می‌دهد، نه وضعیت سرویس روی پنل.",
    services:
      "وضعیت ثبت‌شده محلی؛ این صفحه وضعیت زنده پنل نیست. توقف فقط برای برداشت‌های آینده است.",
    tickets: "گفت‌وگوی مشتری، پاسخ تیم و وضعیت رسیدگی در یک جا.",
    finance:
      "تراکنش‌های ثبت‌شده شارژ کیف پول؛ این جدول، گزارش سود یا فروش نیست.",
    renewals:
      "دفتر جداگانه تمدید از کیف پول؛ بدون قابلیت برداشت یا بازپرداخت اجباری.",
    grants:
      "کمترین دسترسی لازم برای هر نفر؛ فقط مالک می‌تواند مجوز بدهد یا لغو کند.",
    audit:
      "تاریخچه پیش‌نویس‌ها و تغییرات همین مینی‌اپ؛ نه تمام تغییرات ابزارهای خارجی.",
  };
  $("#workspace").innerHTML =
    head(
      titles[e],
      descr[e],
      e === "plans"
        ? btn(ico("plus") + "پلن جدید", "new-plan", "primary")
        : e === "grants"
          ? btn(ico("plus") + "افزودن ادمین", "new-grant", "primary")
          : "",
    ) +
    banner() +
    (e === "finance" || e === "renewals"
      ? `<div class="tabs"><button data-nav="finance" class="${e === "finance" ? "active" : ""}">شارژ کیف پول</button><button data-nav="renewals" class="${e === "renewals" ? "active" : ""}">تمدید خودکار</button></div>`
      : "") +
    `<div class="toolbar"><div class="search">${ico("search")}<input id="list-search" value="${esc(S.q)}" placeholder="جست‌وجوی شناسه${e === "services" ? " یا نام سرویس" : ""}…" maxlength="64" aria-label="جست‌وجو"></div>${btn("جست‌وجو", "search")}<span class="muted">${fa(d.total)} مورد</span></div><div class="card">${
      d.items.length
        ? `<div class="table-wrap"><table><thead><tr>${listColumns[e].map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${d.items
            .map(
              (r) =>
                `<tr>${tableRow(e, r)
                  .map((c) => `<td>${c}</td>`)
                  .join("")}</tr>`,
            )
            .join("")}</tbody></table></div>`
        : `<div class="empty">${ico("search")}موردی پیدا نشد.<br>عبارت جست‌وجو را تغییر دهید یا یک مورد جدید بسازید.</div>`
    }<div class="pagination"><span>صفحه ${fa(d.page)} از ${fa(Math.max(1, Math.ceil(d.total / d.page_size)))}</span><div>${btn("قبلی", "prev", "", d.page === 1 ? "disabled" : "")}${btn("بعدی", "next", "", d.page * d.page_size >= d.total ? "disabled" : "")}</div></div></div>` +
    footer();
}
const field = (name, title, value, type = "text", extra = "") =>
  `<div class="field"><label for="f-${name}">${title}</label><input id="f-${name}" name="${name}" type="${type}" value="${esc(value ?? "")}" ${extra}></div>`;
async function editPlan(id = "new", duplicate = false) {
  let v = await loadDoc("plans", id);
  if (duplicate) {
    delete v.id;
    await loadDoc("plans", "new");
    v.display_button_text = (v.display_button_text || "پلن") + " — کپی";
  }
  const panels = (await api("/list/panels?page=1")).items;
  if (v.panel_code && !panels.some((p) => p.code === v.panel_code))
    panels.push({ code: v.panel_code, name: "پنل فعلی" });
  v = {
    price: 150000,
    storage: 30,
    duration: 30,
    ip_limit: 1,
    panel_code: panels[0]?.code,
    enabled: true,
    plan_type: "volume",
    data_limit_reset_strategy: "no_reset",
    ...v,
  };
  delete v.id;
  S.dirty = false;
  modal(
    id === "new" || duplicate ? "ساخت پلن جدید" : "ویرایش پلن #" + id,
    `<form id="editor-form" class="form-grid">${field("display_button_text", "نام نمایشی پلن", v.display_button_text, "text", 'maxlength="120"')}${field("price", "قیمت (تومان)", v.price, "number", 'min="1" step="1"')}${field("storage", "حجم (گیگابایت؛ صفر = نامحدود)", v.storage, "number", 'min="0" step="any"')}${field("duration", "مدت (روز؛ صفر = نامحدود)", v.duration, "number", 'min="0" step="1"')}${field("ip_limit", "محدودیت دستگاه؛ صفر = نامحدود", v.ip_limit, "number", 'min="0"')}<div class="field"><label>پنل</label><select name="panel_code" ${id !== "new" && !duplicate ? "disabled" : ""}>${panels.map((p) => `<option value="${p.code}" ${p.code === v.panel_code ? "selected" : ""}>${esc(p.name)} #${p.code}</option>`).join("")}</select></div><div class="field"><label>نوع پلن</label><select name="plan_type"><option value="volume">حجمی</option><option value="fair_usage" ${v.plan_type === "fair_usage" ? "selected" : ""}>مصرف منصفانه</option></select></div><div class="field"><label>ریست حجم</label><select name="data_limit_reset_strategy">${[
      ["no_reset", "بدون ریست"],
      ["day", "روزانه"],
      ["week", "هفتگی"],
      ["month", "ماهانه"],
      ["year", "سالانه"],
    ]
      .map(
        ([k, t]) =>
          `<option value="${k}" ${v.data_limit_reset_strategy === k ? "selected" : ""}>${t}</option>`,
      )
      .join(
        "",
      )}</select></div><div class="field"><label>رنگ دکمه پلن</label><select name="button_style">${[
      ["", "پیش‌فرض"],
      ["primary", "آبی"],
      ["success", "سبز"],
      ["danger", "قرمز"],
    ]
      .map(
        ([k, t]) =>
          `<option value="${k}" ${(v.button_style || "") === k ? "selected" : ""}>${t}</option>`,
      )
      .join(
        "",
      )}</select></div>${field("button_icon", "شناسه ایموجی اختیاری", v.button_icon)}<label class="confirm-label field full"><input type="checkbox" name="enabled" ${v.enabled ? "checked" : ""}>پلن برای خریدهای جدید فعال باشد</label></form><div class="banner warning" style="margin-top:18px;margin-bottom:0">${ico("shield")}تغییر پلن یا توقف فروش، رضایت تمدید خودکار آینده را نیازمند بررسی/تأیید تازه می‌کند.</div>`,
    btn("انصراف", "close-modal") + btn("بررسی تغییرات", "save", "primary"),
  );
  const panelSelect = $("#editor-form").elements.panel_code;
  if (!panelSelect.disabled)
    panelSelect.insertAdjacentHTML(
      "beforebegin",
      '<input id="panel-search" placeholder="جست‌وجوی نام یا شناسه پنل…" aria-label="جست‌وجوی پنل" style="margin-bottom:8px">',
    );
  S.save = () => {
    const f = $("#editor-form"),
      value = {};
    for (const k of ["price", "storage", "duration", "ip_limit", "panel_code"])
      value[k] = Number(f.elements[k].value);
    for (const k of ["plan_type", "data_limit_reset_strategy"])
      value[k] = f.elements[k].value;
    for (const k of ["display_button_text", "button_style", "button_icon"])
      value[k] = f.elements[k].value || null;
    value.enabled = f.elements.enabled.checked;
    return review(
      value,
      (id === "new" || duplicate ? "ساخت" : "ویرایش") + " پلن",
    );
  };
}
async function editWallet(id) {
  if (!S.meta.actor.owner) throw Error("تغییر موجودی فقط برای مالک مجاز است.");
  const v = await loadDoc("wallet", id);
  modal(
    "تغییر موجودی کاربر #" + id,
    `<p class="note">موجودی فعلی: <strong>${fa(v.amount)} تومان</strong></p><label>موجودی جدید (تومان)</label><input id="wallet-amount" type="text" inputmode="numeric" dir="ltr" value="${esc(v.amount)}" maxlength="19"><label>دلیل تغییر (اجباری)</label><textarea id="wallet-reason" maxlength="200" placeholder="مثلاً اصلاح موجودی با استناد به رسید..."></textarea><p class="note">مبلغ جدید جایگزین موجودی فعلی می‌شود. اگر موجودی هنگام تأیید با پیش‌نمایش متفاوت باشد، عملیات متوقف می‌شود. کاهش موجودی هم به‌صورت تراکنش ثبت خواهد شد.</p>`,
    btn("انصراف", "close-modal") +
      btn("پیش‌نمایش تغییر موجودی", "save", "primary"),
  );
  S.save = () => {
    const amount = $("#wallet-amount")
      .value.trim()
      .replace(/[۰-۹]/g, (c) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(c)))
      .replace(/[٠-٩]/g, (c) => String("٠١٢٣٤٥٦٧٨٩".indexOf(c)));
    const reason = $("#wallet-reason").value.trim();
    if (!/^\d{1,19}$/.test(amount) || BigInt(amount) > 9223372036854775807n)
      throw Error("مبلغ صحیح و نامنفی به تومان وارد کنید.");
    if (!reason) throw Error("دلیل تغییر موجودی را بنویسید.");
    return review({ amount }, reason);
  };
}
async function editUser(id) {
  const v = await loadDoc("users", id);
  modal(
    "مدیریت کاربر #" + id,
    `<p class="note">این عملیات سرویس را از پنل حذف نمی‌کند، موجودی را تغییر نمی‌دهد و پرداخت ناتمام را بازپرداخت نمی‌کند. کاربر مسدود نمی‌تواند برداشت خودکار جدید داشته باشد.</p><div class="divider"></div><label>وضعیت دسترسی ربات</label><select id="user-status"><option value="">فعال</option><option value="ban" ${v.status === "ban" ? "selected" : ""}>مسدود</option></select>`,
    btn("انصراف", "close-modal") + btn("بررسی تغییرات", "save", "primary"),
  );
  S.save = () =>
    review(
      { status: $("#user-status").value || null },
      "تغییر وضعیت کاربر #" + id,
    );
}
async function editService(id) {
  const v = await loadDoc("services", id);
  modal(
    "تمدید خودکار سرویس #" + id,
    `<p><strong>${esc(v.username)}</strong> · ${badge(v.state)}</p><div class="divider"></div><p class="note">فقط برداشت‌های آینده متوقف می‌شوند. درخواست قبلاً پرداخت‌شده باقی می‌ماند و قابل پیگیری است. اینجا فعال‌سازی به‌جای مشتری، حذف، تمدید اجباری یا تغییر سهمیه انجام نمی‌شود.</p>`,
    btn("بستن", "close-modal") +
      btn("بررسی توقف برداشت آینده", "save", "danger"),
  );
  S.save = () => review({ state: "off" }, "توقف تمدید آینده سرویس #" + id);
}
async function editGrant(id) {
  if (!id) {
    modal(
      "افزودن ادمین",
      `<label>شناسه عددی تلگرام</label><input id="grant-id" type="number" placeholder="مثلاً 123456789"><p class="note">کاربر باید قبلاً ربات را /start کرده باشد. شناسه مالک از اینجا قابل تغییر نیست.</p>`,
      btn("ادامه", "grant-next", "primary"),
    );
    return;
  }
  const v = await loadDoc("grants", id);
  modal(
    "دسترسی ادمین #" + id,
    `<form id="editor-form"><label>نام نقش</label><input name="name" value="${esc(v.name)}" maxlength="60"><div class="divider"></div><div class="permissions">${Object.entries(
      S.meta.permissions,
    )
      .map(
        ([k, t]) =>
          `<div class="permission"><input id="perm-${k}" type="checkbox" name="permissions" value="${k}" ${v.permissions.includes(k) ? "checked" : ""}><label for="perm-${k}">${esc(t)}</label></div>`,
      )
      .join(
        "",
      )}</div></form><p class="note" style="margin-top:15px">برای لغو کامل دسترسی، همه مجوزها را بردارید. کنترل مجوز در هر درخواست دوباره انجام می‌شود.</p>`,
    btn("انصراف", "close-modal") + btn("بررسی دسترسی‌ها", "save", "primary"),
  );
  S.save = () =>
    review(
      {
        name: $("#editor-form").elements.name.value,
        permissions: [
          ...$("#editor-form").querySelectorAll("input:checked"),
        ].map((el) => el.value),
      },
      "تغییر مجوز ادمین #" + id,
    );
}
async function openTicket(id) {
  const d = await api("/tickets/" + id);
  let v = { status: d.ticket.status };
  if (can("tickets.manage")) v = await loadDoc("tickets", id);
  S.threadId = id;
  S.nextBefore = d.next_before;
  modal(
    "تیکت #" + id + " · کاربر #" + d.ticket.user_id,
    `${d.next_before ? btn("پیام‌های قدیمی‌تر", "older-messages", "show-old") : ""}<div class="thread" id="thread">${d.messages.map(message).join("") || '<p class="note">گفت‌وگو خالی است.</p>'}</div>${can("tickets.manage") ? `<div class="divider"></div><form id="editor-form"><label>پاسخ پشتیبانی</label><textarea name="reply" maxlength="3000" placeholder="پاسخ شما در همین گفت‌وگوی تیکت ثبت می‌شود…"></textarea><div class="form-grid"><div><label>وضعیت پس از ثبت</label><select name="status">${["open", "in_progress", "waiting", "closed"].map((k) => `<option value="${k}" ${v.status === k ? "selected" : ""}>${statusLabels[k]}</option>`).join("")}</select></div><label class="confirm-label"><input type="checkbox" name="assign_me">اختصاص به من</label></div></form>` : ""}<p class="note">پیوست‌ها در ربات قابل مشاهده‌اند؛ این نسخه دانلود فایل یا تضمین اعلان تلگرام ندارد.</p>`,
    btn("بستن", "close-modal") +
      (can("tickets.manage")
        ? btn("بررسی پاسخ / وضعیت", "save", "primary")
        : ""),
  );
  S.save = () => {
    const f = $("#editor-form");
    return review(
      {
        reply: f.elements.reply.value,
        status: f.elements.status.value,
        assign_me: f.elements.assign_me.checked,
      },
      "رسیدگی به تیکت #" + id,
    );
  };
}
function message(m) {
  return `<div class="message ${m.kind === "staff" ? "staff" : ""}">${esc(m.text)}${m.has_attachment ? '<br><span class="badge neutral">پیوست در تلگرام</span>' : ""}<small>${m.kind === "staff" ? "پشتیبانی" : "مشتری"} · ${date(m.created_at)}</small></div>`;
}
async function openAudit(token) {
  const d = await api("/audit/" + token);
  S.auditToken = token;
  if (d.status === "draft" && d.actor_id === S.meta.actor.id) {
    showDraft(d);
    return;
  }
  modal(
    "جزئیات تغییر",
    `<p class="note">${esc(d.reason)} · ${badge(d.status)}</p><div class="diff"><div><label>قبل</label><pre>${esc(JSON.stringify(d.before, null, 2))}</pre></div><div><label>بعد</label><pre>${esc(JSON.stringify(d.after, null, 2))}</pre></div></div><p class="note">بازگردانی فقط یک پیش‌نویس تازه برای تنظیمات قبلی می‌سازد؛ تراکنش یا عملیات مشتری را برنمی‌گرداند.</p>`,
    btn("بستن", "close-modal") +
      (["settings", "buttons", "layout", "texts", "plans"].includes(d.entity) &&
      d.status === "published"
        ? btn("پیش‌نویس بازگردانی", "restore", "primary")
        : ""),
  );
}
function changedLayout() {
  S.layoutReset = false;
  S.dirty = true;
  drawLayout();
}
// Native details remain keyboard-accessible. Keep expansion during route re-renders.
document.addEventListener(
  "toggle",
  (event) => {
    const group = event.target;
    if (!group.matches?.("details[data-nav-group]") || !group.isConnected)
      return;
    if (group.open) S.openGroups.add(group.dataset.navGroup);
    else S.openGroups.delete(group.dataset.navGroup);
  },
  true,
);
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button,[data-nav]");
  if (!b || b.disabled) return;
  try {
    if (b.dataset.nav) return await navigate(b.dataset.nav);
    if (b.dataset.section) {
      if (S.dirty && !confirm("تغییرات ذخیره‌نشده کنار گذاشته شود؟")) return;
      S.dirty = false;
      return await settings(b.dataset.section);
    }
    if (b.dataset.appearance) {
      if (S.dirty && !confirm("تغییرات ذخیره‌نشده کنار گذاشته شود؟")) return;
      S.dirty = false;
      return await appearance(b.dataset.appearance);
    }
    if ("color" in b.dataset) {
      S.buttonStyle = b.dataset.color || null;
      document
        .querySelectorAll(".color-choice")
        .forEach((el) => el.classList.toggle("selected", el === b));
      const p = $("#button-preview");
      p.className = "phone-button " + (S.buttonStyle || "");
      S.dirty = true;
      return;
    }
    if (b.dataset.remove) {
      S.layout = S.layout.map((row) =>
        row.filter((k) => k !== b.dataset.remove),
      );
      changedLayout();
      return;
    }
    if (b.dataset.left) {
      const r = S.layout.find((row) => row.includes(b.dataset.left)),
        i = r.indexOf(b.dataset.left);
      if (r.length > 1)
        [r[i], r[(i + 1) % r.length]] = [r[(i + 1) % r.length], r[i]];
      changedLayout();
      return;
    }
    if (b.dataset.move) {
      const k = b.dataset.move,
        i = S.layout.findIndex((row) => row.includes(k));
      if (i > 0 && S.layout[i - 1].length < 3) {
        S.layout[i] = S.layout[i].filter((x) => x !== k);
        S.layout[i - 1].push(k);
        changedLayout();
      }
      return;
    }
    const a = b.dataset.action;
    if (!a) return;
    if (a === "toggle-nav") return $(".sidebar").classList.toggle("open");
    if (a === "close-modal") {
      $("#dialog").close();
      return;
    }
    if (a === "refresh") return await navigate(S.page);
    if (a.startsWith("goto-")) return await navigate(a.slice(5));
    if (a === "reset-text")
      return await review({ text: null }, "بازنشانی متن پیش‌فرض");
    if (a === "new-plan") return await editPlan();
    if (a === "edit-plan" || a === "duplicate-plan")
      return await editPlan(b.dataset.id, a === "duplicate-plan");
    if (a === "edit-wallet") return await editWallet(b.dataset.id);
    if (a === "edit-user") return await editUser(b.dataset.id);
    if (a === "edit-service") return await editService(b.dataset.id);
    if (a === "new-grant") return await editGrant();
    if (a === "grant-next") return await editGrant($("#grant-id").value);
    if (a === "edit-grant") return await editGrant(b.dataset.id);
    if (a === "open-ticket") return await openTicket(b.dataset.id);
    if (a === "open-audit") return await openAudit(b.dataset.id);
    if (a === "older-messages") {
      const d = await api("/tickets/" + S.threadId + "?before=" + S.nextBefore);
      $("#thread").insertAdjacentHTML(
        "afterbegin",
        d.messages.map(message).join(""),
      );
      S.nextBefore = d.next_before;
      if (!d.next_before) b.remove();
      return;
    }
    if (a === "search") {
      S.q = $("#list-search").value;
      S.listPage = 1;
      return await listPage();
    }
    if (a === "next" || a === "prev") {
      S.listPage += a === "next" ? 1 : -1;
      return await listPage();
    }
    if (a === "add-row") {
      if (S.layout.length >= 20) throw Error("حداکثر ۲۰ ردیف مجاز است.");
      S.layout.push([]);
      changedLayout();
      return;
    }
    if (a === "add-key") {
      const k = $("#add-key").value;
      if (k) {
        let r = S.layout.find((r) => r.length < 3);
        if (!r) {
          r = [];
          S.layout.push(r);
        }
        r.push(k);
        changedLayout();
      }
      return;
    }
    if (a === "reset-layout") {
      S.layout = structuredClone(defaultRows);
      S.layoutReset = true;
      S.dirty = true;
      drawLayout();
      return;
    }
    b.disabled = true;
    if (a === "save") {
      if (!S.save) throw Error("فرم را دوباره باز کنید.");
      await S.save();
    } else if (a === "publish") {
      if (!$("#confirm-change")?.checked)
        throw Error("تأیید تغییرات لازم است.");
      const d = await api("/changes/" + S.draft.token + "/publish", {
        confirm: true,
      });
      $("#dialog").close();
      S.dirty = false;
      toast(
        d.replayed
          ? "این تغییر قبلاً اعمال شده بود."
          : "تغییرات ثبت و منتشر شد.",
      );
      await navigate(S.page, true);
    } else if (a === "discard") {
      await api("/changes/" + S.draft.token + "/discard", { confirm: true });
      $("#dialog").close();
      S.dirty = false;
      await navigate(S.page, true);
      toast("پیش‌نویس لغو شد؛ تنظیمات تغییر نکرد.");
    } else if (a === "restore") {
      const d = await api("/changes/" + S.auditToken + "/restore", {
        confirm: true,
      });
      showDraft(d);
    } else if (a === "logout") {
      await api("/logout", {});
      S.token = null;
      location.reload();
    }
  } catch (err) {
    toast(err.message, true);
  } finally {
    if (b.isConnected) b.disabled = false;
  }
});
document.addEventListener("change", async (e) => {
  try {
    if (e.target.id === "confirm-change") {
      $('[data-action="publish"]').disabled = !e.target.checked;
      return;
    }
    if (e.target.id === "button-key") {
      S.dirty = false;
      await appearance("buttons", e.target.value);
      return;
    }
    if (e.target.id === "text-key") {
      S.dirty = false;
      await textPage(e.target.value);
      return;
    }
    if (e.target.closest("#editor-form")) S.dirty = true;
  } catch (err) {
    toast(err.message, true);
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "panel-search") {
    clearTimeout(S.panelSearchTimer);
    const select = $("#editor-form").elements.panel_code,
      q = e.target.value;
    S.panelSearchTimer = setTimeout(async () => {
      try {
        const d = await api("/list/panels?q=" + encodeURIComponent(q));
        if (select.isConnected)
          select.innerHTML =
            d.items
              .map(
                (p) =>
                  `<option value="${p.code}">${esc(p.name)} #${p.code}</option>`,
              )
              .join("") || '<option value="">پنلی پیدا نشد</option>';
      } catch (err) {
        toast(err.message, true);
      }
    }, 350);
  }

  if (e.target.closest("#editor-form")) S.dirty = true;
  if (e.target.id === "button-text" && $("#button-preview"))
    $("#button-preview").textContent = e.target.value;
});
document.addEventListener("submit", (e) => e.preventDefault());
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.id === "list-search") {
    e.preventDefault();
    $('[data-action="search"]').click();
  }
});
document.addEventListener("dragstart", (e) => {
  const token = e.target.closest("[data-drag-key]");
  if (token) {
    S.drag = token.dataset.dragKey;
    e.dataTransfer.setData("text/plain", S.drag);
    e.dataTransfer.effectAllowed = "move";
  }
});
document.addEventListener("dragover", (e) => {
  const row = e.target.closest("[data-row]");
  if (row) {
    e.preventDefault();
    row.classList.add("over");
  }
});
document.addEventListener("dragleave", (e) =>
  e.target.closest("[data-row]")?.classList.remove("over"),
);
document.addEventListener("drop", (e) => {
  const el = e.target.closest("[data-row]");
  if (!el || !S.drag) return;
  e.preventDefault();
  const i = Number(el.dataset.row),
    k = S.drag;
  S.drag = null;
  if (S.layout[i].length >= 3 && !S.layout[i].includes(k)) {
    toast("حداکثر سه دکمه در هر ردیف.", true);
    return;
  }
  S.layout = S.layout.map((row) => row.filter((x) => x !== k));
  const target = e.target.closest("[data-drag-key]")?.dataset.dragKey,
    j = S.layout[i].indexOf(target);
  S.layout[i].splice(j < 0 ? S.layout[i].length : j, 0, k);
  changedLayout();
});
window.addEventListener("beforeunload", (e) => {
  if (S.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
(async () => {
  try {
    if (window.AdminDemo) {
      S.meta = await api("/me");
    } else {
      const tg = window.Telegram?.WebApp;
      if (!tg?.initData)
        throw Error(
          "برای ورود امن، دستور /adminapp را در گفت‌وگوی خصوصی ربات بفرستید و از دکمه آن وارد شوید.",
        );
      tg.ready();
      tg.expand();
      tg.setHeaderColor?.("#102c2e");
      let login;
      try {
        login = await api("/auth", { init_data: tg.initData });
      } catch (error) {
        if (error.status === 403) {
          // Old /admin links remain useful for customers. Keep Telegram's launch
          // fragment on this same-origin navigation; never put a bearer token in a URL.
          location.replace("/admin/account" + location.hash);
          return;
        }
        throw error;
      }
      S.token = login.token;
      S.meta = await api("/me");
    }
    await navigate("dashboard", true);
  } catch (e) {
    $("#app").innerHTML =
      `<div class="loading"><div class="brand-mark">پ</div><h2>ورود امن به مرکز مدیریت</h2><p style="max-width:440px;text-align:center;line-height:2.5;padding:15px">${esc(e.message)}</p><span class="chip">دسترسی فقط برای ادمین‌های مجاز</span></div>`;
  }
})();
