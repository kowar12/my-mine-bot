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

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = "@merrymine_news"
WEBHOOK_URL = "https://my-mine-bot.onrender.com" 

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("idle_game.db", timeout=15)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 0,
            pickaxe_level INTEGER DEFAULT 1, last_bonus_time REAL DEFAULT 0,
            last_sub_bonus_time REAL DEFAULT 0, last_mine_time REAL DEFAULT 0,
            last_seen REAL DEFAULT 0, total_mined INTEGER DEFAULT 0,
            current_status TEXT DEFAULT 'Новичок'
        )
    """)
    for col in ["last_sub_bonus_time", "last_mine_time", "last_seen"]:
        try: cursor.execute(f"ALTER TABLE players ADD COLUMN {col} REAL DEFAULT 0")
        except sqlite3.OperationalError: pass
    try: cursor.execute("ALTER TABLE players ADD COLUMN total_mined INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: cursor.execute("ALTER TABLE players ADD COLUMN current_status TEXT DEFAULT 'Новичок'")
    except sqlite3.OperationalError: pass
    cursor.execute("CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, chat_title TEXT, total_balance INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS achievements (user_id INTEGER, ach_name TEXT, PRIMARY KEY (user_id, ach_name))")
    conn.commit()
    conn.close()

def get_player_level(total_mined): return (total_mined // 500) + 1
def get_upgrade_cost(level): return 50 + (level - 1) * 25

def get_game_keyboard(pickaxe_level=1):
    cost = get_upgrade_cost(pickaxe_level)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛏️ Копать", callback_data="mine")],
        [InlineKeyboardButton(text=f"🛒 Улучшить кирку (Цена: {cost})", callback_data="upgrade")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top_players")],
        [InlineKeyboardButton(text="🏰 Топ бесед", callback_data="top_chats")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton(text="📢 Подписка", callback_data="sub_bonus")],
        [InlineKeyboardButton(text="🏅 Достижения", callback_data="my_achievements")]
    ])

def calculate_passive_income(user_id, balance, last_seen):
    current_time = time.time()
    if last_seen == 0: return balance, current_time
    mined_offline = int((current_time - last_seen) // 60)
    if mined_offline > 480: mined_offline = 480
    return balance + mined_offline, current_time

async def check_achievements(user_id, balance, pickaxe_level, chat_id, message_obj):
    conn = sqlite3.connect("idle_game.db", timeout=15)
    cursor = conn.cursor()
    new_achievements = []
    if balance >= 10:
        cursor.execute("SELECT 1 FROM achievements WHERE user_id = ? AND ach_name = 'Первая руда'", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO achievements (user_id, ach_name) VALUES (?, ?)", (user_id, 'Первая руда'))
            new_achievements.append("⛏️ Первая руда")
    if balance >= 1000:
        cursor.execute("SELECT 1 FROM achievements WHERE user_id = ? AND ach_name = 'Богач'", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO achievements (user_id, ach_name) VALUES (?, ?)", (user_id, 'Богач'))
            new_achievements.append("💰 Богач")
    if pickaxe_level >= 5:
        cursor.execute("SELECT 1 FROM achievements WHERE user_id = ? AND ach_name = 'Трудяга'", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO achievements (user_id, ach_name) VALUES (?, ?)", (user_id, 'Трудяга'))
            new_achievements.append("🛠️ Трудяга")
    conn.commit()
    conn.close()
    if new_achievements:
        text = "🎉 **Новое достижение!**\n\n" + "\n".join(new_achievements)
        await message_obj.answer(text, parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        chat_id = message.chat.id
        chat_title = message.chat.title or "Личный чат"
        conn = sqlite3.connect("idle_game.db", timeout=15)
        cursor = conn.cursor()
        cursor.execute("SELECT balance, pickaxe_level, last_seen FROM players WHERE user_id = ?", (user_id,))
        player = cursor.fetchone()
        current_time = time.time()
        if not player:
            cursor.execute("INSERT INTO players (user_id, username, last_seen) VALUES (?, ?, ?)", (user_id, username, current_time))
            level, balance, offline_text = 1, 0, ""
        else:
            balance, level, last_seen = player
            new_balance, current_time = calculate_passive_income(user_id, balance, last_seen)
            offline_mined = new_balance - balance
            balance = new_balance
            offline_text = f"\n\n💤 Пока тебя не было, кирка накопала {offline_mined} руды!" if offline_mined > 0 else ""
            cursor.execute("UPDATE players SET balance = ?, last_seen = ? WHERE user_id = ?", (balance, current_time, user_id))
        cursor.execute("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO chats (chat_id, chat_title) VALUES (?, ?)", (chat_id, chat_title))
        conn.commit()
        conn.close()
        caption = f"⛏️ **Привет, {username}!**\n\nДобро пожаловать в «Шахту»!{offline_text}\n\n💰 **Твой баланс:** {balance} руды\n🛠 **Твоя кирка:** {level} ур.\n\nЖми «Копать», чтобы добывать руду!"
        await message.reply(caption, parse_mode="Markdown", reply_markup=get_game_keyboard(level))
    except Exception as e:
        await message.reply(f"Ошибка: {e}")

@dp.callback_query()
async def handle_buttons(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        chat_id = callback.message.chat.id
        action = callback.data
        conn = sqlite3.connect("idle_game.db", timeout=15)
        cursor = conn.cursor()
        cursor.execute("SELECT balance, pickaxe_level, last_bonus_time, last_sub_bonus_time, last_mine_time, last_seen, total_mined, current_status FROM players WHERE user_id = ?", (user_id,))
        player = cursor.fetchone()
        if not player:
            await callback.answer("Сначала напиши /start")
            conn.close()
            return
        balance, pickaxe_level, last_bonus_time, last_sub_bonus_time, last_mine_time, last_seen, total_mined, current_status = player
        current_time = time.time()
        balance, current_time = calculate_passive_income(user_id, balance, last_seen)
        cursor.execute("UPDATE players SET balance = ?, last_seen = ? WHERE user_id = ?", (balance, current_time, user_id))

        if action == "mine":
            if current_time - last_mine_time < 5:
                await callback.answer(f"⏳ Подожди {round(5 - (current_time - last_mine_time), 1)} сек.", show_alert=True)
                conn.close()
                return
            mined = 10 * pickaxe_level
            new_balance = balance + mined
            new_total = total_mined + mined
            cursor.execute("UPDATE players SET balance = ?, last_mine_time = ?, total_mined = ? WHERE user_id = ?", (new_balance, current_time, new_total, user_id))
            if chat_id < 0:
                cursor.execute("UPDATE chats SET total_balance = total_balance + ? WHERE chat_id = ?", (mined, chat_id))
            conn.commit()
            await check_achievements(user_id, new_balance, pickaxe_level, chat_id, callback.message)
            lvl_text = f"\n\n🎉 **Новый уровень!**" if get_player_level(new_total) > get_player_level(total_mined) else ""
            await callback.message.edit_text(f"⛏️ {callback.from_user.first_name} накопал {mined} руды!\nБаланс: {new_balance}{lvl_text}", parse_mode="Markdown", reply_markup=get_game_keyboard(pickaxe_level))
        elif action == "upgrade":
            cost = get_upgrade_cost(pickaxe_level)
            if balance >= cost:
                cursor.execute("UPDATE players SET balance = ?, pickaxe_level = ? WHERE user_id = ?", (balance - cost, pickaxe_level + 1, user_id))
                conn.commit()
                await check_achievements(user_id, balance - cost, pickaxe_level + 1, chat_id, callback.message)
                await callback.message.edit_text(f"🛒 Кирка улучшена до {pickaxe_level + 1} ур.!", reply_markup=get_game_keyboard(pickaxe_level + 1))
            else: await callback.answer(f"Нужно {cost} руды!", show_alert=True)
        elif action == "profile":
            await callback.message.answer(f"👤 **Профиль:** {callback.from_user.first_name}\n🎖 Статус: {current_status}\n🏆 Уровень: {get_player_level(total_mined)}\n💰 Баланс: {balance}\n🛠 Кирка: {pickaxe_level}", parse_mode="Markdown")
        elif action == "top_players":
            cursor.execute("SELECT username, balance FROM players ORDER BY balance DESC LIMIT 5")
            text = "🏆 Топ игроков:\n" + "\n".join([f"{i}. @{n} — {b} руды" for i, (n, b) in enumerate(cursor.fetchall(), 1)])
            await callback.message.answer(text)
        elif action == "top_chats":
            cursor.execute("SELECT chat_title, total_balance FROM chats ORDER BY total_balance DESC LIMIT 5")
            text = "🏰 Топ бесед:\n" + "\n".join([f"{i}. {t} — {b} руды" for i, (t, b) in enumerate(cursor.fetchall(), 1)])
            await callback.message.answer(text)
        elif action == "bonus":
            if current_time - last_bonus_time < 86400: await callback.answer("Приходи завтра!", show_alert=True)
            else:
                cursor.execute("UPDATE players SET balance = ?, last_bonus_time = ? WHERE user_id = ?", (balance + 100, current_time, user_id))
                conn.commit()
                await callback.message.edit_text(f"🎁 Бонус +100 руды!", reply_markup=get_game_keyboard(pickaxe_level))
        elif action == "my_achievements":
            cursor.execute("SELECT ach_name FROM achievements WHERE user_id = ?", (user_id,))
            achs = cursor.fetchall()
            await callback.message.answer("🏅 Достижения:\n" + ("\n".join([f"• {a[0]}" for a in achs]) if achs else "Нет"))
        conn.close()
    except Exception as e:
        print(e)

# --- ЗАПУСК СЕРВЕРА ДЛЯ RENDER ---
async def main():
    init_db()  # <--- Вот она, та самая функция, которую он не видел
    print("Бот запущен!")
    
    await bot.set_webhook(WEBHOOK_URL)
    print(f"Вебхук установлен на {WEBHOOK_URL}")
    
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/")
    setup_application(app, dp, bot=bot)
    
    port = int(os.environ.get('PORT', 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Сервер запущен на порту {port}")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
