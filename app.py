import asyncio
import time
import os
import random
import asyncpg
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
DATABASE_URL = os.environ.get("DATABASE_URL") 

bot = Bot(token=TOKEN)
dp = Dispatcher()
db_pool = None

# --- СПИСОК РУД (Шанс выпадения, награда, эмодзи) ---
ORES = [
    {"name": "Обычная руда", "chance": 60, "reward": 10, "emoji": "🪨"},
    {"name": "Уголь", "chance": 25, "reward": 25, "emoji": "🪵"},
    {"name": "Золото", "chance": 10, "reward": 60, "emoji": "🥇"},
    {"name": "Алмаз", "chance": 4, "reward": 150, "emoji": "💎"},
    {"name": "Супер руда", "chance": 1, "reward": 500, "emoji": "🔮"}
]

# --- БАЗА ДАННЫХ ---
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id BIGINT PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 0,
                pickaxe_level INTEGER DEFAULT 1, last_bonus_time DOUBLE PRECISION DEFAULT 0,
                last_sub_bonus_time DOUBLE PRECISION DEFAULT 0, last_mine_time DOUBLE PRECISION DEFAULT 0,
                last_seen DOUBLE PRECISION DEFAULT 0, total_mined INTEGER DEFAULT 0,
                current_status TEXT DEFAULT 'Новичок'
            )
        """)
        await conn.execute("CREATE TABLE IF NOT EXISTS chats (chat_id BIGINT PRIMARY KEY, chat_title TEXT, total_balance INTEGER DEFAULT 0)")
        await conn.execute("CREATE TABLE IF NOT EXISTS achievements (user_id BIGINT, ach_name TEXT, PRIMARY KEY (user_id, ach_name))")

def get_player_level(total_mined): return (total_mined // 500) + 1
def get_upgrade_cost(level): return 100 + (level - 1) * 50

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

def roll_ore():
    ores = [o["name"] for o in ORES]
    chances = [o["chance"] for o in ORES]
    chosen_name = random.choices(ores, weights=chances, k=1)[0]
    return next(o for o in ORES if o["name"] == chosen_name)

async def check_achievements(user_id, balance, pickaxe_level, chat_id, message_obj):
    new_achievements = []
    async with db_pool.acquire() as conn:
        if balance >= 10:
            res = await conn.fetchrow("SELECT 1 FROM achievements WHERE user_id = $1 AND ach_name = 'Первая руда'", user_id)
            if not res:
                await conn.execute("INSERT INTO achievements (user_id, ach_name) VALUES ($1, $2)", user_id, 'Первая руда')
                new_achievements.append("⛏️ Первая руда")
        if balance >= 1000:
            res = await conn.fetchrow("SELECT 1 FROM achievements WHERE user_id = $1 AND ach_name = 'Богач'", user_id)
            if not res:
                await conn.execute("INSERT INTO achievements (user_id, ach_name) VALUES ($1, $2)", user_id, 'Богач')
                new_achievements.append("💰 Богач")
        if pickaxe_level >= 5:
            res = await conn.fetchrow("SELECT 1 FROM achievements WHERE user_id = $1 AND ach_name = 'Трудяга'", user_id)
            if not res:
                await conn.execute("INSERT INTO achievements (user_id, ach_name) VALUES ($1, $2)", user_id, 'Трудяга')
                new_achievements.append("🛠️ Трудяга")
    
    if new_achievements:
        text = "🎉 Новое достижение!\n\n" + "\n".join(new_achievements)
        await message_obj.answer(text)

@dp.message(Command("start", ignore_mention=True))
async def cmd_start(message: types.Message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        chat_id = message.chat.id
        chat_title = message.chat.title or "Личный чат"
        
        async with db_pool.acquire() as conn:
            player = await conn.fetchrow("SELECT balance, pickaxe_level, last_seen FROM players WHERE user_id = $1", user_id)
            current_time = time.time()
            if not player:
                await conn.execute("INSERT INTO players (user_id, username, last_seen) VALUES ($1, $2, $3)", user_id, username, current_time)
                level, balance, offline_text = 1, 0, ""
            else:
                balance, level, last_seen = player['balance'], player['pickaxe_level'], player['last_seen']
                new_balance, current_time = calculate_passive_income(user_id, balance, last_seen)
                offline_mined = new_balance - balance
                balance = new_balance
                offline_text = f"\n\n💤 Пока тебя не было, шахта накопала {offline_mined} монет!" if offline_mined > 0 else ""
                await conn.execute("UPDATE players SET balance = $1, last_seen = $2 WHERE user_id = $3", balance, current_time, user_id)
            
            chat = await conn.fetchrow("SELECT chat_id FROM chats WHERE chat_id = $1", chat_id)
            if not chat:
                await conn.execute("INSERT INTO chats (chat_id, chat_title) VALUES ($1, $2)", chat_id, chat_title)
        
        caption = f"⛏️ Привет, {username}!\n\nДобро пожаловать в «Шахту»!{offline_text}\n\n💰 Твой баланс: {balance} монет\n🛠 Твоя кирка: {level} ур.\n\nЖми «Копать», чтобы добывать руду!"
        await message.reply(caption, reply_markup=get_game_keyboard(level))
    except Exception as e:
        await message.reply(f"Ошибка: {e}")

@dp.callback_query()
async def handle_buttons(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        chat_id = callback.message.chat.id
        action = callback.data
        
        async with db_pool.acquire() as conn:
            player = await conn.fetchrow("SELECT * FROM players WHERE user_id = $1", user_id)
            if not player:
                await callback.answer("Сначала напиши /start")
                return
            
            balance, pickaxe_level = player['balance'], player['pickaxe_level']
            last_bonus_time, last_sub_bonus_time, last_mine_time = player['last_bonus_time'], player['last_sub_bonus_time'], player['last_mine_time']
            last_seen, total_mined, current_status = player['last_seen'], player['total_mined'], player['current_status']
            current_time = time.time()
            
            balance, current_time = calculate_passive_income(user_id, balance, last_seen)
            await conn.execute("UPDATE players SET balance = $1, last_seen = $2 WHERE user_id = $3", balance, current_time, user_id)

            if action == "mine":
                if current_time - last_mine_time < 5:
                    await callback.answer(f"⏳ Подожди {round(5 - (current_time - last_mine_time), 1)} сек.", show_alert=True)
                    return
                
                ore = roll_ore()
                mined = ore["reward"] * pickaxe_level
                new_balance = balance + mined
                new_total = total_mined + mined
                
                await conn.execute("UPDATE players SET balance = $1, last_mine_time = $2, total_mined = $3 WHERE user_id = $4", new_balance, current_time, new_total, user_id)
                if chat_id < 0:
                    await conn.execute("UPDATE chats SET total_balance = total_balance + $1 WHERE chat_id = $2", mined, chat_id)
                
                await check_achievements(user_id, new_balance, pickaxe_level, chat_id, callback.message)
                lvl_text = f"\n\n🎉 Новый уровень!" if get_player_level(new_total) > get_player_level(total_mined) else ""
                
                await callback.message.edit_text(
                    f"⛏️ {callback.from_user.first_name} накопал: {ore['emoji']} {ore['name']}!\n"
                    f"💰 Получено: {mined} монет\n"
                    f"💳 Баланс: {new_balance}{lvl_text}", 
                    reply_markup=get_game_keyboard(pickaxe_level)
                )
            
            elif action == "upgrade":
                cost = get_upgrade_cost(pickaxe_level)
                if balance >= cost:
                    await conn.execute("UPDATE players SET balance = $1, pickaxe_level = $2 WHERE user_id = $3", balance - cost, pickaxe_level + 1, user_id)
                    await check_achievements(user_id, balance - cost, pickaxe_level + 1, chat_id, callback.message)
                    await callback.message.edit_text(f"🛒 Кирка улучшена до {pickaxe_level + 1} ур.!", reply_markup=get_game_keyboard(pickaxe_level + 1))
                else: 
                    await callback.answer(f"Нужно {cost} монет!", show_alert=True)
            
            elif action == "profile":
                await callback.message.answer(f"👤 Профиль: {callback.from_user.first_name}\n🎖 Статус: {current_status}\n🏆 Уровень: {get_player_level(total_mined)}\n💰 Баланс: {balance} монет\n🛠 Кирка: {pickaxe_level}")
            
            elif action == "top_players":
                rows = await conn.fetch("SELECT username, balance FROM players ORDER BY balance DESC LIMIT 5")
                text = "🏆 Топ игроков:\n" + "\n".join([f"{i}. @{r['username']} — {r['balance']} монет" for i, r in enumerate(rows, 1)])
                await callback.message.answer(text)
            
            elif action == "top_chats":
                rows = await conn.fetch("SELECT chat_title, total_balance FROM chats ORDER BY total_balance DESC LIMIT 5")
                text = "🏰 Топ бесед:\n" + "\n".join([f"{i}. {r['chat_title']} — {r['total_balance']} монет" for i, r in enumerate(rows, 1)])
                await callback.message.answer(text)
            
            elif action == "bonus":
                if current_time - last_bonus_time < 86400: 
                    await callback.answer("Приходи завтра!", show_alert=True)
                else:
                    await conn.execute("UPDATE players SET balance = $1, last_bonus_time = $2 WHERE user_id = $3", balance + 100, current_time, user_id)
                    await callback.message.edit_text(f"🎁 Бонус +100 монет!", reply_markup=get_game_keyboard(pickaxe_level))
            
            elif action == "my_achievements":
                rows = await conn.fetch("SELECT ach_name FROM achievements WHERE user_id = $1", user_id)
                achs = [r['ach_name'] for r in rows]
                await callback.message.answer("🏅 Достижения:\n" + ("\n".join([f"• {a}" for a in achs]) if achs else "Нет"))
                
    except Exception as e:
        print(e)

# --- КОМАНДА ДЛЯ ВЫДАЧИ ДОСТИЖЕНИЙ (ТОЛЬКО ДЛЯ АДМИНА) ---
@dp.message(Command("give_ach"))
async def cmd_give_ach(message: types.Message):
    ADMIN_ID = 6564750882
    if message.from_user.id != ADMIN_ID:
        await message.reply("У тебя нет прав для этой команды.")
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("Используй: /give_ach @username Название достижения")
        return

    target_username = args[1].replace("@", "")
    ach_name = args[2]

    async with db_pool.acquire() as conn:
        player = await conn.fetchrow("SELECT user_id FROM players WHERE username = $1", target_username)
        if not player:
            await message.reply(f"Игрок с юзернеймом @{target_username} не найден в базе.")
            return
        existing = await conn.fetchrow("SELECT 1 FROM achievements WHERE user_id = $1 AND ach_name = $2", player['user_id'], ach_name)
        if existing:
            await message.reply(f"У игрока @{target_username} уже есть достижение «{ach_name}».")
            return
        await conn.execute("INSERT INTO achievements (user_id, ach_name) VALUES ($1, $2)", player['user_id'], ach_name)
    
    await message.reply(f"Достижение «{ach_name}» успешно выдано игроку @{target_username}!")

# --- ЗАПУСК СЕРВЕРА ДЛЯ RENDER ---
async def main():
    await init_db()
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