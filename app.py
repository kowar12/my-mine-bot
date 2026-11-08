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
import json

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL") 
BOT_USERNAME = "game_merrymine_bot" 
CHANNEL_ID = "@merrymine_news"
CHANNEL_URL = "https://t.me/merrymine_news"

bot = Bot(token=TOKEN)
dp = Dispatcher()
db_pool = None

# Глобальные переменные для ивентов
active_golden_vein = {} 
active_drop = {} 
drop_winners = {} 

# --- СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# --- ГЕНЕРАТОР 100 ПРЕДМЕТОВ ---
def generate_loot_items():
    items = []
    common = ["Ржавый гвоздь", "Кусок угля", "Сломанная ручка", "Осколок стекла", "Старая тряпка", 
              "Кирпич", "Пустая банка", "Ржавая гайка", "Погнутая ложка", "Камень",
              "Веревка", "Кусок мыла", "Старый носок", "Пластиковая бутылка", "Обломок доски"]
    for i in range(60):
        items.append({"name": random.choice(common) + f" #{i+1}", "rarity": "Обычный", "emoji": "⚙️", "price": random.randint(5, 20)})
    
    rare = ["Старая монета", "Серебряное кольцо", "Кусок карты", "Странный амулет", "Бронзовый ключ",
            "Ржавый кинжал", "Фляга", "Осколок кристалла", "Старая книга", "Компас"]
    for i in range(25):
        items.append({"name": random.choice(rare) + f" #{i+1}", "rarity": "Редкий", "emoji": "💠", "price": random.randint(100, 300)})
        
    epic = ["Древний свиток", "Золотая статуэтка", "Кристалл душ", "Руна силы", "Корона мини-короля",
            "Перо феникса", "Глаз провидца", "Клык дракона", "Сфера тьмы", "Часы времени"]
    for i in range(10):
        items.append({"name": random.choice(epic) + f" #{i+1}", "rarity": "Эпический", "emoji": "🔮", "price": random.randint(500, 1500)})

    legendary = ["Сердце Шахты", "Меч Владыки", "Ключ от всех дверей", "Кровь Земли", "Кристалл Бесконечности"]
    for i in range(5):
        items.append({"name": legendary[i], "rarity": "Легендарный", "emoji": "👑", "price": random.randint(5000, 10000)})

    return items

LOOT_ITEMS = generate_loot_items()

# --- ЛОКАЦИИ ---
LOCATIONS = {
    "wood_mine": {
        "name": "Деревянный рудник", "required_pickaxe": "wood", "emoji": "🌲", "img": "loc_wood.jpg",
        "ores": [
            {"name": "Обычная руда", "chance": 80, "reward": 10, "emoji": "🪨", "img": "ore_cammon.jpg"},
            {"name": "Уголь", "chance": 20, "reward": 25, "emoji": "🪵", "img": "ore_coal.jpg"}
        ]
    },
    "stone_quarry": {
        "name": "Каменный карьер", "required_pickaxe": "stone", "emoji": "🪨", "img": "loc_stone.jpg",
        "ores": [
            {"name": "Уголь", "chance": 50, "reward": 25, "emoji": "🪵", "img": "ore_coal.jpg"},
            {"name": "Золото", "chance": 40, "reward": 60, "emoji": "🥇", "img": "ore_gold.jpg"},
            {"name": "Алмаз", "chance": 10, "reward": 150, "emoji": "💎", "img": "ore_daimmond.jpg"}
        ]
    },
    "abandoned_mine": {
        "name": "Заброшенная шахта (Лутбокс)", "required_pickaxe": "wood", "emoji": "📦", "img": "loc_abandoned.jpg",
        "is_lootbox": True
    },
    "crystal_cave": {
        "name": "Кристальная пещера", "required_pickaxe": "diamond", "emoji": "💎", "img": "loc_crystal.jpg",
        "ores": [
            {"name": "Алмаз", "chance": 70, "reward": 150, "emoji": "💎", "img": "ore_daimmond.jpg"},
            {"name": "Супер руда", "chance": 30, "reward": 500, "emoji": "🔮", "img": "ore_super.jpg"}
        ]
    },
    "lords_depths": {
        "name": "Глубины Владыки", "required_pickaxe": "diamond", "emoji": "👑", "img": "loc_lord.jpg",
        "ores": [
            {"name": "Супер руда", "chance": 100, "reward": 500, "emoji": "🔮", "img": "ore_super.jpg"}
        ]
    }
}

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
        DATABASE_URL, min_size=1, max_size=10, statement_cache_size=0
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
                current_status TEXT DEFAULT 'Новичок', used_promo BOOLEAN DEFAULT FALSE,
                notifications_enabled BOOLEAN DEFAULT TRUE,
                referrer_id BIGINT DEFAULT NULL,
                current_location TEXT DEFAULT 'wood_mine',
                artifacts TEXT DEFAULT '[]',
                lootbox_mines_today INTEGER DEFAULT 0,
                last_lootbox_date TEXT DEFAULT ''
            )
        """)
        cols_to_add = [
            ("active_pickaxe", "TEXT"), ("pickaxe_levels", "TEXT"), ("owned_pickaxes", "TEXT"),
            ("used_promo", "BOOLEAN DEFAULT FALSE"), ("notifications_enabled", "BOOLEAN DEFAULT TRUE"),
            ("referrer_id", "BIGINT DEFAULT NULL"), ("current_location", "TEXT DEFAULT 'wood_mine'"),
            ("artifacts", "TEXT DEFAULT '[]'"), ("lootbox_mines_today", "INTEGER DEFAULT 0"),
            ("last_lootbox_date", "TEXT DEFAULT ''")
        ]
        for col, col_type in cols_to_add:
            try: await conn.execute(f"ALTER TABLE players ADD COLUMN {col} {col_type}")
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
        [InlineKeyboardButton(text="⛏️ КОПАТЬ", callback_data="mine")],
        [InlineKeyboardButton(text="🗺️ Локации", callback_data="locations"), InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"), InlineKeyboardButton(text="🛒 Улучшить", callback_data="upgrade_menu")],
        [InlineKeyboardButton(text="🏅 Артефакты", callback_data="artifacts"), InlineKeyboardButton(text="🏆 Топы", callback_data="top_menu")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="bonus"), InlineKeyboardButton(text="👥 Позвать друга", callback_data="invite_friend")],
        [InlineKeyboardButton(text="📰 Новости бота", url=CHANNEL_URL)]
    ])

def calculate_passive_income(user_id, balance, last_seen):
    current_time = time.time()
    if last_seen == 0: return balance, current_time
    mined_offline = int((current_time - last_seen) // 60)
    if mined_offline > 480: mined_offline = 480
    return balance + mined_offline, current_time

def roll_ore(location_key):
    loc = LOCATIONS[location_key]
    ores = [o["name"] for o in loc["ores"]]
    chances = [o["chance"] for o in loc["ores"]]
    chosen_name = random.choices(ores, weights=chances, k=1)[0]
    return next(o for o in loc["ores"] if o["name"] == chosen_name)

# --- ФОНОВЫЕ ЗАДАЧИ (ИВЕНТЫ) ---
async def trigger_drop(chat_id):
    await asyncio.sleep(random.randint(3600, 10800))
    try:
        active_drop[chat_id] = time.time()
        drop_winners[chat_id] = []
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Забрать х2!", callback_data="claim_drop")]
        ])
        await bot.send_message(chat_id, "🚨 **ВНИМАНИЕ! В шахте обнаружен тайник!**\n\nПервые 3 человека, кто нажмет кнопку, получат **х2 монеты** за следующую копку! Успей!", reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass

async def trigger_golden_vein(chat_id):
    await asyncio.sleep(random.randint(7200, 14400))
    try:
        active_golden_vein[chat_id] = time.time()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 ЗОЛОТАЯ ЖИЛА! Забрать джекпот", callback_data="claim_vein")]
        ])
        await bot.send_message(chat_id, "💎 **ЗОЛОТАЯ ЖИЛА!**\n\nВ шахте обнаружен огромный самородок! Кто первый нажмет кнопку — заберет **5000 монет**!", reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start", ignore_mention=True))
async def cmd_start(message: types.Message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        chat_id = message.chat.id
        chat_title = message.chat.title or "Личный чат"
        
        args = message.text.split()
        referrer_id = None
        if len(args) > 1 and args[1].startswith("ref_"):
            try: referrer_id = int(args[1].replace("ref_", ""))
            except: pass

        is_new_chat = False
        
        async with db_pool.acquire() as conn:
            player = await conn.fetchrow("SELECT * FROM players WHERE user_id = $1", user_id)
            current_time = time.time()
            if not player:
                await conn.execute("INSERT INTO players (user_id, username, last_seen, referrer_id, balance) VALUES ($1, $2, $3, $4, 100)", user_id, username, current_time, referrer_id)
                balance, total_mined = 100, 0
                offline_text = "\n\n🎁 Тебе начислено **100 монет** на старт!"
                
                if referrer_id:
                    ref_row = await conn.fetchrow("SELECT user_id FROM players WHERE user_id = $1", referrer_id)
                    if ref_row:
                        await conn.execute("UPDATE players SET balance = balance + 500 WHERE user_id = $1", referrer_id)
                        try: await bot.send_message(referrer_id, f"🎉 По твоей ссылке зашел новый игрок! Тебе начислено 500 монет!")
                        except: pass
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
                is_new_chat = True
                await conn.execute("INSERT INTO chats (chat_id, chat_title) VALUES ($1, $2)", chat_id, chat_title)

        if is_new_chat and chat_id < 0:
            asyncio.create_task(trigger_drop(chat_id))
            asyncio.create_task(trigger_golden_vein(chat_id))

        level = get_player_level(total_mined)
        caption = (
            f"⛏️ Привет, {username}!\n\n"
            f"Добро пожаловать в «Шахту»!{offline_text}\n\n"
            f"💰 Твой баланс: {balance} монет\n"
            f"🏆 Твой уровень: {level}\n\n"
            f"👇 Жми **«⛏️ КОПАТЬ»**, чтобы добыть первую руду!"
        )

        try:
            photo = FSInputFile("start.jpg")
            await message.answer_photo(photo=photo, caption=caption, reply_markup=get_game_keyboard())
        except Exception:
            await message.reply(caption, reply_markup=get_game_keyboard())

    except Exception as e:
        await message.reply(f"Ошибка: {e}")

# --- ТЕСТОВАЯ КОМАНДА ДЛЯ ИВЕНТОВ ---
@dp.message(Command("test_event"))
async def cmd_test_event(message: types.Message):
    chat_id = message.chat.id
    if chat_id > 0:
        await message.reply("Эта команда работает только в группах!")
        return
    
    active_drop[chat_id] = time.time()
    drop_winners[chat_id] = []
    kb1 = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 Забрать х2!", callback_data="claim_drop")]])
    await message.answer("🚨 **ТЕСТ: В шахте обнаружен тайник!**\n\nПервые 3 человека получат бонус!", reply_markup=kb1, parse_mode="Markdown")
    
    active_golden_vein[chat_id] = time.time()
    kb2 = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 ЗОЛОТАЯ ЖИЛА! Забрать", callback_data="claim_vein")]])
    await message.answer("💎 **ТЕСТ: ЗОЛОТАЯ ЖИЛА!**\n\nКто первый нажмет — заберет 5000 монет!", reply_markup=kb2, parse_mode="Markdown")

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
            last_mine_time = player['last_mine_time'] or 0
            total_mined = player['total_mined'] or 0
            current_status = player['current_status'] or 'Новичок'
            current_location = player['current_location'] or 'wood_mine'
            artifacts_str = player['artifacts'] or '[]'
            try: artifacts = json.loads(artifacts_str)
            except: artifacts = []
            
            current_time = time.time()

            # ОБРАБОТКА ИВЕНТОВ
            if action == "claim_vein":
                if chat_id in active_golden_vein and (current_time - active_golden_vein[chat_id] < 300):
                    del active_golden_vein[chat_id]
                    await conn.execute("UPDATE players SET balance = balance + 5000 WHERE user_id = $1", user_id)
                    await callback.message.edit_text(f"💎 **ДЖЕКПОТ!**\n\n{callback.from_user.first_name} первым нашел золотую жилу и забрал **5000 монет**! 🎉", parse_mode="Markdown")
                else:
                    await callback.answer("Эта жила уже остыла или её забрали!", show_alert=True)
                return

            if action == "claim_drop":
                if chat_id in active_drop and (current_time - active_drop[chat_id] < 300):
                    if user_id in drop_winners[chat_id]:
                        await callback.answer("Ты уже забрал свой бонус!", show_alert=True)
                        return
                    if len(drop_winners[chat_id]) >= 3:
                        await callback.answer("Все бонусы уже разобрали!", show_alert=True)
                        return
                    
                    drop_winners[chat_id].append(user_id)
                    await conn.execute("UPDATE players SET balance = balance + 500 WHERE user_id = $1", user_id)
                    await callback.answer("🎉 Ты успел! Тебе начислено 500 монет!", show_alert=True)
                else:
                    await callback.answer("Тайник уже закрыт!", show_alert=True)
                return

            # ОБЫЧНЫЕ ДЕЙСТВИЯ
            if action == "mine":
                loc_data = LOCATIONS[current_location]
                is_lootbox = loc_data.get("is_lootbox", False)
                
                if not is_lootbox:
                    req_pickaxe = loc_data["required_pickaxe"]
                    if req_pickaxe not in owned_pickaxes:
                        await callback.answer(f"⛔ Для этой локации нужна {PICKAXES[req_pickaxe]['name']} кирка!", show_alert=True)
                        return

                cooldown = 30 if is_lootbox else 300
                if current_time - last_mine_time < cooldown:
                    wait_sec = round(cooldown - (current_time - last_mine_time), 1)
                    await callback.answer(f"⏳ Нужно подождать {wait_sec} сек.", show_alert=True)
                    return

                if is_lootbox:
                    # ПРОВЕРКА ЛИМИТА (3 РАЗА В ДЕНЬ)
                    today_date = datetime.now().strftime("%Y-%m-%d")
                    lootbox_mines = player['lootbox_mines_today'] or 0
                    last_date = player['last_lootbox_date'] or ''
                    
                    if last_date != today_date:
                        lootbox_mines = 0
                        await conn.execute("UPDATE players SET lootbox_mines_today = 0, last_lootbox_date = $1 WHERE user_id = $2", today_date, user_id)
                    
                    if lootbox_mines >= 3:
                        await callback.answer("⛔ Ты уже 3 раза копался в этой шахте сегодня! Приходи завтра.", show_alert=True)
                        return

                    await conn.execute("UPDATE players SET lootbox_mines_today = lootbox_mines + 1, last_lootbox_date = $1 WHERE user_id = $2", today_date, user_id)

                    rarity_roll = random.randint(1, 100)
                    if rarity_roll <= 60: target_rarity = "Обычный"
                    elif rarity_roll <= 85: target_rarity = "Редкий"
                    elif rarity_roll <= 95: target_rarity = "Эпический"
                    else: target_rarity = "Легендарный"

                    possible_items = [i for i in LOOT_ITEMS if i["rarity"] == target_rarity]
                    found_item = random.choice(possible_items)

                    new_balance = balance + found_item["price"]
                    artifacts.append(found_item)
                    
                    await conn.execute("UPDATE players SET balance = $1, artifacts = $2, last_mine_time = $3, total_mined = total_mined + $4 WHERE user_id = $5", 
                                       new_balance, json.dumps(artifacts), current_time, found_item["price"], user_id)

                    text = (
                        f"📦 Ты копался в заброшенной шахте и нашел: {found_item['emoji']} **{found_item['name']}**!\n"
                        f"✨ Редкость: {found_item['rarity']}\n"
                        f"💰 Стоимость: {found_item['price']} монет\n\n"
                        f"Предмет добавлен в твои Артефакты! (Найдено: {len(artifacts)} шт.)\n"
                        f"*(Осталось копок сегодня: {2 - lootbox_mines})*"
                    )
                    await callback.message.answer(text, parse_mode="Markdown", reply_markup=get_game_keyboard())

                else:
                    ore = roll_ore(current_location)
                    p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                    base_mult = PICKAXES[active_pickaxe]["base_mult"]
                    total_mult = base_mult + (p_lvl - 1) * 0.5

                    mined = int(ore["reward"] * total_mult)
                    new_balance = balance + mined
                    new_total = total_mined + mined

                    await conn.execute("UPDATE players SET balance = $1, last_mine_time = $2, total_mined = $3 WHERE user_id = $4", new_balance, current_time, new_total, user_id)
                    if chat_id < 0:
                        await conn.execute("UPDATE chats SET total_balance = total_balance + $1 WHERE chat_id = $2", mined, chat_id)

                    text = (
                        f"⛏️ {callback.from_user.first_name} накопал в {loc_data['name']}: {ore['emoji']} {ore['name']}!\n"
                        f"💰 Получено: {mined} монет (x{total_mult})\n"
                        f"💳 Баланс: {new_balance}"
                    )
                    try:
                        photo = FSInputFile(ore["img"])
                        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=get_game_keyboard())
                    except Exception:
                        await callback.message.answer(text, reply_markup=get_game_keyboard())

            elif action == "locations":
                text = f"🗺️ **Выбор локации**\n\nТекущая: {LOCATIONS[current_location]['emoji']} {LOCATIONS[current_location]['name']}\n\nДоступные:"
                buttons = []
                for loc_key, loc_data in LOCATIONS.items():
                    status = "✅ (Текущая)" if loc_key == current_location else "Перейти"
                    
                    if not loc_data.get("is_lootbox"):
                        req = loc_data["required_pickaxe"]
                        if req not in owned_pickaxes:
                            status = f"🔒 Нужна {PICKAXES[req]['name']}"
                        else:
                            status = "Перейти"
                    
                    buttons.append([InlineKeyboardButton(text=f"{loc_data['emoji']} {loc_data['name']} — {status}", callback_data=f"go_loc_{loc_key}")])
                
                buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
                await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

            elif action.startswith("go_loc_"):
                new_loc = action.replace("go_loc_", "")
                if new_loc in LOCATIONS:
                    req_pickaxe = LOCATIONS[new_loc].get("required_pickaxe")
                    if LOCATIONS[new_loc].get("is_lootbox") or req_pickaxe in owned_pickaxes:
                        await conn.execute("UPDATE players SET current_location = $1 WHERE user_id = $2", new_loc, user_id)
                        await callback.answer(f"Ты перешел в {LOCATIONS[new_loc]['name']}!")
                        await callback.message.answer("📍 Локация изменена! Жми копать.", reply_markup=get_game_keyboard())
                    else:
                        await callback.answer(f"Нужна {PICKAXES[req_pickaxe]['name']} кирка!", show_alert=True)
                return

            elif action == "artifacts":
                if not artifacts:
                    await callback.message.answer("🏅 Твой инвентарь артефактов пуст. Копай в Заброшенной шахте, чтобы найти что-то интересное!")
                    return
                
                text = f"🏅 **Твои артефакты ({len(artifacts)} шт.)**\n\n"
                for item in reversed(artifacts[-20:]):
                    text += f"{item['emoji']} {item['name']} ({item['rarity']}) — {item['price']} монет\n"
                
                if len(artifacts) > 20:
                    text += f"\n*...и еще {len(artifacts) - 20} шт.*"

                await callback.message.answer(text, parse_mode="Markdown")

            elif action == "inventory":
                p_data = PICKAXES[active_pickaxe]
                text = f"🎒 Твой инвентарь:\n\nСейчас в руках: {p_data['emoji']} {p_data['name']}\n\nВыбери кирку:"
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
                    photo = FSInputFile("inv.jpg")
                    await callback.message.answer_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
                except Exception:
                    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

            elif action.startswith("equip_"):
                new_pickaxe = action.split("_")[1]
                if new_pickaxe in owned_pickaxes:
                    await conn.execute("UPDATE players SET active_pickaxe = $1 WHERE user_id = $2", new_pickaxe, user_id)
                    await callback.answer(f"Экипирована {PICKAXES[new_pickaxe]['name']} кирка!")
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
                    await callback.answer(f"Ты купил {PICKAXES[pickaxe_id]['name']} кирку!")
                    await callback.message.answer("🎉 Кирка куплена! Она в твоем инвентаре.", reply_markup=get_game_keyboard())
                else:
                    await callback.answer(f"Нужно {price} монет!", show_alert=True)

            elif action == "upgrade_menu":
                p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                cost = get_upgrade_cost(p_lvl)
                text = f"🛒 Улучшение: {PICKAXES[active_pickaxe]['emoji']} {PICKAXES[active_pickaxe]['name']}\n\nУровень: {p_lvl}\nЦена: {cost} монет\n\nДает +0.5 к множителю!"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"Улучшить за {cost}", callback_data="do_upgrade")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
                ])
                
                try:
                    photo = FSInputFile("shop.jpg")
                    await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb)
                except Exception:
                    await callback.message.answer(text, reply_markup=kb)

            elif action == "do_upgrade":
                p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                cost = get_upgrade_cost(p_lvl)
                if balance >= cost:
                    new_balance = balance - cost
                    new_levels = update_pickaxe_level(pickaxe_levels, active_pickaxe, p_lvl + 1)
                    await conn.execute("UPDATE players SET balance = $1, pickaxe_levels = $2 WHERE user_id = $3", new_balance, new_levels, user_id)
                    
                    msg = await callback.message.answer("🔨 *Тук... тук... тук...*")
                    await asyncio.sleep(0.5)
                    await msg.edit_text("⚙️ *Искры летят в разные стороны...*")
                    await asyncio.sleep(0.5)
                    await msg.edit_text("🔥 *Кирка накаляется!*")
                    await asyncio.sleep(0.5)
                    
                    await msg.edit_text(f"🎉 **УСПЕХ!**\n\nТвоя кирка улучшена до **{p_lvl + 1} уровня**!\nТеперь она добывает руду еще эффективнее!", parse_mode="Markdown")
                    await callback.message.answer("Жми копать, чтобы проверить!", reply_markup=get_game_keyboard())
                else:
                    await callback.answer(f"Нужно {cost} монет!", show_alert=True)

            elif action == "back_to_menu":
                await callback.message.answer("Главное меню:", reply_markup=get_game_keyboard())

            elif action == "top_menu":
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top_players"), InlineKeyboardButton(text="🏰 Топ бесед", callback_data="top_chats")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
                ])
                await callback.message.answer("Выбери топ:", reply_markup=kb)

            elif action == "profile":
                level = get_player_level(total_mined)
                p_lvl = get_pickaxe_level(pickaxe_levels, active_pickaxe)
                base_mult = PICKAXES[active_pickaxe]["base_mult"]
                total_mult = base_mult + (p_lvl - 1) * 0.5
                
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")]])
                await callback.message.answer(
                    f"👤 Профиль: {callback.from_user.first_name}\n"
                    f"🎖 Статус: {current_status}\n"
                    f"🏆 Уровень: {level}\n"
                    f"📍 Локация: {LOCATIONS[current_location]['name']}\n"
                    f"💰 Баланс: {balance} монет\n"
                    f"⛏️ Кирка: {PICKAXES[active_pickaxe]['emoji']} {PICKAXES[active_pickaxe]['name']} (Ур. {p_lvl}, x{total_mult})\n"
                    f"🏅 Артефактов: {len(artifacts)} шт.", 
                    reply_markup=kb
                )

            elif action == "invite_friend":
                ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
                await callback.message.answer(f"👥 Приглашай друзей и получай 500 монет за каждого!\n\nТвоя ссылка:\n`{ref_link}`", parse_mode="Markdown")

            elif action == "bonus":
                last_bonus = player['last_bonus_time'] or 0
                if current_time - last_bonus < 86400: 
                    await callback.answer("Ты уже забирал бонус сегодня!", show_alert=True)
                else:
                    await conn.execute("UPDATE players SET balance = $1, last_bonus_time = $2 WHERE user_id = $3", balance + 100, current_time, user_id)
                    await callback.message.answer(f"🎁 Ежедневный бонус: +100 монет!", reply_markup=get_game_keyboard())

            elif action == "top_players":
                rows = await conn.fetch("SELECT username, balance, current_status FROM players ORDER BY balance DESC LIMIT 5")
                text = "🏆 Топ игроков:\n" + "\n".join([f"{i}. @{r['username']} — {r['balance']}" for i, r in enumerate(rows, 1)])
                await callback.message.answer(text)

            elif action == "top_chats":
                rows = await conn.fetch("SELECT chat_title, total_balance FROM chats ORDER BY total_balance DESC LIMIT 5")
                text = "🏰 Топ бесед:\n" + "\n".join([f"{i}. {r['chat_title']} — {r['total_balance']}" for i, r in enumerate(rows, 1)])
                await callback.message.answer(text)

    except Exception as e:
        traceback.print_exc()
        try:
            await callback.message.answer(f"⚠️ ОШИБКА:\n`{e}`", parse_mode="Markdown")
        except Exception:
            pass

async def main():
    await init_db()
    print("Бот запущен (Polling)!")
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())