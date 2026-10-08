import html
import logging
from typing import Dict, Optional

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import ADMIN_IDS, VOICE, DISPLAY_NAME, CONTEXT_WINDOW, DAILY_PAIRS_LIMIT
from src.db import db
from src.groq_client import groq_client
from src.keyboards import (
    get_voice_keyboard,
    get_text_shown_keyboard,
    get_translate_shown_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()

# Telegram отклоняет edit_caption выше этого лимита (MEDIA_CAPTION_TOO_LONG) —
# у обычного текстового сообщения лимит 4096, у подписи к медиа — 1024.
# Это реальный баг, пойманный в speechflow (см. его changelog) — здесь
# закладываем защиту сразу, а не после жалоб пользователей.
TELEGRAM_CAPTION_LIMIT = 1024

# In-memory кэш текста под кнопками Text/Translate — быстрый путь, с
# фоллбэком на БД (messages.telegram_message_id) после рестарта процесса.
_originals: Dict[int, str] = {}
_translations: Dict[int, str] = {}


async def _edit_display(message: Message, new_content: str, reply_markup) -> None:
    """
    Text/Translate-кнопки висят и под голосовыми (подпись к медиа), и под
    обычными текстовыми ответами (если юзер писал текстом) — edit_caption
    работает только для первых, edit_text только для вторых. Без этой
    развилки Translate на текстовом ответе падал бы с ошибкой API.
    """
    is_media = bool(message.voice or message.audio or message.video or message.photo or message.document)
    if is_media:
        await message.edit_caption(caption=new_content, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await message.edit_text(new_content, parse_mode="HTML", reply_markup=reply_markup)


def _cache_original(telegram_message_id: int, text: str) -> None:
    if len(_originals) > 5000:
        _originals.clear()
    _originals[telegram_message_id] = text


async def _get_original(user_id: int, telegram_message_id: int) -> Optional[str]:
    cached = _originals.get(telegram_message_id)
    if cached:
        return cached
    text = await db.get_message_by_telegram_id(user_id, telegram_message_id)
    if text:
        _cache_original(telegram_message_id, text)
    return text


# ─── Онбординг ────────────────────────────────────────────────────────────────

class Onboarding(StatesGroup):
    waiting_name = State()
    waiting_level = State()


def _level_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Beginner", callback_data="level_beginner"),
        InlineKeyboardButton(text="Intermediate", callback_data="level_intermediate"),
        InlineKeyboardButton(text="Advanced", callback_data="level_advanced"),
    )
    return builder.as_markup()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = await db.get_or_create_user(message.from_user.id)
    if user["name"] and user["level"]:
        await message.answer(f"Welcome back. I'm {DISPLAY_NAME} — ready when you are.")
        return
    await state.set_state(Onboarding.waiting_name)
    await message.answer(
        f"Hello, I'm {DISPLAY_NAME}. I'll be your English teacher here — "
        f"we'll talk, and I'll help you along the way. What should I call you?"
    )


@router.message(Onboarding.waiting_name)
async def onboarding_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()[:100]
    if not name:
        await message.answer("Just your name is fine.")
        return
    await db.set_name(message.from_user.id, name)
    await state.set_state(Onboarding.waiting_level)
    await message.answer(
        f"Lovely to meet you, {name}. How would you describe your English?",
        reply_markup=_level_keyboard(),
    )


@router.callback_query(Onboarding.waiting_level, F.data.startswith("level_"))
async def onboarding_level(callback: CallbackQuery, state: FSMContext):
    level = callback.data.removeprefix("level_")
    await db.set_level(callback.from_user.id, level)
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Good. Then let's begin — tell me something about your day.")
    await callback.answer()


# ─── Основной разговор ────────────────────────────────────────────────────────

async def _send_session_summary(message: Message, user_id: int, auto: bool = False) -> None:
    if auto:
        # Это сообщение, на котором счётчик дошёл до дневного лимита —
        # объясняем, почему сейчас приходит саммари, вместо того чтобы
        # просто вывалить его без контекста сразу после обычного ответа.
        await message.answer(
            f"That's your {DAILY_PAIRS_LIMIT} exchanges for today — a good "
            f"stopping point. Before you go, let me tell you how it went."
        )

    session_messages = await db.get_session_messages(user_id)
    session_errors = await db.get_session_errors(user_id)
    summary_text = await groq_client.generate_session_summary(session_messages, session_errors)

    voice_bytes = await groq_client.text_to_speech(summary_text, voice=VOICE)
    if voice_bytes:
        voice_file = BufferedInputFile(voice_bytes, filename="summary.wav")
        sent = await message.answer_voice(
            voice_file,
            caption=f"🎙 {DISPLAY_NAME} — Session Summary",
        )
        msg_id = await db.save_message(user_id, "assistant", summary_text)
        await db.set_message_telegram_id(msg_id, sent.message_id)
        _cache_original(sent.message_id, summary_text)
        await sent.edit_reply_markup(reply_markup=get_voice_keyboard(sent.message_id))
    else:
        safe = html.escape(summary_text)
        await message.answer(safe, parse_mode="HTML")


@router.message(Command("summary"))
async def cmd_summary(message: Message):
    await message.answer("One moment...")
    await _send_session_summary(message, message.from_user.id)


async def _process_turn(message: Message, bot: Bot, user_text: str, reply_as_voice: bool) -> None:
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS

    burst = await db.check_burst(user_id, is_admin)
    if burst["blocked"]:
        return
    if burst["warn"]:
        await message.answer("You're sending messages very fast — slow down a little so I can keep up.")

    daily = await db.check_daily_limit(user_id)
    if daily["reached"]:
        await message.answer(
            f"That's your {DAILY_PAIRS_LIMIT} exchanges for today — come back "
            f"tomorrow and we'll pick up right where we left off."
        )
        return

    user = await db.get_or_create_user(user_id)
    history = await db.get_recent_messages(user_id, CONTEXT_WINDOW)

    result = await groq_client.generate_reply(user_text, user.get("level") or "intermediate", history)

    await db.save_message(user_id, "user", user_text)
    bot_msg_id = await db.save_message(user_id, "assistant", result["reply"])

    if result.get("category") not in (None, "none", "") and result.get("mistake"):
        await db.log_error(user_id, result["category"], result["mistake"], result.get("corrected", ""))

    if reply_as_voice:
        voice_bytes = await groq_client.text_to_speech(result["reply"], voice=VOICE)
        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="reply.wav")
            sent = await message.answer_voice(voice_file, caption=f"🎙 {DISPLAY_NAME}")
            await db.set_message_telegram_id(bot_msg_id, sent.message_id)
            _cache_original(sent.message_id, result["reply"])
            await sent.edit_reply_markup(reply_markup=get_voice_keyboard(sent.message_id))
        else:
            safe = html.escape(result["reply"])
            sent = await message.answer(f"💬 {safe}", parse_mode="HTML")
            await db.set_message_telegram_id(bot_msg_id, sent.message_id)
            _cache_original(sent.message_id, result["reply"])
            await sent.edit_reply_markup(reply_markup=get_text_shown_keyboard(sent.message_id))
    else:
        safe = html.escape(result["reply"])
        sent = await message.answer(safe, parse_mode="HTML")
        await db.set_message_telegram_id(bot_msg_id, sent.message_id)
        _cache_original(sent.message_id, result["reply"])
        await sent.edit_reply_markup(reply_markup=get_text_shown_keyboard(sent.message_id))

    inc = await db.increment_daily_pairs(user_id)
    if inc["just_reached_limit"]:
        await _send_session_summary(message, user_id, auto=True)


@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext, bot: Bot):
    if await state.get_state() is not None:
        await message.answer("Text works better while we're getting set up — go ahead.")
        return
    file = await bot.get_file(message.voice.file_id)
    audio_bytes = (await bot.download_file(file.file_path)).read()
    text = await groq_client.transcribe_audio(audio_bytes)
    if not text:
        await message.answer("I couldn't quite hear that — mind trying again?")
        return
    await _process_turn(message, bot, text, reply_as_voice=True)


@router.message(F.text)
async def handle_text(message: Message, state: FSMContext, bot: Bot):
    if await state.get_state() is not None:
        await message.answer("Go ahead and tap one of the buttons above.")
        return
    await _process_turn(message, bot, message.text, reply_as_voice=False)


# ─── Text / Translate кнопки ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("text_"))
async def cq_show_text(callback: CallbackQuery):
    message_id = int(callback.data.removeprefix("text_"))
    text = await _get_original(callback.from_user.id, message_id)
    if not text:
        await callback.answer("Not available.", show_alert=True)
        return
    safe = html.escape(text)
    caption = f"💬 {safe}"
    if len(caption) > TELEGRAM_CAPTION_LIMIT:
        # Подпись к медиа ограничена 1024 символами — длинный текст
        # (например, Session Summary) уходит отдельным сообщением.
        sent = await callback.message.answer(caption, parse_mode="HTML")
        _cache_original(sent.message_id, text)
        await sent.edit_reply_markup(reply_markup=get_text_shown_keyboard(sent.message_id))
    else:
        await _edit_display(callback.message, caption, get_text_shown_keyboard(message_id))
    await callback.answer()


@router.callback_query(F.data.startswith("translate_"))
async def cq_translate(callback: CallbackQuery):
    message_id = int(callback.data.removeprefix("translate_"))
    text = await _get_original(callback.from_user.id, message_id)
    if not text:
        await callback.answer("Not available.", show_alert=True)
        return

    translation = _translations.get(message_id)
    if not translation:
        translation = await groq_client.translate_text(text)
        _translations[message_id] = translation

    safe = html.escape(translation)
    caption = f"🌐 {safe}"
    if len(caption) > TELEGRAM_CAPTION_LIMIT:
        sent = await callback.message.answer(caption, parse_mode="HTML")
        _cache_original(sent.message_id, text)  # Original на новом сообщении должен работать
        _translations[sent.message_id] = translation
        await sent.edit_reply_markup(reply_markup=get_translate_shown_keyboard(sent.message_id))
    else:
        await _edit_display(callback.message, caption, get_translate_shown_keyboard(message_id))
    await callback.answer()
