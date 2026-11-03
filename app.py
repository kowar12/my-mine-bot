import sqlite3
import asyncio
import time
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.exceptions import TelegramBadRequest
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = "@merrymine_news" 
WEBHOOK_URL = "https://my-mine-bot.onrender.com"  # Ссылка на твой Render

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ... (твой код с init_db, get_game_keyboard, calculate_passive_income, check_achievements, cmd_start и handle_buttons остается БЕЗ ИЗМЕНЕНИЙ) ...

async def main():
    init_db()
    print("Бот запущен!")
    
    # Настраиваем вебхук при старте
    await bot.set_webhook(WEBHOOK_URL)
    
    # Создаем веб-приложение aiohttp для приема сообщений
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/")
    setup_application(app, dp, bot=bot)
    
    # Запускаем сервер на порту, который дает Render
    port = int(os.environ.get('PORT', 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Сервер запущен на порту {port}")
    
    # Держим процесс живым
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
