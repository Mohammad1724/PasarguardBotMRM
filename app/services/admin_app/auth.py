"""Telegram initData -> short-lived bearer session. Never trust initDataUnsafe."""

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl, urlsplit

from fastapi import HTTPException, Request

from app.db.base import AsyncSessionLocal as Session
from app.db.models.admin_app import AdminGrant
from app.db.models.user import User
from app.db.redis import get_redis
from config import ADMIN_ID, ADMIN_MINI_APP_URL, BOT_TOKEN

PERMISSIONS = {
    "settings.manage": "تنظیمات عمومی",
    "payments.manage": "تنظیمات پرداخت",
    "appearance.manage": "منو و متن‌ها",
    "plans.manage": "مدیریت پلن‌ها",
    "users.view": "مشاهده کاربران",
    "users.manage": "مسدودسازی کاربران",
    "services.view": "مشاهده سرویس‌ها",
    "services.manage": "توقف تمدید خودکار",
    "tickets.view": "مشاهده تیکت‌ها",
    "tickets.manage": "پاسخ و وضعیت تیکت",
    "finance.view": "گزارش مالی",
    "audit.view": "تاریخچه تغییرات",
}
PREFIX = "admin-app:" + hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:20] + ":"


def fail(status, message):
    raise HTTPException(status, message)


def origin():
    u = urlsplit(ADMIN_MINI_APP_URL)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.path != "/admin" or u.query or u.fragment:
        fail(503, "مینی‌اپ غیرفعال است؛ آدرس HTTPS با مسیر /admin را در سرور تنظیم کنید.")
    try:
        host = u.hostname.encode("idna").decode().lower()
        if ":" in host:
            host = "[" + host + "]"
        port = u.port
    except ValueError, UnicodeError:
        fail(503, "آدرس مینی‌اپ نامعتبر است.")
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def validate_init_data(raw, now=None):
    if not isinstance(raw, str) or len(raw) > 8192:
        fail(401, "ورود نامعتبر")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError()
        supplied = data.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError()
        now = int(time.time()) if now is None else now
        date = int(data["auth_date"])
        if date > now + 30 or now - date > 300:
            raise ValueError()
        user = json.loads(data["user"])
        uid = user["id"]
        if type(uid) is not int or not 0 < uid < 2**52 or user.get("is_bot"):
            raise ValueError()
        return uid, supplied
    except ValueError, KeyError, TypeError:
        fail(401, "ورود منقضی یا نامعتبر؛ مینی‌اپ را از ربات دوباره باز کنید.")


async def identity(session, uid, *, lock=False):
    user = await session.get(User, uid, with_for_update=lock)
    if not user or user.status in ("ban", "BlockedBot", "DeleteAccount"):
        fail(403, "حساب در دسترس نیست.")
    if uid in ADMIN_ID:
        return {"id": uid, "name": "مالک ربات", "owner": True, "permissions": list(PERMISSIONS), "access_version": 0}
    grant = await session.get(AdminGrant, uid, with_for_update=lock)
    if not grant or not grant.permissions:
        fail(403, "دسترسی مدیریتی ندارید.")
    return {
        "id": uid,
        "name": grant.name,
        "owner": False,
        "access_version": grant.revision,
        "permissions": [p for p in grant.permissions if p in PERMISSIONS],
    }


def require(actor, permission):
    if not actor["owner"] and permission not in actor["permissions"]:
        fail(403, "مجوز این بخش را ندارید.")


async def redis_required():
    redis = await get_redis()
    if redis is None:
        fail(503, "احراز هویت موقتاً در دسترس نیست.")
    return redis


async def rate(redis, key, limit):
    count = await redis.eval(
        "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
        1,
        PREFIX + key,
    )
    if count > limit:
        fail(429, "درخواست‌ها زیاد است؛ یک دقیقه صبر کنید.")


async def exchange(request, raw):
    if request.headers.get("origin") != origin():
        fail(403, "مبدأ درخواست نامعتبر است.")
    redis = await redis_required()
    await rate(
        redis,
        "login:" + hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest(),
        30,
    )
    uid, signature = validate_init_data(raw)
    async with Session() as session:
        actor = await identity(session, uid)
    if not await redis.set(PREFIX + "used:" + signature, "1", nx=True, ex=360):
        fail(401, "این ورود قبلاً استفاده شده؛ مینی‌اپ را ببندید و دوباره باز کنید.")
    token = secrets.token_urlsafe(32)
    await redis.set(
        PREFIX + "session:" + hashlib.sha256(token.encode()).hexdigest(),
        json.dumps({"uid": uid, "revision": actor["access_version"], "owner": actor["owner"]}),
        ex=1800,
    )
    return {"token": token, "expires_in": 1800, "actor": actor}


async def authenticate(request: Request):
    allowed = origin()
    if request.method not in ("GET", "HEAD") and request.headers.get("origin") != allowed:
        fail(403, "مبدأ درخواست نامعتبر است.")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 100:
        fail(401, "مینی‌اپ را از تلگرام باز کنید.")
    token = authorization[7:]
    redis = await redis_required()
    key = PREFIX + "session:" + hashlib.sha256(token.encode()).hexdigest()
    uid = await redis.get(key)
    if uid is None:
        fail(401, "نشست منقضی شده؛ دوباره از ربات وارد شوید.")
    try:
        stored = json.loads(uid)
        user_id = int(stored["uid"])
        revision = stored["revision"]
    except ValueError, TypeError, KeyError:
        fail(401, "نشست نامعتبر؛ دوباره از ربات وارد شوید.")
    await rate(redis, "requests:" + str(user_id), 180)
    async with Session() as session:
        actor = await identity(session, user_id)
    if actor.get("access_version", 0) != revision or actor["owner"] is not stored.get("owner"):
        await redis.delete(key)
        fail(401, "مجوزهای نشست تغییر کرده؛ مینی‌اپ را از ربات دوباره باز کنید.")
    request.state.admin_session_key = key
    return actor
