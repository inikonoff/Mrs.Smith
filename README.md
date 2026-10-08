# Mrs. Smith

Карманный репетитор английского — один персонаж, один режим, бесплатно.
Решения и их обоснование — в [`PLAN.md`](PLAN.md) и [`PERSONA_PROMPT.md`](PERSONA_PROMPT.md).

## Стек

- Python / [aiogram](https://docs.aiogram.dev/) 3, long polling
- Postgres ([Neon](https://neon.tech) в проде, Docker локально) через `asyncpg`, без ORM
- Groq API (`openai/gpt-oss-120b`) для ответов, `whisper-large-v3` для транскрибации, `canopylabs/orpheus-v1-english` для TTS

## Структура

```
src/
├── main.py        # Bot/Dispatcher, aiohttp /health, запуск polling
├── config.py      # env-переменные, лимиты
├── prompt.py      # системный промпт Mrs. Smith
├── db.py          # asyncpg pool, все SQL-запросы
├── groq_client.py # вызовы Groq: ответ, Session Summary, TTS, транскрибация
├── keyboards.py   # инлайн-кнопки
├── handlers.py    # /start, онбординг, сообщения, Text/Translate
└── admin.py       # админ-панель
migrations/
└── 001_init.sql
```

## Локальный запуск

1. Postgres в Docker:
   ```bash
   docker run -d --name mrs-smith-db -e POSTGRES_PASSWORD=postgres \
     -p 5432:5432 postgres:16
   createdb -h localhost -U postgres mrs_smith   # или через psql
   ```
2. Применить миграцию:
   ```bash
   psql "postgresql://postgres:postgres@localhost:5432/mrs_smith" -f migrations/001_init.sql
   ```
3. `.env` из `.env.example` — заполнить `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEYS`, `DATABASE_URL` (строка выше), `ADMIN_IDS`.
4. ```bash
   python -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   python -m src.main
   ```

## Деплой (Render, free Web Service)

Long polling + free Web Service — подробности и почему именно так в
`PLAN.md`. Нужен внешний keep-alive (UptimeRobot / cron-job.org),
пингующий `https://<твой-сервис>.onrender.com/health` каждые 10-14 минут,
иначе free-сервис заснёт после 15 минут без входящего HTTP.

`DATABASE_URL` на Render — строка подключения к Neon (не к локальному
Docker-Postgres).

## Миграции

Новая миграция — следующий номер в `migrations/` (`002_...sql`),
применяется той же командой `psql "$DATABASE_URL" -f migrations/00N_*.sql`
и локально, и на Neon.
