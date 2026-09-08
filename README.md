# PasarguardBotMRM

[![Python](https://img.shields.io/badge/Python-3.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-AGPL--3.0-green)](LICENSE)
[![Telethon](https://img.shields.io/badge/Telethon-1.44+-0088cc?logo=telegram)](https://github.com/LonamiWebs/Telethon)
[![Docs](https://img.shields.io/badge/Docs-GitHub%20Pages-blue?logo=github)](https://amirkenzo.github.io/PasarguardBot/)

فورک توسعه‌یافته‌ی [PasarguardBot](https://github.com/AmirKenzo/PasarguardBot) — ربات فروش وی‌پی‌ان مبتنی بر پنل [پاسارگارد پنل](https://github.com/PasarGuard/panel).

**[مستندات کامل (فارسی)](https://amirkenzo.github.io/PasarguardBot/)**

## 🔧 تغییرات این فورک

- ✅ **درگاه پرداخت آنلاین زرین‌پال (API v4)** — شارژ لحظه‌ای کیف پول با لینک پرداخت امن، تایید خودکار با polling هر ۶۰ ثانیه، دکمه «بررسی پرداخت»، حالت آزمایشی (sandbox)، محدودیت مبلغ و بونوس اختیاری
- ✅ **پرداخت با ستاره تلگرام (XTR)** — فاکتور استارزی با نرخ قابل تنظیم؛ تایید Pre-Checkout و شارژ خودکار کیف پول پس از پرداخت
- ✅ **سیستم زیرمجموعه‌گیری (رفرال)** — لینک اختصاصی `?start=ref_` برای هر کاربر، پاداش درصدی از هر شارژ + پاداش ثابت اولین شارژ، نمایش آمار در پروفایل
- ✅ **کد هدیه** — سه نوع: شارژ کیف پول / روز رایگان / حجم رایگان؛ سقف کلی و سقف هر کاربر، تاریخ انقضا، پنل مدیریت کامل (`🎁 کد هدیه`) و فعال‌سازی با دکمه منوی اصلی
- ✅ **ریستور ایمن بکاپ** — با توقف ربات، بکاپ ایمنی، migration و بازیابی کلیدها از طریق CLI سرور
- ✅ **Zip Slip protection** — امنیت بیشتر هنگام ریستور
- ✅ **Streaming SQL import** — جلوگیری از OOM در بکاپ‌های بزرگ
- ✅ **Atomic .env writes** — جلوگیری از خرابی فایل تنظیمات
- ✅ **رفع timing attack** روی webhook
- ✅ **نصب پایدارتر** — تست خودکار شبکه Docker قبل از نصب + تعمیر خودکار netplan روی Ubuntu
- ✅ **Retry خودکار pull** — عبور از rate limit رجیستری‌ها (۳ بار با فاصله)
- ✅ **اعتبارسنجی برنچ/ایمیج** — قبل از نصب چک می‌شود که برنچ و تگ Docker واقعاً وجود دارند
- ✅ **پشتیبانی ARM64** — ایمیج multi-arch (amd64 + arm64) برای سرورهای ARM
- ✅ **phpMyAdmin فقط localhost** — به‌صورت پیش‌فرض امن (دسترسی از راه دور: `PHPMYADMIN_BIND=0.0.0.0` در .env)
- ✅ **healthcheck تنظیم‌شده MariaDB** — نصب موفق روی سرورهای ضعیف (VPS با رم کم)

## نصب سریع (لینوکس)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Mohammad1724/PasarguardBotMRM/main/scripts/pasarguardbot.sh)
```

بعد از نصب: `pasarguardbot`

### ریستور بکاپ

```bash
# از طریق سرور:
sudo pasarguardbot restore /path/to/backup.zip

# داخل ربات فقط راهنمای ریستور نمایش داده می‌شود؛ ریستور مخرب روی ربات زنده مجاز نیست.
```

## لایسنس

[GNU AGPL-3.0](LICENSE)

## اصلاحات ایمنی و تست

راهنمای تغییرات، تست‌ها و استقرار: [SAFETY_CHANGES.md](SAFETY_CHANGES.md).

```bash
uv sync --frozen
uv run ruff check
uv run ruff format --check
uv run pytest -q
```

برای build کد محلی (به‌جای دانلود ایمیج قبلی):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build bot
```

قبل از ارتقا بکاپ بگیرید. migration جدید `f19c8d42a601` باید قبل از شروع ربات اجرا شود.


## نسخه اول تجربه مشتری (برنچ توسعه)

تیکت متصل به سرویس، پشتیبان با دسترسی محدود، راهنمای اتصال مرحله‌ای، تبدیل تست به پلن حجمی همان پنل از کیف پول، و یک پیگیری اختیاری همراه آمار تبدیل اضافه شده‌اند. گزینه‌ها **پیش‌فرض خاموش** هستند و از `/cx` کنترل می‌شوند. این نسخه تمدید دوره‌ای، Mini App یا انتشار خودکار image نیست.

پیش از فعال‌سازی، [راهنمای نصب، محدودیت‌ها و آزمون واقعی نسخه اول](CUSTOMER_EXPERIENCE_V1.fa.md) را بخوانید. migration جدید `c82a1d9e740b` است. وجود درخواست تبدیل پرداخت‌شده ناتمام، مانع downgrade می‌شود.
