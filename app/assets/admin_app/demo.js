/* Isolated in-memory demonstration. Not served by the production router. */
window.AdminDemo = {
  async request(path, body) {
    await new Promise((r) => setTimeout(r, 80));
    if (!this.meta) {
      this.meta = await (await fetch("/preview-meta.json")).json();
      this.init();
    }
    return structuredClone(this.dispatch(path, body));
  },
  init() {
    const now = Math.floor(Date.now() / 1000);
    this.now = now;
    this.docs = {
      settings: structuredClone(this.meta.sections),
      buttons: Object.fromEntries(
        this.meta.buttons.map((b) => [
          b.key,
          {
            text: b.default,
            style:
              b.key === "bt.menu_buy_service"
                ? "success"
                : b.key === "bt.menu_my_services"
                  ? "primary"
                  : null,
            icon: null,
          },
        ]),
      ),
      texts: Object.fromEntries(
        Object.keys(this.meta.texts).map((k) => [
          k,
          {
            text:
              k === "start_message"
                ? "به پاسارگارد خوش آمدید 🌿\nبرای شروع، یکی از گزینه‌های منو را انتخاب کنید."
                : "متن نمونه این بخش؛ در ربات واقعی، متن ثبت‌شده شما نمایش داده می‌شود.",
          },
        ]),
      ),
      layout: { home: { rows: [] } },
      plans: {},
      users: {},
      services: {},
      tickets: {},
      grants: {},
    };
    this.docs.settings.core_settings.sale_mode = true;
    this.docs.settings.core_settings.cx_tickets_enabled = true;
    this.docs.settings.purchase_settings.tamdid_mode = true;
    this.lists = {
      plans: [
        ["ماهانه اقتصادی", 30, 30, 149000],
        ["ماهانه حرفه‌ای", 60, 30, 249000],
        ["دوماهه استاندارد", 100, 60, 429000],
        ["سه‌ماهه ویژه", 180, 90, 689000],
        ["پلن خانواده", 250, 90, 899000],
        ["پلن آزمایشی تیم", 10, 7, 49000],
      ].map(([name, storage, duration, price], i) => ({
        id: i + 1,
        display_button_text: name,
        storage,
        duration,
        price,
        panel_code: (i % 2) + 10,
        enabled: i !== 5,
        plan_type: "volume",
        data_limit_reset_strategy: "no_reset",
        ip_limit: i === 4 ? 4 : 2,
        button_style: null,
        button_icon: null,
      })),
      panels: [
        { code: 10, name: "آلمان • Frankfurt", enable: true },
        { code: 11, name: "فنلاند • Helsinki", enable: true },
      ],
      users: Array.from({ length: 8 }, (_, i) => ({
        id: 102034201 + i,
        status: i === 5 ? "ban" : null,
        time_s: now - i * 86400,
        amount: Math.max(0, 1250000 - i * 120000),
        tested: true,
      })),
      services: Array.from({ length: 7 }, (_, i) => ({
        code: 84210 + i,
        username: "pasarguard_" + (210 + i),
        id: 102034201 + i,
        in_panel: 10,
        enable: i !== 4,
        is_test: i === 6,
        expiration_time: now + (18 - i * 3) * 86400,
      })),
      tickets: ["connection", "payment", "renewal", "other"].map(
        (topic, i) => ({
          id: 481 + i,
          user_id: 102034201 + i,
          topic,
          status: ["open", "in_progress", "waiting", "closed"][i],
          assigned_to: i ? 1 : null,
          updated_at: now - i * 1200,
        }),
      ),
      finance: [149000, 249000, 429000, 689000, 149000, 899000].map(
        (amount, i) => ({
          id: 7210 + i,
          user_id: 102034201 + i,
          amount,
          method: ["manual", "zarinpal", "stars"][i % 3],
          status: ["completed", "completed", "pending"][i % 3],
          created_at: now - i * 6300,
        }),
      ),
      renewals: [
        {
          token: "a31b208239049102a891",
          user_id: 102034202,
          service_code: 84211,
          price: 149000,
          status: "applied",
          month: "2026-09",
          created_at: now - 3000,
        },
        {
          token: "b41004949181004493001",
          user_id: 102034203,
          service_code: 84212,
          price: 249000,
          status: "review",
          month: "2026-09",
          created_at: now - 5000,
        },
      ],
      grants: [
        {
          user_id: 102034205,
          name: "پشتیبانی",
          permissions: ["tickets.view", "tickets.manage", "users.view"],
          updated_at: now - 80000,
        },
        {
          user_id: 102034206,
          name: "مدیر فروش",
          permissions: ["plans.manage", "finance.view"],
          updated_at: now - 160000,
        },
      ],
      audit: [],
    };
    for (const e of ["plans", "users", "services", "tickets", "grants"])
      for (const r of this.lists[e]) {
        const id =
          e === "services" ? r.code : e === "grants" ? r.user_id : r.id;
        this.docs[e][id] =
          e === "plans"
            ? { ...r }
            : e === "users"
              ? { status: r.status }
              : e === "services"
                ? {
                    state: r.code % 2 ? "enabled" : "off",
                    revision: 1,
                    user_id: r.id,
                    username: r.username,
                    panel_code: r.in_panel,
                    panel_userid: 200 + r.code,
                  }
                : e === "tickets"
                  ? {
                      status: r.status,
                      assigned_to: r.assigned_to,
                      last_message: 2,
                    }
                  : { name: r.name, permissions: r.permissions };
      }
    this.threads = Object.fromEntries(
      this.lists.tickets.map((r) => [
        r.id,
        [
          {
            id: 1,
            kind: "customer",
            text: "سلام، برای اتصال به سرویس روی گوشی جدید راهنمایی می‌خواستم. ممنون می‌شم بررسی کنید.",
            created_at: now - 3600,
            has_attachment: false,
          },
          {
            id: 2,
            kind: "staff",
            text: "سلام، خوشحال می‌شیم کمک کنیم. لطفاً نام برنامه و تصویر خطا را در همین تیکت بفرستید.",
            created_at: now - 1800,
            has_attachment: false,
          },
        ],
      ]),
    );
    this.changes = {};
    [
      "تغییر قیمت پلن ماهانه",
      "به‌روزرسانی منوی اصلی",
      "فعال‌سازی پشتیبانی",
      "تغییر دسترسی تیم",
    ].forEach((reason, i) => {
      const token = "preview-change-" + i,
        entity = ["plans", "layout", "settings", "grants"][i],
        target = ["1", "home", "core_settings", "102034205"][i],
        before = structuredClone(this.docs[entity][target]),
        after = structuredClone(before);
      this.lists.audit.push({
        token,
        actor_id: 1,
        entity,
        target,
        reason,
        status: "published",
        created_at: now - 600 - i * 1800,
        published_at: now - 500 - i * 1800,
      });
      this.changes[token] = {
        token,
        entity,
        target,
        before,
        after,
        reason,
        status: "published",
      };
    });
  },
  version(v) {
    return JSON.stringify(v);
  },
  dispatch(path, body) {
    const u = new URL(path, "https://demo.invalid"),
      parts = u.pathname.split("/").filter(Boolean),
      [root, a, b] = parts;
    if (root === "me") return this.meta;
    if (root === "logout") return { logged_out: true };
    if (root === "dashboard")
      return {
        server_time: this.now,
        counts: {
          users: 1284,
          services: 962,
          wallet_liability: 18450000,
          tickets: this.lists.tickets.filter((t) => t.status !== "closed")
            .length,
          plans: this.lists.plans.length,
        },
        activity: [18, 27, 22, 39, 32, 51, 46],
      };
    if (root === "document") {
      if (
        a === "grants" &&
        !this.docs.grants[b] &&
        this.lists.users.some((r) => r.id == b)
      )
        this.docs.grants[b] = { name: "ادمین", permissions: [] };
      const value = a === "plans" && b === "new" ? {} : this.docs[a]?.[b];
      if (!value) throw Error("مورد آزمایشی پیدا نشد.");
      return { value, version: this.version(value) };
    }
    if (root === "list") {
      let rows = [...(this.lists[a] || [])],
        q = u.searchParams.get("q") || "",
        page = Number(u.searchParams.get("page") || 1);
      if (q)
        rows = rows.filter((r) =>
          [r.id, r.code, r.user_id, r.username, r.name, r.token].some((v) =>
            String(v ?? "").includes(q),
          ),
        );
      return {
        items: rows.slice((page - 1) * 25, page * 25),
        total: rows.length,
        page,
        page_size: 25,
      };
    }
    if (root === "tickets") {
      return {
        ticket: this.lists.tickets.find((r) => r.id == a),
        messages: this.threads[a] || [],
        next_before: null,
      };
    }
    if (root === "audit") {
      const d = this.changes[a];
      if (!d) throw Error("تغییر پیدا نشد.");
      return d;
    }
    if (root === "changes" && !a) {
      const before =
        body.entity === "plans" && body.target === "new"
          ? {}
          : this.docs[body.entity][body.target];
      if (this.version(before) !== body.version)
        throw Error("اطلاعات تغییر کرده است؛ دوباره صفحه را باز کنید.");
      const token = crypto.randomUUID().replaceAll("-", ""),
        after = { ...before, ...body.value };
      if (body.entity === "plans") delete after.id;
      const d = {
        ...body,
        token,
        before: structuredClone(before),
        after,
        status: "draft",
        created_at: this.now,
        actor_id: 1,
        warning:
          "پیش‌نمایش آزمایشی — انتشار فقط داده همین صفحه را تغییر می‌دهد.",
      };
      this.changes[token] = d;
      return d;
    }
    if (root === "changes" && a) {
      const d = this.changes[a];
      if (!d) throw Error("پیش‌نویس پیدا نشد.");
      if (b === "discard") {
        d.status = "cancelled";
        return { cancelled: true };
      }
      if (b === "restore") {
        return this.dispatch("/changes", {
          entity: d.entity,
          target: d.target,
          value: d.before,
          version: this.version(this.docs[d.entity][d.target]),
          reason: "بازگردانی نسخه آزمایشی",
        });
      }
      if (b === "publish") {
        if (d.status === "published")
          return { replayed: true, result: { saved: true } };
        if (d.status !== "draft") throw Error("پیش‌نویس لغو شده است.");
        let target = d.target;
        if (d.entity === "plans" && target === "new") {
          target = String(Math.max(...this.lists.plans.map((p) => p.id)) + 1);
          d.after.id = Number(target);
          this.lists.plans.unshift({ ...d.after });
        } else if (this.lists[d.entity]) {
          const row = this.lists[d.entity].find(
            (r) =>
              (d.entity === "services"
                ? r.code
                : d.entity === "grants"
                  ? r.user_id
                  : r.id) == target,
          );
          if (row) Object.assign(row, d.after);
          else if (d.entity === "grants")
            this.lists.grants.push({
              user_id: Number(target),
              ...d.after,
              updated_at: this.now,
            });
        }
        this.docs[d.entity][target] = {
          ...this.docs[d.entity][target],
          ...d.after,
        };
        if (d.entity === "tickets" && d.after.reply) {
          this.threads[target].push({
            id: Date.now(),
            kind: "staff",
            text: d.after.reply,
            created_at: this.now,
          });
          this.docs.tickets[target].last_message = Date.now();
        }
        d.status = "published";
        d.published_at = this.now;
        this.lists.audit.unshift({ ...d });
        return { result: { saved: true }, replayed: false };
      }
      throw Error("عملیات در این پیش‌نمایش پشتیبانی نشده است.");
    }
  },
};
