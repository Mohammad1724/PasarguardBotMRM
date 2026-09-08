"""Read-only customer sessions, isolated from all administration sessions/permissions."""

import hashlib
import json
import secrets

from fastapi import Request

from app.db.base import AsyncSessionLocal as Session
from app.db.models.user import User
from app.services.admin_app import auth

CUSTOMER_APP_LABEL = "🖥 مینی‌اپ من"
CUSTOMER_PATH = "/admin/account"
PREFIX = auth.PREFIX + "customer:"


def url():
    # Reuse the configured HTTPS origin/port and existing /admin/ proxy routes.
    return auth.origin() + CUSTOMER_PATH


async def identity(session, uid):
    user = await session.get(User, uid)
    if not user:
        auth.fail(403, "ابتدا در گفت‌وگوی خصوصی ربات /start را بفرستید.")
    if user.status in ("ban", "BlockedBot", "DeleteAccount"):
        auth.fail(403, "حساب شما در دسترس نیست؛ با پشتیبانی تماس بگیرید.")
    return {"id": uid}


async def exchange(request, raw):
    if request.headers.get("origin") != auth.origin():
        auth.fail(403, "مبدأ درخواست نامعتبر است.")
    redis = await auth.redis_required()
    ip = request.client.host if request.client else "unknown"
    await auth.rate(redis, "customer-login:" + hashlib.sha256(ip.encode()).hexdigest(), 30)
    uid, signature = auth.validate_init_data(raw)
    async with Session() as session:
        actor = await identity(session, uid)
    if not await redis.set(PREFIX + "used:" + signature, "1", nx=True, ex=360):
        auth.fail(401, "این ورود قبلاً استفاده شده؛ مینی‌اپ را ببندید و از ربات دوباره باز کنید.")
    token = secrets.token_urlsafe(32)
    await redis.set(PREFIX + "session:" + hashlib.sha256(token.encode()).hexdigest(), json.dumps(actor), ex=1800)
    return {"token": token, "expires_in": 1800, "user_id": str(uid)}


async def authenticate(request: Request):
    allowed = auth.origin()
    if request.method not in ("GET", "HEAD") and request.headers.get("origin") != allowed:
        auth.fail(403, "مبدأ درخواست نامعتبر است.")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or not 20 <= len(authorization) <= 100:
        auth.fail(401, "مینی‌اپ من را از دکمه ربات باز کنید.")
    redis = await auth.redis_required()
    key = PREFIX + "session:" + hashlib.sha256(authorization[7:].encode()).hexdigest()
    stored = await redis.get(key)
    if stored is None:
        auth.fail(401, "نشست تمام شده؛ مینی‌اپ را از ربات دوباره باز کنید.")
    try:
        uid = json.loads(stored)["id"]
        if type(uid) is not int or not 0 < uid < 2**52:
            raise ValueError()
    except ValueError, KeyError, TypeError:
        auth.fail(401, "نشست نامعتبر است؛ دوباره وارد شوید.")
    await auth.rate(redis, "customer-requests:" + str(uid), 120)
    async with Session() as session:
        actor = await identity(session, uid)
    request.state.customer_session_key = key
    return actor
