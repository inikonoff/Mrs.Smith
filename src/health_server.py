"""
Образец keep-alive сервера для free Render Web Service — НЕ подключён
к боту (main.py запускает только long polling, без этого). Free
сервис засыпает после 15 минут без входящего HTTP; этот /health
существовал бы для внешнего пингера (UptimeRobot/cron-job.org), чтобы
такая настройка была под рукой, если бот заснёт в проде и понадобится
быстро её подключить.

Чтобы включить: импортировать start_health_server в main.py, вызвать
его рядом с db.connect() и сохранить runner для runner.cleanup() при
остановке (как было сделано изначально, до переноса сюда).
"""

import logging

from aiohttp import web

from src.config import settings
from src.db import db

logger = logging.getLogger(__name__)


async def health(request: web.Request) -> web.Response:
    ok = await db.ping()
    return web.json_response({"status": "ok" if ok else "degraded"}, status=200 if ok else 503)


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.PORT)
    await site.start()
    logger.info(f"🌐 Health server on :{settings.PORT}")
    return runner
