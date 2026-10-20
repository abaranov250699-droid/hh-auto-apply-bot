import os
import sys
import asyncio
import logging
import httpx
from aiohttp import web

from bot import bot, dp
import database as db
from scheduler import start_monitoring_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("HHAutoApplyBot")

# --- HTTP сервер для Render.com Web Service ---
async def handle_health(request):
    return web.json_response({"status": "ok", "service": "HH-Auto-Apply-Bot", "state": "alive"})

async def handle_root(request):
    return web.Response(
        text="<h2>HH.ru Auto-Apply Bot is Running 24/7!</h2><p>Telegram Bot: active</p>", 
        content_type="text/html"
    )

async def start_http_server():
    """Запуск легковесного веб-сервера для прохождения проверок Render."""
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Render HTTP health-check server started on port {port}")

async def keep_alive_pinger():
    """
    Функция самопинга, чтобы Render.com Free Tier не засыпал каждые 15 минут.
    """
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PING_URL")
    if not url:
        logger.info("RENDER_EXTERNAL_URL not set; skipping auto-pinger. (Using local mode)")
        return

    health_url = f"{url.rstrip('/')}/health"
    logger.info(f"Starting auto-pinger targeting: {health_url}")
    
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(600)  # каждые 10 минут
            try:
                resp = await client.get(health_url, timeout=15)
                logger.info(f"Self-ping to {health_url} status: {resp.status_code}")
            except Exception as e:
                logger.warning(f"Self-ping failed: {e}")

async def main():
    logger.info("Initializing database...")
    await db.init_db()

    # Запускаем HTTP сервер для Render
    await start_http_server()

    # Запускаем фоновый пингер для предотвращения засыпания Render
    asyncio.create_task(keep_alive_pinger())

    # Запускаем фоновый планировщик проверки вакансий
    logger.info("Starting background vacancy monitoring scheduler...")
    asyncio.create_task(start_monitoring_scheduler(bot))

    # Сбрасываем старые вебхуки и запускаем polling
    logger.info("Deleting old webhook and starting Telegram bot polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    bot_info = await bot.get_me()
    logger.info(f"Bot successfully launched as @{bot_info.username} (ID: {bot_info.id})")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
