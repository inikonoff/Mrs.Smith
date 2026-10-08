import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncpg

from src.config import (
    settings,
    DAILY_PAIRS_LIMIT,
    RATE_LIMIT_WINDOW,
    RATE_LIMIT_MAX,
    RATE_LIMIT_MAX_ADMIN,
    COOLDOWN_DURATION,
    WARN_THRESHOLD,
)

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=1, max_size=5)
        logger.info("✅ Postgres pool connected")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def ping(self) -> bool:
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"❌ DB ping failed: {e}")
            return False

    # ─── Users ──────────────────────────────────────────────────────────────

    async def get_or_create_user(self, telegram_id: int) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1", telegram_id
            )
            if row:
                await conn.execute(
                    "UPDATE users SET last_active = now() WHERE telegram_id = $1",
                    telegram_id,
                )
                return dict(row)

            row = await conn.fetchrow(
                "INSERT INTO users (telegram_id) VALUES ($1) RETURNING *",
                telegram_id,
            )
            logger.info(f"👤 New user {telegram_id}")
            return dict(row)

    async def set_name(self, telegram_id: int, name: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET name = $1 WHERE telegram_id = $2", name, telegram_id
            )

    async def set_level(self, telegram_id: int, level: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET level = $1 WHERE telegram_id = $2", level, telegram_id
            )

    # ─── Дневной лимит (10 пар сообщений/сутки, см. PLAN.md) ────────────────

    async def check_daily_limit(self, telegram_id: int) -> Dict[str, Any]:
        """
        Проверяет лимит ПЕРЕД обработкой нового сообщения юзера.
        Сбрасывает счётчик, если наступил новый день (UTC).
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT daily_pairs_used, daily_reset_date FROM users WHERE telegram_id = $1",
                telegram_id,
            )
            used = row["daily_pairs_used"]
            reset_date = row["daily_reset_date"]
            today = datetime.now(timezone.utc).date()

            if reset_date != today:
                await conn.execute(
                    "UPDATE users SET daily_pairs_used = 0, daily_reset_date = $2 WHERE telegram_id = $1",
                    telegram_id, today,
                )
                used = 0

            return {
                "used": used,
                "limit": DAILY_PAIRS_LIMIT,
                "reached": used >= DAILY_PAIRS_LIMIT,
            }

    async def increment_daily_pairs(self, telegram_id: int) -> Dict[str, Any]:
        """
        Вызывается ПОСЛЕ успешно отправленного ответа. Возвращает
        just_reached_limit=True ровно в тот момент, когда счётчик впервые
        дошёл до лимита за сегодня — на этом сообщении и только на нём
        хендлер должен запустить авто Session Summary.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE users SET daily_pairs_used = daily_pairs_used + 1 "
                "WHERE telegram_id = $1 RETURNING daily_pairs_used",
                telegram_id,
            )
            used = row["daily_pairs_used"]
            return {"used": used, "just_reached_limit": used == DAILY_PAIRS_LIMIT}

    # ─── Burst rate-limit (защита от спама, персистентно — см. PLAN.md) ─────

    async def check_burst(self, telegram_id: int, is_admin: bool) -> Dict[str, Any]:
        """
        Возвращает {"blocked": bool, "warn": bool}. blocked=True — сообщение
        нужно молча проигнорировать (юзер уже предупреждён при входе в
        cooldown). warn=True — сообщение обработать, но сначала предупредить
        юзера о скорости.
        """
        now = datetime.now(timezone.utc)
        limit = RATE_LIMIT_MAX_ADMIN if is_admin else RATE_LIMIT_MAX

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT burst_message_count, burst_window_start, burst_cooldown_until "
                "FROM users WHERE telegram_id = $1",
                telegram_id,
            )
            cooldown_until = row["burst_cooldown_until"]
            if not is_admin and cooldown_until and now < cooldown_until:
                return {"blocked": True, "warn": False}

            window_start = row["burst_window_start"]
            count = row["burst_message_count"] or 0

            if not window_start or (now - window_start).total_seconds() > RATE_LIMIT_WINDOW:
                window_start = now
                count = 0

            count += 1

            if count > limit and not is_admin:
                new_cooldown = now + timedelta(seconds=COOLDOWN_DURATION)
                await conn.execute(
                    "UPDATE users SET burst_message_count = $2, burst_window_start = $3, "
                    "burst_cooldown_until = $4 WHERE telegram_id = $1",
                    telegram_id, count, window_start, new_cooldown,
                )
                return {"blocked": True, "warn": False}

            await conn.execute(
                "UPDATE users SET burst_message_count = $2, burst_window_start = $3, "
                "burst_cooldown_until = NULL WHERE telegram_id = $1",
                telegram_id, count, window_start,
            )
            return {"blocked": False, "warn": (count == WARN_THRESHOLD and not is_admin)}

    # ─── Messages (история для контекста + Text/Translate после рестарта) ──

    async def save_message(self, user_id: int, role: str, text: str) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO messages (user_id, role, text) VALUES ($1, $2, $3) RETURNING id",
                user_id, role, text,
            )
            return row["id"]

    async def set_message_telegram_id(self, message_id: int, telegram_message_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE messages SET telegram_message_id = $1 WHERE id = $2",
                telegram_message_id, message_id,
            )

    async def get_message_by_telegram_id(self, user_id: int, telegram_message_id: int) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT text FROM messages WHERE user_id = $1 AND telegram_message_id = $2",
                user_id, telegram_message_id,
            )
            return row["text"] if row else None

    async def get_recent_messages(self, user_id: int, limit: int) -> List[Dict[str, str]]:
        """Последние `limit` сообщений в хронологическом порядке, для контекста промпта."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT role, text FROM messages WHERE user_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                user_id, limit,
            )
            return [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]

    async def get_session_messages(self, user_id: int, limit: int = 50) -> List[str]:
        """Сообщения юзера за сегодня — для Session Summary."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT text FROM messages WHERE user_id = $1 AND role = 'user' "
                "AND created_at >= date_trunc('day', now()) "
                "ORDER BY created_at DESC LIMIT $2",
                user_id, limit,
            )
            return [r["text"] for r in reversed(rows)]

    # ─── Error log (для Session Summary и админ-панели) ─────────────────────

    async def log_error(self, user_id: int, category: str, mistake: str, corrected: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO error_logs (user_id, category, mistake_text, corrected_text) "
                "VALUES ($1, $2, $3, $4)",
                user_id, category, mistake, corrected,
            )

    async def get_session_errors(self, user_id: int, limit: int = 20) -> List[Dict[str, str]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT category, mistake_text, corrected_text FROM error_logs "
                "WHERE user_id = $1 AND created_at >= date_trunc('day', now()) "
                "ORDER BY created_at DESC LIMIT $2",
                user_id, limit,
            )
            return [dict(r) for r in rows]

    # ─── Админ-панель ────────────────────────────────────────────────────────

    async def get_admin_stats(self) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT count(*) FROM users")
            active_today = await conn.fetchval(
                "SELECT count(*) FROM users WHERE last_active >= date_trunc('day', now())"
            )
            active_week = await conn.fetchval(
                "SELECT count(*) FROM users WHERE last_active >= now() - interval '7 days'"
            )
            messages_today = await conn.fetchval(
                "SELECT count(*) FROM messages WHERE created_at >= date_trunc('day', now())"
            )
            level_rows = await conn.fetch(
                "SELECT coalesce(level, 'unknown') AS level, count(*) AS n "
                "FROM users GROUP BY level ORDER BY n DESC"
            )
            return {
                "total_users": total_users,
                "active_today": active_today,
                "active_week": active_week,
                "messages_today": messages_today,
                "levels": {r["level"]: r["n"] for r in level_rows},
            }

    async def get_error_category_breakdown(self, limit: int = 10) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT category, count(*) AS n FROM error_logs "
                "GROUP BY category ORDER BY n DESC LIMIT $1",
                limit,
            )
            return [dict(r) for r in rows]

    async def get_user_card(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
            if not user:
                return None
            message_count = await conn.fetchval(
                "SELECT count(*) FROM messages WHERE user_id = $1", telegram_id
            )
            recent_errors = await conn.fetch(
                "SELECT category, mistake_text, corrected_text, created_at FROM error_logs "
                "WHERE user_id = $1 ORDER BY created_at DESC LIMIT 5",
                telegram_id,
            )
            return {
                "user": dict(user),
                "message_count": message_count,
                "recent_errors": [dict(r) for r in recent_errors],
            }

    async def list_users(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT telegram_id, name, level, last_active FROM users "
                "ORDER BY last_active DESC LIMIT $1 OFFSET $2",
                limit, offset,
            )
            return [dict(r) for r in rows]


db = Database()
