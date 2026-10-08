from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_voice_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """Под голосовым ответом Mrs. Smith: показать текст / перевод."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Text", callback_data=f"text_{message_id}"),
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}"),
    )
    return builder.as_markup()


def get_text_shown_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """После показа текста — только Translate (Original вернёт обратно к тексту)."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}"))
    return builder.as_markup()


def get_translate_shown_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """
    После перевода — Original. Важно: Original должен снова показать текст
    (ту же функцию, что и кнопка Text), а не сбрасывать всё в пустоту — это
    была реальная найденная в speechflow ошибка (см. roadmap.md того проекта),
    тут делаем сразу правильно.
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔤 Original", callback_data=f"text_{message_id}"))
    return builder.as_markup()


def get_session_summary_offer_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Yes, please", callback_data="summary_yes"),
        InlineKeyboardButton(text="No, thanks", callback_data="summary_no"),
    )
    return builder.as_markup()


# ─── Админ-панель ────────────────────────────────────────────────────────────

def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Stats", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="❗ Error categories", callback_data="admin_errors"))
    builder.row(InlineKeyboardButton(text="👥 Users", callback_data="admin_users_0"))
    return builder.as_markup()


def get_admin_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="« Back", callback_data="admin_back"))
    return builder.as_markup()


def get_admin_users_keyboard(users: list, offset: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for u in users:
        label = u["name"] or str(u["telegram_id"])
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin_user_{u['telegram_id']}"))
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="« Prev", callback_data=f"admin_users_{max(0, offset - 20)}"))
    if len(users) == 20:
        nav.append(InlineKeyboardButton(text="Next »", callback_data=f"admin_users_{offset + 20}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="« Back", callback_data="admin_back"))
    return builder.as_markup()
