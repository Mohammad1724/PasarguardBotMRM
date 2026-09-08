"use strict";
const root = document.querySelector("#app");
const S = { token: null, page: 1, seq: 0, hidden: false, busy: false };
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fa = (v) =>
  new Intl.NumberFormat("fa-IR").format(
    typeof v === "string" && /^-?\d+$/.test(v) ? BigInt(v) : (v ?? 0),
  );
function date(v) {
  if (!v) return "بدون تاریخ انقضا";
  const d = new Date(Number(v) * 1000);
  return Number.isNaN(d.getTime())
    ? "ثبت نشده"
    : new Intl.DateTimeFormat("fa-IR", {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "Asia/Tehran",
      }).format(d);
}
function volume(v) {
  return v === null
    ? "ثبت نشده"
    : v === "0"
      ? "نامحدود"
      : fa(Math.round((Number(v) / 1073741824) * 10) / 10) + " گیگابایت";
}
function status(r) {
  if (r.expires_at && Number(r.expires_at) <= Date.now() / 1000)
    return ["منقضی‌شده", "off"];
  return r.enabled ? ["فعال", ""] : ["غیرفعال", "off"];
}
function notice(message) {
  const el = document.querySelector("#notice");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(S.timer);
  S.timer = setTimeout(() => {
    el.hidden = true;
  }, 5000);
}
function locked(message) {
  S.seq++;
  S.token = null;
  S.me = null;
  S.list = null;
  document.querySelector("#details").close();
  root.innerHTML = `<section class="loading"><div class="mark">پ</div><h1>حساب شخصی شما</h1><p>${esc(message)}</p><button class="button primary" data-action="close">بازگشت به ربات</button><p>در گفت‌وگوی خصوصی ربات، «🖥 مینی‌اپ من» یا /miniapp را انتخاب کنید.</p></section>`;
}
async function api(path, body) {
  const r = await fetch("/admin/account/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      ...(S.token ? { Authorization: "Bearer " + S.token } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "omit",
    cache: "no-store",
  });
  let d;
  try {
    d = await r.json();
  } catch {
    throw Error("پاسخ سرور معتبر نیست؛ کمی بعد دوباره امتحان کنید.");
  }
  if (!r.ok) {
    const message =
      typeof d.detail === "string" ? d.detail : "درخواست انجام نشد.";
    if (r.status === 401 || r.status === 403) locked(message);
    throw Error(message);
  }
  return d;
}
function card(r) {
  const [label, kind] = status(r);
  return `<article class="service"><div class="service-top"><div><h3 dir="auto">${esc(r.username)}</h3><div class="code">شناسه سرویس: <span dir="ltr">${esc(r.code)}</span></div></div><span class="badge ${kind}">${label}</span></div><div class="kv"><div><span>حجم بسته</span><strong>${volume(r.package_bytes)}</strong></div><div><span>تاریخ انقضا</span><strong>${date(r.expires_at)}</strong></div></div><div class="service-bottom"><span class="muted">${r.is_test ? "سرویس آزمایشی" : "اشتراک شما"}</span><button class="button" data-action="details" data-code="${esc(r.code)}">جزئیات سرویس ←</button></div></article>`;
}
function render() {
  const m = S.me,
    list = S.list;
  root.innerHTML = `<header class="header"><div class="brand"><div class="mark">پ</div><div><strong>حساب من</strong><small>داشبورد شخصی سرویس‌ها</small></div></div><div class="actions"><button class="button" data-action="refresh">↻ به‌روزرسانی</button><button class="button" data-action="logout">خروج</button></div></header><section class="greeting"><div class="eyebrow">یک فضای ساده، فقط برای شما</div><h1>همه سرویس‌ها، در یک نگاه.</h1><p>موجودی کیف پول و اطلاعات اشتراک‌های خودتان را ببینید.</p></section><section class="overview"><article class="wallet"><div class="wallet-top"><span>موجودی کیف پول</span><button class="eye" data-action="privacy" aria-label="نمایش یا پنهان کردن موجودی">${S.hidden ? "نمایش" : "پنهان"}</button></div><div class="balance">${S.hidden ? "••••••" : fa(m.balance)}<small>تومان</small></div><div class="wallet-foot"><span>شناسه شما: <b dir="ltr">${esc(m.user_id)}</b></span><span>کیف پول شخصی</span></div></article><div class="stats"><article class="stat"><span class="muted">همه سرویس‌ها</span><strong>${fa(m.services_count)}</strong><small>اشتراک ثبت‌شده برای شما</small></article><article class="stat"><span class="muted"><i class="dot"></i>فعال ثبت‌شده</span><strong>${fa(m.active_count)}</strong><small>بر اساس اطلاعات ربات</small></article></div></section><div class="section-head"><h2>سرویس‌های من</h2><span class="chip">دسترسی خصوصی</span></div><section class="services">${list.items.length ? list.items.map(card).join("") : '<div class="empty">هنوز سرویسی برای نمایش در این صفحه ندارید.<br>برای خرید یا دریافت راهنمایی به ربات برگردید.</div>'}</section>${list.total > 25 ? `<nav class="pagination"><button class="button" data-action="prev" ${S.page === 1 ? "disabled" : ""}>قبلی</button><span>صفحه ${fa(S.page)}</span><button class="button" data-action="next" ${S.page * 25 >= list.total ? "disabled" : ""}>بعدی</button></nav>` : ""}<aside class="info"><span>ⓘ</span><span>اطلاعات سرویس‌ها از آخرین داده ثبت‌شده ربات خوانده می‌شود؛ حجم نمایش‌داده‌شده، حجم بسته است نه مصرف یا مانده لحظه‌ای پنل. خرید، شارژ کیف پول و کد هدیه فعلاً از داخل ربات انجام می‌شوند.</span></aside><footer class="footer">فقط سرویس‌ها و موجودی حساب خودتان نمایش داده می‌شود.<br>برای تازه‌کردن اطلاعات از «به‌روزرسانی» استفاده کنید.</footer>`;
}
async function refresh(page = S.page) {
  const seq = ++S.seq;
  const [me, list] = await Promise.all([
    api("/me"),
    api("/services?page=" + page),
  ]);
  if (seq !== S.seq || !S.token) return;
  S.page = page;
  S.me = me;
  S.list = list;
  render();
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  if (action === "close") {
    window.Telegram?.WebApp?.close();
    return;
  }
  if (S.busy) return;
  if (action === "privacy") {
    S.hidden = !S.hidden;
    if (S.me) render();
    return;
  }
  S.busy = true;
  button.disabled = true;
  try {
    if (action === "logout") {
      const logout = api("/logout", {});
      locked(
        "اطلاعات این صفحه پاک شد. برای ورود دوباره، مینی‌اپ را از ربات باز کنید.",
      );
      await logout;
    } else if (action === "details") {
      const seq = S.seq,
        r = await api("/services/" + encodeURIComponent(button.dataset.code));
      if (seq !== S.seq || !S.token) return;
      const pairs = [
        ["شناسه", r.code],
        ["وضعیت ثبت‌شده", status(r)[0]],
        ["حجم بسته", volume(r.package_bytes)],
        ["انقضا", date(r.expires_at)],
        ["نوع", r.is_test ? "آزمایشی" : "اشتراک"],
      ];
      document.querySelector("#detail-content").innerHTML =
        `<h2>${esc(r.username)}</h2>${pairs.map(([k, v]) => `<div class="detail-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}<p class="muted">این صفحه فقط اطلاعات ثبت‌شده سرویس را نمایش می‌دهد.</p>`;
      document.querySelector("#details").showModal();
    } else {
      const page =
        action === "next"
          ? S.page + 1
          : action === "prev"
            ? Math.max(1, S.page - 1)
            : S.page;
      await refresh(page);
    }
  } catch (error) {
    notice(error.message);
  } finally {
    S.busy = false;
    button.disabled = false;
  }
});
document
  .querySelector("#close-detail")
  .addEventListener("click", () => document.querySelector("#details").close());
(async () => {
  try {
    const tg = window.Telegram?.WebApp;
    if (!tg?.initData) {
      locked(
        "برای ورود امن از دکمه «مینی‌اپ من» در ربات استفاده کنید؛ لینک مرورگر به‌تنهایی ورود ایجاد نمی‌کند.",
      );
      return;
    }
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.("#102c2e");
    const login = await api("/auth", { init_data: tg.initData });
    S.token = login.token;
    await refresh();
  } catch (error) {
    locked(error.message);
  }
})();
