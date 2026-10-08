from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    GROQ_API_KEYS: str = ""
    DATABASE_URL: str
    PORT: int = 10000

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def groq_api_keys_list(self) -> List[str]:
        return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]


settings = Settings()


def get_admin_ids() -> List[int]:
    import os
    raw = os.environ.get("ADMIN_IDS", "")
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


ADMIN_IDS = get_admin_ids()

# ─── Персонаж ───────────────────────────────────────────────────────────────
VOICE = "diana"
DISPLAY_NAME = "Mrs. Smith"

# ─── Лимиты (см. PLAN.md) ───────────────────────────────────────────────────
DAILY_PAIRS_LIMIT = 10       # пар сообщений/сутки, дальше — авто Session Summary
CONTEXT_WINDOW = 5           # последних сообщений в промпте

# ─── Burst rate-limit (защита от спама) ─────────────────────────────────────
RATE_LIMIT_WINDOW = 60       # секунд для подсчёта сообщений
RATE_LIMIT_MAX = 10          # макс сообщений за WINDOW для обычного юзера
RATE_LIMIT_MAX_ADMIN = 100
COOLDOWN_DURATION = 60       # секунд тишины после превышения лимита
WARN_THRESHOLD = 7
