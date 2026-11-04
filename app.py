import asyncio
import time
import os
import random
import asyncpg
import traceback
import threading
from datetime import datetime, timedelta
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL") 

bot = Bot(token=TOKEN)
dp = Dispatcher()
db_pool = None

# --- МИКРО-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- СПИСОК РУД ---
ORES = [
    {"name": "Обычная руда", "chance": 60, "reward": 10, "emoji": "🪨", "img": "ore_cammon.jpg"},
    {"name": "Уголь", "chance": 25, "reward": 25, "emoji": "🪵", "img": "ore_coal.jpg"},
    {"name": "Золото", "chance": 10, "reward": 60, "emoji": "🥇", "img": "ore_gold.jpg"},
    {"name": "Алмаз", "chance": 4, "reward": 150, "emoji": "💎", "img": "ore_daimmond.jpg"},
    {"name": "Супер руда", "chance": 1, "reward": 500, "emoji": "🔮", "img": "ore_super.jpg"}
]

# --- СПИСОК КИРОК ---
PICKAXES = {
    "wood": {"name": "Деревянная", "base_mult": 1, "price": 0, "emoji": "🪵", "img": "pick_wood.jpg"},
    "stone": {"name": "Каменная", "base_mult": 2, "price": 5000, "emoji": "🪨", "img": "pick_stone.jpg"},
    "gold": {"name": "Золотая", "base_mult": 3, "price": 20000, "emoji": "🥇", "img": "pick_gold.jpg"},
    "diamond": {"name": "Алмазная", "base_mult": 5, "price": 100000, "emoji": "💎", "img": "pick_diamond.jpg"}
}

# --- БАЗА ДАННЫХ ---
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL, 
        min_size=1, 
        max_size=10,
        statement_cache_size=0
    )

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id BIGINT PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 0,
                active_pickaxe TEXT DEFAULT 'wood', pickaxe_levels TEXT DEFAULT 'wood:1',
                owned_pickaxes TEXT DEFAULT 'wood',
                last_bonus_time DOUBLE PRECISION DEFAULT 0,
                last_sub_bonus_time DOUBLE PRECISION DEFAULT 0, last_mine_time DOUBLE PRECISION DEFAULT 0,
                last_seen DOUBLE PRECISION DEFAULT 0, total_mined INTEGER DEFAULT 0,
                current_status TEXT DEFAULT 'Новичок', used_promo BOOLEAN DEFAULT FALSE
            )
        """)
        for col in ["active_pickaxe", "pickaxe_levels", "owned_pickaxes"]:
            try: await conn.execute(f"ALTER TABLE players ADD COLUMN {col} TEXT")
            except asyncpg.exceptions.DuplicateColumnError: pass
        try: await conn.execute("ALTER TABLE players ADD COLUMN used_promo BOOLEAN DEFAULT FALSE")
        except asyncpg.exceptions.DuplicateColumnError: pass

        await conn.execute("CREATE TABLE IF NOT EXISTS chats (chat_id BIGINT PRIMARY KEY, chat_title TEXT, total_balance INTEGER DEFAULT 0)")
        await conn.execute("CREATE TABLE IF NOT EXISTS achievements (user_id BIGINT, ach_name TEXT, PRIMARY KEY (user_id, ach_name))")

def get_player_level(total_mined): return (total_mined // 500) + 1

def get_pickaxe_level(levels_str, pickaxe_id):
    if not levels_str: return 1
    parts = levels_str.split(',')
    for p in parts:
        if p.startswith(f"{pickaxe_id}:"):
            return int(p.split(':')[1])
    return 1

def update_pickaxe_level(levels_str, pickaxe_id, new_level):
    if not levels_str: return f"{pickaxe_id}:{new_level}"
    parts = levels_str.split(',')
    for i, p in enumerate(parts):
        if p.startswith(f"{pickaxe_id}:"):
            parts[i] = f"{pickaxe_id}:{new_level}"
            return ",".join(parts)
    return levels_str + f",{pickaxe_id}:{new_level}"

def get_upgrade_cost(level): return 100 + (level - 1) * 50

def get_game_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛏️ Копать", callback_data="mine"), InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"), InlineKeyboardButton(text="🛒 Улучшить", callback_data="upgrade_menu")],
        [InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top_players"), InlineKeyboardButton(text="🏰 Топ бесед", callback_data="top_chats")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="bonus"), InlineKeyboardButton(text="📢 Подписка", callback_data="sub_bonus")],
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

# --- ФОНОВОЕ НАПОМИНАНИЕ ---
async def remind_later(user_id):
    await asyncio.sleep(300) # Ждем 5 минут
    try:
        # Проверяем, не скопал ли он уже еще раз (по базе)
        async with db_pool.acquire() as conn:
            player = await conn.fetchrow("SELECT last_mine_time FROM players WHERE user_id = $1", user_id)
            if player:
                # Если с момента последней копки прошло 5 минут (или чуть больше)
                if time.time() - player['last_mine_time'] >= 295:
                    await bot.send_message(user_id, "⛏️ Эй, шахтер! Твоя шахта остыла. Жми «Копать», чтобы добыть новую руду!")
    except Exception as e:
        print(f"Не удалось отправить напоминание: {e}")

async def check_achievements(user_id, balance, total_mined, chat_id, message_obj):
    new_achievements = []
    async with db_pool.acquire() as conn:
        if balance >= 10:
            res = await conn.fetchrow("SELECT 1 FROM achievements WHERE user_id = $1 AND ach_name = 'Первая руда'", user_id)
            if not res:
                await conn.execute("INSERT INTO achievements (user_id, ach_name) VALUES ($1, $2)", user_id, 'Первая руда')
                new_achievements.append("⛏️ Первая руда")

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
            player = await conn.fetchrow("SELECT * FROM players WHERE user_id = $1", user_id)
            current_time = time.time()
            if not player:
                await conn.execute("INSERT INTO players (user_id, username, last_seen) VALUES ($1, $2, $3)", user_id, username, current_time)
                balance, total_mined = 0, 0
                offline_text = ""
            else:
                balance = player['balance']
                total_mined = player['total_mined']
                last_seen = player['last_seen']
                new_balance, current_time = calculate_passive_income(user_id, balance, last_seen)
                offline_mined = new_balance - balance
                balance = new_balance
                offline_text = f"\n\n💤 Пока тебя не было, шахта накопала {offline_mined} монет!" if offline_mined > 0 else ""
                await conn.execute("UPDATE players SET balance = $1, last_seen = $2 WHERE user_id = $3", balance, current_time, user_id)

            chat = await conn.fetchrow("SELECT chat_id FROM chats WHERE chat_id = $1", chat_id)
            if not chat:
                await conn.execute("INSERT INTO chats (chat_id, chat_title) VALUES ($1, $2)", chat_id, chat_title)

        level = get_player_level(total_mined)
        caption = f"⛏️ Привет, {username}!\n\nДобро пожаловать в «Шахту»!{offline_text}\n\n💰 Твой баланс: {balance} монет\n🏆 Твой уровень: {level}\n\nЖми «Копать», чтобы добывать руду!"

        try:
            photo = FSInputFile("start.jpg")
            await message.answer_photo(photo=photo, caption=caption, reply_markup=get_game_keyboard())
        except Exception:
            await message.reply(caption, reply_markup=get_game_keyboard())

    except Exception as e:
        await message.reply(f"Ошибка: {e}")

@dp.callback_query()
async def handle_buttons(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        chat_id = callback.message.chat.id
        action = callback.data

        if action is None:
            await callback.answer("Кнопка сломалась, попробуй /start", show_alert=True)
            return

        async with db_pool.acquire() as conn:
            player = await conn.fetchrow("SELECT * FROM players WHERE user_id = $1", user_id)
            if not player:
                await callback.answer("Сначала напиши /start")
                return

            balance = player['balance']
            active_pickaxe = player['active_pickaxe']
            owned_pickaxes_str = player['owned_pickaxes'] or 'wood'
            owned_pickaxes = owned_pickaxes_str.split(',')
            
            pickaxe_levels = player['pickaxe_levels'] or 'wood:1'
            last_bonus_time = player['last_bonus_time'] or 0
            last_mine_time = player['last_mine_time'] or 0
            last_seen = player['last_seen'] or 0
            total_mined = player['total_mined'] or 0
            current_status = player['current_status'] or 'Новичок'
            current_time = time.time()

            balance, current_time = calculate_passive_income(user_id, balance, last_seen)
            await conn.execute("UPDATE players SET balance = $1, last_seen = $2 WHERE user_id = $3", balance, current_time, user_id)

            mine_ready = (current_time - last_mine_time >= 300)
            bonus_ready = (current_time - last_bonus_time >= 86400)

            if action == "mine":
                if not mine_ready:
                    wait_sec = round(300 - (current_time - last_mine_time), 1)
                    await callback.answer(f"⏳ Шахта остывает! Подожди {wait_sec // 60} мин. {wait_sec % 60} сек.", show_alert=True)
                    return

                ore = roll_ore()
                p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                base_mult = PICKAXES[active_pickaxe]["base_mult"]
                total_mult = base_mult + (p_lvl - 1) * 0.5

                mined = int(ore["reward"] * total_mult)
                new_balance = balance + mined
                new_total = total_mined + mined

                await conn.execute("UPDATE players SET balance = $1, last_mine_time = $2, total_mined = $3 WHERE user_id = $4", new_balance, current_time, new_total, user_id)
                if chat_id < 0:
                    await conn.execute("UPDATE chats SET total_balance = total_balance + $1 WHERE chat_id = $2", mined, chat_id)

                await check_achievements(user_id, new_balance, new_total, chat_id, callback.message)

                text = (
                    f"⛏️ {callback.from_user.first_name} накопал: {ore['emoji']} {ore['name']}!\n"
                    f"💰 Получено: {mined} монет (x{total_mult})\n"
                    f"💳 Баланс: {new_balance}\n\n"
                    f"⏳ Следующая копка через 5 минут."
                )

                try:
                    photo = FSInputFile(ore["img"])
                    await callback.message.answer_photo(photo=photo, caption=text, reply_markup=get_game_keyboard())
                except Exception:
                    try:
                        await callback.message.answer(text, reply_markup=get_game_keyboard())
                    except Exception:
                        pass
                
                # ЗАПУСКАЕМ ТАЙМЕР НА 5 МИНУТ
                asyncio.create_task(remind_later(user_id))

            elif action == "inventory":
                p_data = PICKAXES[active_pickaxe]
                text = f"🎒 Твой инвентарь:\n\nСейчас в руках: {p_data['emoji']} {p_data['name']}\n\nВыбери кирку для копки или купи новую:"
                if mine_ready:
                    text += "\n\n✅ Шахта уже остыла, можешь копать!"

                buttons = []
                for p_id, p_data in PICKAXES.items():
                    if p_id in owned_pickaxes:
                        status = "✅ (Активна)" if p_id == active_pickaxe else "Надеть"
                        buttons.append([InlineKeyboardButton(text=f"{p_data['emoji']} {p_data['name']} — {status}", callback_data=f"equip_{p_id}")])
                    else:
                        if p_data['price'] > 0:
                            buttons.append([InlineKeyboardButton(text=f"🛒 Купить {p_data['name']} за {p_data['price']}", callback_data=f"buy_{p_id}")])

                buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])

                try:
                    photo = FSInputFile(PICKAXES[active_pickaxe]["img"])
                    await callback.message.answer_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
                except Exception:
                    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

            elif action.startswith("equip_"):
                new_pickaxe = action.split("_")[1]
                if new_pickaxe in owned_pickaxes:
                    await conn.execute("UPDATE players SET active_pickaxe = $1 WHERE user_id = $2", new_pickaxe, user_id)
                    await callback.answer(f"Ты экипировал {PICKAXES[new_pickaxe]['name']} кирку!")
                    await callback.message.answer("🎒 Инвентарь обновлен. Жми копать!", reply_markup=get_game_keyboard())
                else:
                    await callback.answer("У тебя нет этой кирки!")

            elif action.startswith("buy_"):
                pickaxe_id = action.split("_")[1]
                price = PICKAXES[pickaxe_id]["price"]
                if balance >= price:
                    new_balance = balance - price
                    new_owned = owned_pickaxes_str + f",{pickaxe_id}"
                    await conn.execute("UPDATE players SET balance = $1, owned_pickaxes = $2 WHERE user_id = $3", new_balance, new_owned, user_id)

                    text = f"🎉 Ты купил {PICKAXES[pickaxe_id]['name']} кирку!\n\nТеперь она в твоем инвентаре."
                    try:
                        photo = FSInputFile(PICKAXES[pickaxe_id]["img"])
                        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=get_game_keyboard())
                    except Exception:
                        await callback.message.answer(text, reply_markup=get_game_keyboard())
                else:
                    await callback.answer(f"Нужно {price} монет!", show_alert=True)

            elif action == "upgrade_menu":
                p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                cost = get_upgrade_cost(p_lvl)
                text = f"🛒 Улучшение кирки: {PICKAXES[active_pickaxe]['emoji']} {PICKAXES[active_pickaxe]['name']}\n\nТекущий уровень: {p_lvl}\nСтоимость улучшения: {cost} монет\n\nКаждый уровень дает +0.5 к множителю добычи!"

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"Улучшить за {cost}", callback_data="do_upgrade")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
                ])
                await callback.message.answer(text, reply_markup=kb)

            elif action == "do_upgrade":
                p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                cost = get_upgrade_cost(p_lvl)
                if balance >= cost:
                    new_balance = balance - cost
                    new_levels = update_pickaxe_level(pickaxe_levels, active_pickaxe, p_lvl + 1)
                    await conn.execute("UPDATE players SET balance = $1, pickaxe_levels = $2 WHERE user_id = $3", new_balance, new_levels, user_id)
                    await callback.answer(f"Кирка улучшена до {p_lvl + 1} уровня!", show_alert=True)
                    await callback.message.answer("Кирка улучшена! Жми копать.", reply_markup=get_game_keyboard())
                else:
                    await callback.answer(f"Нужно {cost} монет!", show_alert=True)

            elif action == "back_to_menu":
                await callback.message.answer("Главное меню:", reply_markup=get_game_keyboard())

            elif action == "profile":
                level = get_player_level(total_mined)
                p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                base_mult = PICKAXES[active_pickaxe]["base_mult"]
                total_mult = base_mult + (p_lvl - 1) * 0.5
                
                reminder = ""
                if mine_ready:
                    reminder = "\n\n🔔 **Шахта уже остыла!** Жми «Копать»."
                if bonus_ready:
                    reminder += "\n🎁 **Бонус доступен!** Забери свои 100 монет."

                await callback.message.answer(f"👤 Профиль: {callback.from_user.first_name}\n🎖 Статус: {current_status}\n🏆 Уровень: {level}\n💰 Баланс: {balance} монет\n⛏️ Кирка: {PICKAXES[active_pickaxe]['emoji']} {PICKAXES[active_pickaxe]['name']} (Ур. {p_lvl}, x{total_mult}){reminder}", parse_mode="Markdown")

            elif action == "top_players":
                rows = await conn.fetch("SELECT username, balance FROM players ORDER BY balance DESC LIMIT 5")
                text = "🏆 Топ игроков:\n" + "\n".join([f"{i}. @{r['username']} — {r['balance']} монет" for i, r in enumerate(rows, 1)])
                await callback.message.answer(text)

            elif action == "top_chats":
                rows = await conn.fetch("SELECT chat_title, total_balance FROM chats ORDER BY total_balance DESC LIMIT 5")
                text = "🏰 Топ бесед:\n" + "\n".join([f"{i}. {r['chat_title']} — {r['total_balance']} монет" for i, r in enumerate(rows, 1)])
                await callback.message.answer(text)

            elif action == "bonus":
                if not bonus_ready: 
                    wait_sec = 86400 - (current_time - last_bonus_time)
                    hours = int(wait_sec // 3600)
                    minutes = int((wait_sec % 3600) // 60)
                    await callback.answer(f"🎁 Ты уже забирал ежедневный бонус!\nПриходи через {hours} ч. {minutes} мин.", show_alert=True)
                else:
                    await conn.execute("UPDATE players SET balance = $1, last_bonus_time = $2 WHERE user_id = $3", balance + 100, current_time, user_id)
                    await callback.message.answer(f"🎁 Ежедневный бонус получен: +100 монет!", reply_markup=get_game_keyboard())

            elif action == "my_achievements":
                rows = await conn.fetch("SELECT ach_name FROM achievements WHERE user_id = $1", user_id)
                achs = [r['ach_name'] for r in rows]
                await callback.message.answer("🏅 Достижения:\n" + ("\n".join([f"• {a}" for a in achs]) if achs else "Нет"))

    except Exception as e:
        traceback.print_exc()
        try:
            await callback.message.answer(f"⚠️ ОШИБКА В КОДЕ:\n`{e}`\n\nПодробности в логах Render.", parse_mode="Markdown")
        except Exception:
            pass

# --- КОМАНДА ДЛЯ ВЫДАЧИ ДОСТИЖЕНИЙ ---
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

# --- ПРОМОКОД ---
@dp.message(Command("promo"))
async def cmd_promo(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Введи промокод: /promo СЛОВО")
        return

    promo_word = args[1].strip()
    SECRET_WORD = "ШАХТА"

    if promo_word.upper() != SECRET_WORD:
        await message.reply("Неверный промокод.")
        return

    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        player = await conn.fetchrow("SELECT used_promo FROM players WHERE user_id = $1", user_id)
        if not player:
            await message.reply("Сначала напиши /start")
            return

        if player['used_promo']:
            await message.reply("Ты уже использовал этот промокод!")
            return

        await conn.execute("UPDATE players SET balance = balance + 1000, used_promo = TRUE WHERE user_id = $1", user_id)
        await message.reply("🎉 Промокод активирован! Ты получил 1000 монет!")

# --- ЗАПУСК ---
async def main():
    await init_db()
    print("Бот запущен (Polling)!")
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())