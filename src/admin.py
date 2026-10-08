from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from src.config import ADMIN_IDS
from src.db import db
from src.keyboards import get_admin_panel_keyboard, get_admin_back_keyboard, get_admin_users_keyboard

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not _is_admin(message.from_user.id):
        return
    await message.answer("Admin panel", reply_markup=get_admin_panel_keyboard())


@router.callback_query(F.data == "admin_back")
async def cq_admin_back(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.message.edit_text("Admin panel", reply_markup=get_admin_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cq_admin_stats(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    stats = await db.get_admin_stats()
    levels = "\n".join(f"  {level}: {n}" for level, n in stats["levels"].items()) or "  (none yet)"
    text = (
        "📊 <b>Stats</b>\n\n"
        f"Total users: {stats['total_users']}\n"
        f"Active today: {stats['active_today']}\n"
        f"Active this week: {stats['active_week']}\n"
        f"Messages today: {stats['messages_today']}\n\n"
        f"By level:\n{levels}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_errors")
async def cq_admin_errors(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    rows = await db.get_error_category_breakdown()
    if not rows:
        body = "No errors logged yet."
    else:
        body = "\n".join(f"  {r['category']}: {r['n']}" for r in rows)
    text = f"❗ <b>Error categories (all time)</b>\n\n{body}"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_users_"))
async def cq_admin_users(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    offset = int(callback.data.removeprefix("admin_users_"))
    users = await db.list_users(limit=20, offset=offset)
    if not users:
        text = "No users yet." if offset == 0 else "No more users."
    else:
        text = "👥 <b>Users</b> (tap for details)"
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=get_admin_users_keyboard(users, offset)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_"))
async def cq_admin_user_card(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer()
        return
    telegram_id = int(callback.data.removeprefix("admin_user_"))
    card = await db.get_user_card(telegram_id)
    if not card:
        await callback.answer("Not found.", show_alert=True)
        return

    u = card["user"]
    errors = card["recent_errors"]
    error_lines = "\n".join(
        f"  • {e['category']}: \"{e['mistake_text']}\" → \"{e['corrected_text']}\""
        for e in errors
    ) or "  (none logged)"

    text = (
        f"👤 <b>{u['name'] or 'Unnamed'}</b> ({u['telegram_id']})\n\n"
        f"Level: {u['level'] or 'unset'}\n"
        f"Messages: {card['message_count']}\n"
        f"Last active: {u['last_active']}\n"
        f"Today's pairs used: {u['daily_pairs_used']}\n\n"
        f"Recent errors:\n{error_lines}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())
    await callback.answer()
