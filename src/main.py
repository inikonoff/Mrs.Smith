import asyncio
import logging
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from src.config import settings
from src.db import db
from src import handlers, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=settings.TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(admin.router)
dp.include_router(handlers.router)


async def health(request: web.Request) -> web.Response:
    ok = await db.ping()
    return web.json_response({"status": "ok" if ok else "degraded"}, status=200 if ok else 503)


async def start_health_server() -> web.AppRunner:
    """
    Free Render Web Service засыпает после 15 минут без входящего HTTP —
    этот сервер существует, чтобы внешний keep-alive (UptimeRobot/
    cron-job.org, настраивается отдельно) мог его пинговать. Сам бот
    работает через long polling, не через этот сервер (см. PLAN.md).
    """
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.PORT)
    await site.start()
    logger.info(f"🌐 Health server on :{settings.PORT}")
    return runner


async def main() -> None:
    await db.connect()
    runner = await start_health_server()
    try:
        logger.info("🚀 Starting polling...")
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
