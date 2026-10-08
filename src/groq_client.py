import random
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from src.config import settings
from src.prompt import SYSTEM_PROMPT, STUDENT_LEVEL_HEADER

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"

FALLBACK_REPLY = {
    "category": "none",
    "mistake": "",
    "corrected": "",
    "reply": "Tell me more — I'm listening.",
}


class GroqClient:
    def __init__(self, api_keys: List[str]):
        self.clients = [
            AsyncOpenAI(
                api_key=key.strip(),
                base_url="https://api.groq.com/openai/v1",
                timeout=60.0,
            )
            for key in api_keys
            if key.strip()
        ]
        self.current_index = 0
        self._lock = asyncio.Lock()
        logger.info(f"✅ Инициализировано {len(self.clients)} Groq клиентов")

    # ─── Think-leak guard ────────────────────────────────────────────────────
    # gpt-oss-120b иногда протекает reasoning прямо в message.content
    # (<think>...</think> или без закрывающего тега), съедая весь max_tokens
    # и оставляя пустой/обрезанный ответ. Проверено на speechflow — закладываем
    # защиту с первого дня, а не после жалоб пользователей.

    @staticmethod
    def _strip_think(text: Optional[str]) -> Optional[str]:
        if not text:
            return text
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        cleaned = re.sub(r"^.*?</think>", "", cleaned, flags=re.DOTALL)
        return cleaned.strip()

    def _extract_text(self, response, label: str = "response") -> str:
        text = self._strip_think(response.choices[0].message.content)
        text = text.strip() if text else text
        if not text:
            raise ValueError(f"Empty response after stripping reasoning ({label})")
        return text

    def _extract_json(self, response, label: str = "response") -> Dict[str, Any]:
        text = self._extract_text(response, label)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {label}: {e}") from e

    # ─── Multi-key rotation ──────────────────────────────────────────────────

    async def _get_next_client(self) -> Optional[AsyncOpenAI]:
        if not self.clients:
            return None
        async with self._lock:
            client = self.clients[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.clients)
            return client

    async def _make_request(self, func, *args, **kwargs):
        if not self.clients:
            raise Exception("Нет доступных Groq клиентов")

        errors = []
        for attempt in range(len(self.clients) * 2):
            client = await self._get_next_client()
            if not client:
                break
            try:
                return await func(client, *args, **kwargs)
            except Exception as e:
                errors.append(str(e))
                logger.warning(f"❌ Groq request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(0.5 + random.random())

        raise Exception(f"Все Groq клиенты недоступны: {'; '.join(errors[:3])}")

    # ─── Единый ответ: разговор + коррекция в одном вызове ──────────────────

    async def generate_reply(
        self,
        text: str,
        level: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Возвращает {"category", "mistake", "corrected", "reply"}. Один
        LLM-вызов: "reply" — то, что видит/слышит юзер (разговор с вплетённой
        коррекцией по правилам промпта); остальные поля — только для
        error_logs. См. PERSONA_PROMPT.md для истории решения.
        """
        system_prompt = f"{SYSTEM_PROMPT}\n\n{STUDENT_LEVEL_HEADER.format(level=level.upper())}"

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _chat(client):
            response = await client.chat.completions.create(
                model=MODEL,
                extra_body={"include_reasoning": False},
                messages=messages,
                temperature=0.7,
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            return self._extract_json(response, "generate_reply")

        try:
            result = await self._make_request(_chat)
            if not result.get("reply"):
                raise ValueError("Missing 'reply' field")
            return result
        except Exception as e:
            logger.error(f"❌ Ошибка generate_reply: {e}")
            return dict(FALLBACK_REPLY)

    # ─── Session Summary (голосовое, авто при лимите или по запросу) ───────

    async def generate_session_summary(
        self,
        messages: List[str],
        errors: List[Dict[str, str]],
    ) -> str:
        if not messages:
            return (
                "We haven't talked enough yet for a proper summary — "
                "let's have a real conversation first, and I'll have plenty to say."
            )

        sample = "\n".join(f"- {m}" for m in messages[-20:])

        patterns = ""
        if errors:
            lines = [
                f'- {e["category"]}: said "{e["mistake_text"]}" → "{e["corrected_text"]}"'
                for e in errors[:6]
            ]
            patterns = "\n\nRecurring patterns from today:\n" + "\n".join(lines)

        system = (
            "You are Mrs. Smith, a warm and experienced English teacher, speaking "
            "directly to your student as a short voice message at the end of a "
            "practice session — not writing a report, actually talking to them.\n\n"
            "Cover, in flowing spoken prose:\n"
            "- What you talked about together this session\n"
            "- Their general level and how their English sounded today\n"
            "- One or two things they're doing well — genuine and specific, not generic praise\n"
            "- One or two things worth practicing next time, using the patterns below if given\n\n"
            "Speak TO them, in second person ('you'), the way a real teacher wraps up "
            "a conversation — warm, honest, not a checklist and not a grade.\n"
            "This will be read aloud by text-to-speech: no markdown, no headers, "
            "no bullet points, no numbered lists — just natural spoken sentences.\n"
            "150-220 words."
            f"{patterns}"
        )

        async def _summary(client):
            response = await client.chat.completions.create(
                model=MODEL,
                extra_body={"include_reasoning": False},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Student messages from this session:\n{sample}"},
                ],
                temperature=0.6,
                max_tokens=500,
            )
            return self._extract_text(response, "generate_session_summary")

        try:
            return await self._make_request(_summary)
        except Exception as e:
            logger.error(f"❌ Ошибка generate_session_summary: {e}")
            return (
                "I couldn't put together a proper summary right now — but I've "
                "enjoyed our conversation today. See you next time."
            )

    # ─── Перевод (кнопка Translate) ──────────────────────────────────────────

    async def translate_text(self, text: str) -> str:
        async def _translate(client):
            response = await client.chat.completions.create(
                model=MODEL,
                extra_body={"include_reasoning": False},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a translator. Translate the given English text to Russian.\n"
                            "Translate idioms by meaning, not literally. Preserve the tone. "
                            "Return only the translation, no comments."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
                max_tokens=400,
            )
            return self._extract_text(response, "translate_text")

        try:
            return await self._make_request(_translate)
        except Exception as e:
            logger.error(f"❌ Ошибка перевода: {e}")
            return "Translation failed."

    # ─── Транскрибация голосовых юзера ──────────────────────────────────────

    async def transcribe_audio(self, audio_bytes: bytes) -> Optional[str]:
        async def _transcribe(client):
            response = await client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=("voice.ogg", audio_bytes, "audio/ogg"),
                language="en",
                response_format="text",
                temperature=0.0,
            )
            return response

        try:
            result = await self._make_request(_transcribe)
            text = result if isinstance(result, str) else getattr(result, "text", str(result))
            return text.strip()
        except Exception as e:
            logger.error(f"❌ Ошибка транскрибации: {e}")
            return None

    # ─── TTS ─────────────────────────────────────────────────────────────────

    async def text_to_speech(self, text: str, voice: str) -> Optional[bytes]:
        async def _tts(client):
            response = await client.audio.speech.create(
                model="canopylabs/orpheus-v1-english",
                voice=voice,
                input=text,
                response_format="wav",
            )
            if hasattr(response, "content"):
                return response.content
            elif hasattr(response, "read"):
                return await response.read()
            return bytes(response)

        try:
            return await self._make_request(_tts)
        except Exception as e:
            logger.error(f"❌ Ошибка TTS: {e}")
            return None


groq_client = GroqClient(settings.groq_api_keys_list)
