# Daily Ten (with Mrs. Smith)

Telegram-бот: каждый день один разговор с Mrs. Smith из **10 обменов
репликами**, в конце — **Session Summary** (голосовое + текстовая карточка
с разбором английского). В разговоре коррекции нет: ошибки собираются молча
и разбираются только в Summary. Следующие сессии МС строит на памяти о
прошлых. Решения и обоснование — в [`PLAN.md`](PLAN.md) и
[`PERSONA_PROMPT.md`](PERSONA_PROMPT.md).

## Как это работает

- `/start` — онбординг (имя + уровень) и сразу первая десятка; повторно
  показывает статус. `/ten` или кнопка «Start the ten» — новая сессия.
  `/summary` — последняя Session Summary.
- Реплики МС 1–9 обычные, 10-я — предфинальная, 11-я (после 10-го ответа)
  — прощание; сразу за ней приходит Summary. Режим выбирает код по
  счётчику, текст пишет LLM. Под каждой репликой полоска `●●●○○○○○○○`
  (в голосовых — в подписи).
- Недоделанная сессия переходит через полночь; новая десятка — не раньше
  следующих суток по Минску. Брошенная сессия закрывается через 48 часов,
  при ≥ 3 парах юзеру приходит Summary.
- Голос — mirror: на голосовое отвечаем голосом, на текст текстом.

## Стек

- Python / [aiogram](https://docs.aiogram.dev/) 3, long polling
- Postgres ([Neon](https://neon.tech) в проде, Docker локально) через `asyncpg`, без ORM
- Groq API (`openai/gpt-oss-120b`) для ответов и Summary, `whisper-large-v3` для транскрибации, `canopylabs/orpheus-v1-english` для TTS

## Структура

```
src/
├── main.py          # Bot/Dispatcher, health-сервер, фоновая задача, polling
├── health_server.py # /health (без БД), /health/db
├── config.py        # env-переменные, параметры сессии
├── prompt.py        # промпт МС (+ блок режима), промпт SS/SS4T
├── session.py       # чистая логика: режимы, день по Минску
├── db.py            # asyncpg pool, все SQL-запросы
├── groq_client.py   # вызовы Groq: ответ, SS+SS4T, TTS, транскрибация
├── summary.py       # генерация и доставка Session Summary
├── background.py    # закрытие брошенных сессий (раз в 30 минут)
├── keyboards.py     # кнопки, формат полоски прогресса
├── handlers.py      # /start, /ten, /summary, машина состояний сессии
└── admin.py         # админ-панель
migrations/
├── 001_init.sql
└── 002_sessions.sql
```

## Локальный запуск

1. Postgres в Docker:
   ```bash
   docker run -d --name mrs-smith-db -e POSTGRES_PASSWORD=postgres \
     -p 5432:5432 postgres:16
   createdb -h localhost -U postgres mrs_smith   # или через psql
   ```
2. Применить миграции по порядку (002 идемпотентна):
   ```bash
   for f in migrations/*.sql; do
     psql "postgresql://postgres:postgres@localhost:5432/mrs_smith" -f "$f"
   done
   ```
3. `.env` из `.env.example` — заполнить `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEYS`, `DATABASE_URL` (строка выше), `ADMIN_IDS`.
4. ```bash
   python -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   python -m src.main
   ```

## Деплой (Render, free Web Service + Neon)

Long polling. Free Web Service засыпает после 15 минут без входящего HTTP,
а фоновая задача (закрытие брошенных сессий) должна жить, поэтому
`health_server.py` подключён к `main.py` и нужен внешний пингер:

1. На [UptimeRobot](https://uptimerobot.com) (или cron-job.org) создать
   монитор типа HTTP(s) на `https://<твой-сервис>.onrender.com/health`
   с интервалом **не реже 10–14 минут** (Render засыпает через 15). Метод
   HEAD или GET — оба отвечают 200.
2. `/health` намеренно **не обращается к БД**: пинг каждые 10 минут иначе
   держал бы compute Neon включённым круглосуточно и тратил бесплатные
   часы. Neon сам просыпается за ~500 мс на первый запрос. Проверка БД
   вручную — `/health/db`.
3. Отдельный DB keep-alive не нужен (в отличие от Supabase, который
   ставится на паузу).

`DATABASE_URL` на Render — строка подключения к Neon
(`?sslmode=require`), не к локальному Docker-Postgres. Миграции на Neon:
`psql "$DATABASE_URL" -f migrations/00N_*.sql`.

## Админ-панель

`/admin` (только `ADMIN_IDS`): общая статистика, разбор ошибок по
категориям, **Sessions** (число сессий, доля завершённых и таймаутов,
средняя длина ответа юзера в словах по порядковому номеру сессии — график
«разговорился ли»), карточка юзера.

## Миграции

Новая миграция — следующий номер в `migrations/` (`003_...sql`),
применяется той же командой `psql "$DATABASE_URL" -f migrations/00N_*.sql`
и локально, и на Neon. `001_init.sql` не правим.
