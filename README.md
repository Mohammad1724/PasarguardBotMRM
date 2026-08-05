# PasarguardBotMRM

[![Python](https://img.shields.io/badge/Python-3.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-AGPL--3.0-green)](LICENSE)
[![Telethon](https://img.shields.io/badge/Telethon-1.44+-0088cc?logo=telegram)](https://github.com/LonamiWebs/Telethon)
[![Docs](https://img.shields.io/badge/Docs-GitHub%20Pages-blue?logo=github)](https://amirkenzo.github.io/PasarguardBot/)

فورک توسعه‌یافته‌ی [PasarguardBot](https://github.com/AmirKenzo/PasarguardBot) — ربات فروش وی‌پی‌ان مبتنی بر پنل [پاسارگارد پنل](https://github.com/PasarGuard/panel).

**[مستندات کامل (فارسی)](https://amirkenzo.github.io/PasarguardBot/)**

## 🔧 تغییرات این فورک

- ✅ **ریستور آسان بکاپ** — از داخل ربات تلگرام یا دستور CLI روی سرور
- ✅ **Zip Slip protection** — امنیت بیشتر هنگام ریستور
- ✅ **Streaming SQL import** — جلوگیری از OOM در بکاپ‌های بزرگ
- ✅ **Atomic .env writes** — جلوگیری از خرابی فایل تنظیمات
- ✅ **رفع timing attack** روی webhook

## نصب سریع (لینوکس)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Mohammad1724/PasarguardBotMRM/main/scripts/pasarguardbot.sh)
```

بعد از نصب: `pasarguardbot`

### ریستور بکاپ

```bash
# از طریق سرور:
sudo pasarguardbot restore /path/to/backup.zip

# از داخل ربات:
# پنل مدیریت → 📦 بکاپ ربات → 📥 ریستور از فایل
```

## لایسنس

[GNU AGPL-3.0](LICENSE)
