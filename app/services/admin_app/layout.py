"""Reorder only buttons the existing builder has already authorized for this user."""

from sqlalchemy import select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.admin_app import AdminState
from app.db.models.keyboards import KeyboardButton
from app.telegram.keyboards.registry import KEYBOARD_BUTTON_DEFAULTS


async def arrange_home(rows):
    async with Session() as session:
        state = await session.get(AdminState, 1)
        if not state or not state.layout:
            return rows
        labels = {k: v for k, v in KEYBOARD_BUTTON_DEFAULTS.items() if k.startswith("bt.")}
        configs = (await session.scalars(select(KeyboardButton).where(KeyboardButton.button_key.like("bt.%")))).all()
        labels.update({r.button_key: r.button_text for r in configs})
        if len(set(labels.values())) != len(labels):
            return rows  # Ambiguous legacy text: keep safe old layout.
        keys = {v: k for k, v in labels.items()}
        available = {}
        fixed = []
        for row in rows:
            keep = []
            for button in row:
                key = keys.get(getattr(button, "text", None))
                if key and key != "bt.menu_admin_panel":
                    available[key] = button
                else:
                    keep.append(button)
            if keep:
                fixed.append(keep)
        ordered = [[available[k] for k in row if k in available] for row in state.layout]
        return [row for row in ordered if row] + fixed
