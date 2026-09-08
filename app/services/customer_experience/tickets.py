"""Ticket access is enforced inside each operation, not only on Telegram buttons."""

import time

from sqlalchemy import func, select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.customer_experience import SupportTicket, TicketMessage
from app.services.customer_experience.common import owned_service, require_user, settings, staff_ids

TOPICS = {"connection": "اتصال", "payment": "پرداخت", "renewal": "تمدید", "other": "سایر"}
STATUSES = {"open": "باز", "in_progress": "در حال بررسی", "waiting": "منتظر مشتری", "closed": "بسته"}
MAX_FILE_BYTES = 10 * 1024 * 1024


async def _ticket(session, actor, ticket_id, *, lock=False):
    await require_user(session, actor)
    config = await settings(session)
    query = select(SupportTicket).where(SupportTicket.id == ticket_id)
    row = await session.scalar(query.with_for_update() if lock else query)
    staff = actor in staff_ids(config)
    if not row or (row.user_id != actor and not staff):
        raise ValueError("تیکت پیدا نشد یا دسترسی ندارید.")
    return row, staff


async def _rate_limit(session, user_id, now):
    count = await session.scalar(
        select(func.count())
        .select_from(TicketMessage)
        .where(
            TicketMessage.author_id == user_id,
            TicketMessage.created_at > now - 3600,
            TicketMessage.kind != "system",
        )
    )
    cap = 200 if user_id in staff_ids(await settings(session)) else 20
    if count >= cap:
        raise ValueError("سقف ۲۰ پیام در ساعت برای پشتیبانی پر شده است؛ کمی بعد تلاش کنید.")


def validate_payload(text, file_id):
    if not (text.strip() or file_id) or len(text) > 3000:
        raise ValueError("پیام یا فایل بفرستید؛ متن حداکثر ۳۰۰۰ کاراکتر باشد.")


async def add_message(
    actor,
    text,
    *,
    ticket_id=None,
    service_code=None,
    topic="other",
    file_id=None,
    source_chat_id=None,
    source_message_id=None,
):
    """A first message and its ticket are committed together. Duplicate Telegram updates are harmless."""
    validate_payload(text, file_id)
    now = int(time.time())
    async with Session() as session, session.begin():
        await require_user(session, actor, lock=True)
        config = await settings(session)
        existing = (
            await session.scalar(
                select(TicketMessage).where(
                    TicketMessage.source_chat_id == source_chat_id,
                    TicketMessage.source_message_id == source_message_id,
                )
            )
            if source_chat_id is not None and source_message_id is not None
            else None
        )
        if existing:
            row, _ = await _ticket(session, actor, existing.ticket_id)
            if existing.author_id != actor:
                raise ValueError("پیام نامعتبر است.")
            return row, existing, False
        await _rate_limit(session, actor, now)
        if ticket_id is None:
            if not config.cx_tickets_enabled or not config.support_mode:
                raise ValueError("ثبت تیکت جدید فعلاً غیرفعال است.")
            if topic not in TOPICS:
                raise ValueError("موضوع نامعتبر است.")
            if service_code:
                await owned_service(session, actor, service_code)
            open_count = await session.scalar(
                select(func.count())
                .select_from(SupportTicket)
                .where(
                    SupportTicket.user_id == actor,
                    SupportTicket.status != "closed",
                )
            )
            if open_count >= 3:
                raise ValueError("حداکثر ۳ تیکت باز مجاز است. لطفاً در تیکت قبلی ادامه دهید.")
            row = SupportTicket(
                user_id=actor, service_code=service_code, topic=topic, status="open", created_at=now, updated_at=now
            )
            session.add(row)
            await session.flush()
            staff = False
        else:
            row, staff = await _ticket(session, actor, ticket_id, lock=True)
            # A support operator writing in their own ticket is still the customer.
            staff = staff and row.user_id != actor
            if row.status == "closed":
                raise ValueError("تیکت بسته است؛ ابتدا بازگشایی کنید.")
        msg = TicketMessage(
            ticket_id=row.id,
            author_id=actor,
            kind="staff" if staff else "customer",
            text=text,
            file_id=file_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            created_at=now,
        )
        session.add(msg)
        row.updated_at = now
        row.status = "waiting" if staff else "open"
        if staff and not row.assigned_to:
            row.assigned_to = actor
        await session.flush()
        return row, msg, True


async def list_tickets(actor, *, staff_queue=False, page=0, closed=False):
    page = max(0, min(page, 10000))
    async with Session() as session:
        await require_user(session, actor)
        config = await settings(session)
        query = select(SupportTicket)
        if staff_queue:
            if actor not in staff_ids(config):
                raise ValueError("دسترسی پشتیبانی ندارید.")
            query = query.where(SupportTicket.status == "closed" if closed else SupportTicket.status != "closed")
        else:
            query = query.where(SupportTicket.user_id == actor)
        return list(
            (
                await session.scalars(
                    query.order_by(
                        SupportTicket.updated_at.asc() if staff_queue else SupportTicket.updated_at.desc(),
                        SupportTicket.id.desc(),
                    )
                    .offset(page * 8)
                    .limit(9)
                )
            ).all()
        )


async def history(actor, ticket_id, *, before=0):
    async with Session() as session:
        row, staff = await _ticket(session, actor, ticket_id)
        query = select(TicketMessage).where(TicketMessage.ticket_id == row.id)
        if before:
            query = query.where(TicketMessage.id < before)
        messages = list((await session.scalars(query.order_by(TicketMessage.id.desc()).limit(6))).all())
        return row, staff, messages[:5][::-1], len(messages) > 5


async def attachment(actor, message_id):
    async with Session() as session:
        msg = await session.get(TicketMessage, message_id)
        if not msg:
            raise ValueError("فایل پیدا نشد.")
        await _ticket(session, actor, msg.ticket_id)
        if not msg.file_id:
            raise ValueError("این پیام فایل ندارد.")
        return msg.file_id


async def change_status(actor, ticket_id, action):
    now = int(time.time())
    async with Session() as session, session.begin():
        # Same lock ordering as add_message.
        await require_user(session, actor, lock=True)
        row, staff = await _ticket(session, actor, ticket_id, lock=True)
        old = f"{row.status}/{row.assigned_to}"
        if action == "claim" and staff:
            if row.status == "closed":
                raise ValueError("ابتدا تیکت را بازگشایی کنید.")
            if row.assigned_to not in (None, actor):
                raise ValueError("تیکت مسئول دیگری دارد؛ ابتدا باید آزاد شود.")
            row.assigned_to, row.status = actor, "in_progress"
        elif action == "release" and staff:
            from config import ADMIN_ID

            if row.assigned_to not in (None, actor) and actor not in ADMIN_ID:
                raise ValueError("فقط مسئول تیکت یا مالک می‌تواند آن را آزاد کند.")
            row.assigned_to = None
        elif action == "close":
            row.status = "closed"
        elif action == "reopen":
            count = await session.scalar(
                select(func.count())
                .select_from(SupportTicket)
                .where(
                    SupportTicket.user_id == row.user_id,
                    SupportTicket.status != "closed",
                )
            )
            if row.status == "closed" and count >= 3:
                raise ValueError("ابتدا یکی از تیکت‌های باز را ببندید.")
            row.status = "open"
        else:
            raise ValueError("عملیات مجاز نیست.")
        new = f"{row.status}/{row.assigned_to}"
        if old != new:
            row.updated_at = now
            session.add(
                TicketMessage(
                    ticket_id=row.id, author_id=actor, kind="system", text=f"{action}: {old} → {new}", created_at=now
                )
            )
        return row
