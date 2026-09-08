"""Text templates for user profile."""

from app.utils.formatting.dates import Time_Date


def discount_code_text(discount_code) -> str:
    expiration = Time_Date(discount_code.expiration_date)
    usage_line = ""
    if not discount_code.is_public:
        usage_line = f"**🔢 تعداد استفاده:** `{discount_code.times_used}`**/**`{discount_code.usage_limit}`\n"
    return (
        "\n"
        f"**📌 کدتخفیف:** `{discount_code.code}`\n"
        f"**💸 درصد تخفیف:** `{discount_code.discount_percentage}%`\n"
        f"{usage_line}"
        f"**📋 نوع کد:** {'`🌍 عمومی 🌍`' if discount_code.is_public else '`💎 پرایوت 💎`'}\n"
        f"**⏳ تاریخ انقضا:** `{expiration['jf']} ({expiration['remaining_days']})`\n"
    )


def profile_message(
    user_id: int,
    info,
    date_label: str,
    discount_status: str,
) -> str:
    return (
        f"**👤 شناسه کاربری:** `{user_id}`\n"
        f"**👥 تعداد زیرمجموعه ها:**  `{info.invite:,}`\n"
        f"**💰 موجودی:** `{info.amount:,.0f}` تومان\n"
        f"**📞 شماره تلفن:** `{info.number}`\n"
        f"**🕒 تاریخ عضویت:** `{date_label}`\n"
        f"{discount_status}\n"
    )


def referral_section_text(link: str, invite_count: int, earned: int, percent: int, first_bonus: int) -> str:
    rules = []
    if percent > 0:
        rules.append(f"📈 سهم شما از هر شارژ: **{percent}%**")
    if first_bonus > 0:
        rules.append(f"🎉 پاداش اولین شارژ هر کاربر: **{first_bonus:,} تومان**")
    rules_text = ("\n".join(rules)) if rules else "مقادیر پاداش توسط مدیریت تنظیم می‌شود."
    return (
        "\n**👥 زیرمجموعه‌گیری**\n"
        f"🔗 **لینک اختصاصی شما:**\n`{link}`\n\n"
        f"👥 **اعضای معرفی‌شده:** `{invite_count:,}`\n"
        f"💰 **کل پاداش دریافتی:** `{earned:,}` تومان\n\n"
        f"{rules_text}\n"
        "با فرستادن لینک بالا برای دوستانتان، به‌ازای هر شارژ موفق پاداش می‌گیرید."
    )
