# Часть 1: весь код до игр (включая админки и секретные команды, с исправлениями)

import asyncio
import logging
import random
import os
import re
import json
from datetime import datetime, timedelta
from contextlib import contextmanager
from aiohttp import web
import psycopg2
from psycopg2 import pool

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
    BufferedInputFile
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 7934547554
MIN_BET = 10
BOT_USERNAME = ""

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()

CACHED_ANIMATION = None

def get_cached_animation():
    global CACHED_ANIMATION
    if CACHED_ANIMATION is None and os.path.exists("red-1.mp4"):
        CACHED_ANIMATION = FSInputFile("red-1.mp4")
    return CACHED_ANIMATION

DB_POOL = None

def init_db_pool():
    global DB_POOL
    DB_POOL = pool.ThreadedConnectionPool(minconn=1, maxconn=20, dsn=DATABASE_URL)
    logging.info("Connection Pool PostgreSQL создан")

@contextmanager
def get_db():
    conn = DB_POOL.getconn()
    try:
        yield conn
    finally:
        DB_POOL.putconn(conn)

def init_db():
    init_db_pool()
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_name TEXT, last_name TEXT, username TEXT,
                    balance BIGINT DEFAULT 1000, last_bonus TEXT,
                    custom_nick TEXT,
                    is_banned BOOLEAN DEFAULT FALSE,
                    ban_until TIMESTAMP,
                    games_played INT DEFAULT 0,
                    games_won INT DEFAULT 0,
                    referrer_id BIGINT,
                    referred_count INT DEFAULT 0,
                    referal_bonus_claimed INT DEFAULT 0,
                    last_daily TEXT,
                    last_robbery TIMESTAMP,
                    vip_until TIMESTAMP,
                    btc NUMERIC DEFAULT 0,
                    clan_id INT
                )""")
            for col, col_type in [
                ("is_banned", "BOOLEAN DEFAULT FALSE"),
                ("ban_until", "TIMESTAMP"),
                ("games_played", "INT DEFAULT 0"),
                ("games_won", "INT DEFAULT 0"),
                ("referrer_id", "BIGINT"),
                ("referred_count", "INT DEFAULT 0"),
                ("referal_bonus_claimed", "INT DEFAULT 0"),
                ("last_daily", "TEXT"),
                ("custom_nick", "TEXT"),
                ("last_robbery", "TIMESTAMP"),
                ("vip_until", "TIMESTAMP"),
                ("btc", "NUMERIC DEFAULT 0"),
                ("clan_id", "INT")
            ]:
                try:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type}")
                    conn.commit()
                except psycopg2.Error:
                    conn.rollback()

            cursor.execute("CREATE TABLE IF NOT EXISTS admins (user_id BIGINT PRIMARY KEY, rank TEXT DEFAULT 'moder')")
            cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS disabled_games (game_name TEXT PRIMARY KEY)")
            cursor.execute("CREATE TABLE IF NOT EXISTS roulette_log (id SERIAL PRIMARY KEY, roll INT, color TEXT, user_id BIGINT, bet_amount BIGINT, target TEXT, win_amount BIGINT, timestamp TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS user_last_bets (user_id BIGINT PRIMARY KEY, bets_json TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS promocodes (code TEXT PRIMARY KEY, amount BIGINT NOT NULL, uses INT NOT NULL DEFAULT 1)")
            cursor.execute("CREATE TABLE IF NOT EXISTS used_promocodes (user_id BIGINT, code TEXT, PRIMARY KEY (user_id, code))")
            cursor.execute("CREATE TABLE IF NOT EXISTS secret_powers (user_id BIGINT, command_name TEXT, PRIMARY KEY (user_id, command_name))")
            cursor.execute("CREATE TABLE IF NOT EXISTS admin_log (id SERIAL PRIMARY KEY, admin_id BIGINT, action TEXT, target_id BIGINT, amount BIGINT, timestamp TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS balance_checkpoints (user_id BIGINT, balance BIGINT, checkpoint_time TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS daily_tasks (user_id BIGINT, date TEXT, task_type TEXT, progress INT DEFAULT 0, completed BOOLEAN DEFAULT FALSE, PRIMARY KEY (user_id, date, task_type))")
            cursor.execute("CREATE TABLE IF NOT EXISTS referrals (user_id BIGINT PRIMARY KEY, referrer_id BIGINT, bonus_claimed BOOLEAN DEFAULT FALSE)")
            cursor.execute("CREATE TABLE IF NOT EXISTS deposits (user_id BIGINT PRIMARY KEY, amount BIGINT, start_time TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS clans (id SERIAL PRIMARY KEY, name TEXT UNIQUE, owner_id BIGINT, balance BIGINT DEFAULT 0)")
            cursor.execute("CREATE TABLE IF NOT EXISTS clan_members (clan_id INT, user_id BIGINT, PRIMARY KEY (clan_id, user_id))")
            cursor.execute("CREATE TABLE IF NOT EXISTS cases_inventory (user_id BIGINT, case_type TEXT, count INT DEFAULT 1, PRIMARY KEY (user_id, case_type))")
            cursor.execute("CREATE TABLE IF NOT EXISTS miners (user_id BIGINT, card_type TEXT, count INT, PRIMARY KEY (user_id, card_type))")
            cursor.execute("CREATE TABLE IF NOT EXISTS mining_stats (user_id BIGINT PRIMARY KEY, accumulated_btc NUMERIC DEFAULT 0, last_update TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS snos_log (user_id BIGINT PRIMARY KEY, previous_rank TEXT, previous_balance BIGINT, timestamp TEXT)")
            conn.commit()

            cursor.execute("SELECT user_id FROM admins WHERE user_id = %s", (ADMIN_ID,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO admins (user_id, rank) VALUES (%s, 'owner')", (ADMIN_ID,))

            for key, value in [('bonus_amount', '3000'), ('bonus_cooldown', '8'), ('referral_bonus', '75000'), ('referral_limit', '25'), ('btc_rate', '1000000000')]:
                cursor.execute("INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT (key) DO NOTHING", (key, value))
            conn.commit()
    logging.info("БД инициализирована")

init_db()

# ---------- Хелперы ----------
def get_user(user_id: int, first_name: str = "", last_name: str = "", username: str = "") -> dict:
    safe_name = first_name if first_name else "Игрок"
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, first_name, last_name, username, balance, last_bonus, games_played, games_won, referrer_id, referred_count, referal_bonus_claimed, last_daily, custom_nick, last_robbery, is_banned, ban_until, vip_until, btc, clan_id FROM users WHERE user_id = %s",
                (user_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    "INSERT INTO users (user_id, first_name, last_name, username, balance, last_bonus, games_played, games_won, referal_bonus_claimed) VALUES (%s,%s,%s,%s,%s,%s,0,0,0)",
                    (user_id, safe_name, last_name or "", username or "", 4000, None))
                conn.commit()
                return {"user_id": user_id, "first_name": safe_name, "last_name": last_name or "", "username": username or "", "balance": 4000, "last_bonus": None, "games_played": 0, "games_won": 0, "referrer_id": None, "referred_count": 0, "referal_bonus_claimed": 0, "last_daily": None, "custom_nick": None, "last_robbery": None, "is_banned": False, "ban_until": None, "vip_until": None, "btc": 0, "clan_id": None}
            return {"user_id": row[0], "first_name": row[1], "last_name": row[2], "username": row[3], "balance": row[4], "last_bonus": row[5], "games_played": row[6], "games_won": row[7], "referrer_id": row[8], "referred_count": row[9], "referal_bonus_claimed": row[10], "last_daily": row[11], "custom_nick": row[12], "last_robbery": row[13], "is_banned": row[14], "ban_until": row[15], "vip_until": row[16], "btc": float(row[17]) if row[17] else 0, "clan_id": row[18]}

def update_balance(user_id: int, amount: int):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount, user_id))
            conn.commit()

def find_user_by_identifier(identifier: str):
    clean_id = identifier.replace("@", "").strip()
    with get_db() as conn:
        with conn.cursor() as cursor:
            if clean_id.isdigit():
                cursor.execute(
                    "SELECT user_id, first_name, last_name, username, balance, last_bonus, games_played, games_won, referrer_id, referred_count, referal_bonus_claimed, last_daily, custom_nick, last_robbery, is_banned, ban_until, vip_until, btc, clan_id FROM users WHERE user_id = %s",
                    (int(clean_id),))
            else:
                cursor.execute(
                    "SELECT user_id, first_name, last_name, username, balance, last_bonus, games_played, games_won, referrer_id, referred_count, referal_bonus_claimed, last_daily, custom_nick, last_robbery, is_banned, ban_until, vip_until, btc, clan_id FROM users WHERE LOWER(username) = LOWER(%s)",
                    (clean_id,))
            row = cursor.fetchone()
            if row:
                return {"user_id": row[0], "first_name": row[1], "last_name": row[2], "username": row[3], "balance": row[4], "last_bonus": row[5], "games_played": row[6], "games_won": row[7], "referrer_id": row[8], "referred_count": row[9], "referal_bonus_claimed": row[10], "last_daily": row[11], "custom_nick": row[12], "last_robbery": row[13], "is_banned": row[14], "ban_until": row[15], "vip_until": row[16], "btc": float(row[17]) if row[17] else 0, "clan_id": row[18]}
    return None

def get_rank(user_id: int) -> str:
    if user_id == ADMIN_ID:
        return "owner"
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT rank FROM admins WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            return row[0] if row else "user"

def get_rank_emoji(rank: str) -> str:
    return {"owner": "💎 Владелец", "head": "👑 Главный администратор", "admin": "🔰 Администратор", "spadmin": "👑 Special Administrator", "chatowner": "👥 Chat Owner", "moder": "⭐ Модератор"}.get(rank, "")

def is_admin(user_id: int) -> bool:
    return get_rank(user_id) in ("owner", "head", "admin", "moder", "spadmin", "chatowner")

def is_moder_or_above(user_id: int) -> bool:
    return get_rank(user_id) in ("owner", "head", "admin", "moder", "spadmin", "chatowner")

def is_admin_or_above(user_id: int) -> bool:
    return get_rank(user_id) in ("owner", "head", "admin")

def is_head_or_above(user_id: int) -> bool:
    return get_rank(user_id) in ("owner", "head")

def is_owner(user_id: int) -> bool:
    return user_id == ADMIN_ID

def has_secret_power(user_id: int, command: str) -> bool:
    if user_id == ADMIN_ID:
        return True
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM secret_powers WHERE user_id = %s AND command_name = %s", (user_id, command))
            return cursor.fetchone() is not None

def is_game_disabled(game_name: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM disabled_games WHERE game_name = %s", (game_name,))
            return cursor.fetchone() is not None

def get_setting(key: str, default: str) -> str:
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cursor.fetchone()
            return row[0] if row else default

def set_setting(key: str, value: str):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
            conn.commit()

def is_cursed(user_id: int) -> bool:
    return get_setting(f"cursed_{user_id}", "0") == "1"

def add_roulette_log(roll: int | None, color: str | None, user_id=None, bet_amount=None, target=None, win_amount=None):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO roulette_log (roll, color, user_id, bet_amount, target, win_amount, timestamp) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (roll, color, user_id, bet_amount, target, win_amount, datetime.now().isoformat()))
            if user_id is None:
                cursor.execute("DELETE FROM roulette_log WHERE roll IS NOT NULL AND id NOT IN (SELECT id FROM roulette_log WHERE roll IS NOT NULL ORDER BY id DESC LIMIT 10)")
            conn.commit()

def get_roulette_history():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT roll, color FROM roulette_log WHERE roll IS NOT NULL ORDER BY id DESC LIMIT 10")
            return [{"roll": r[0], "color": r[1]} for r in cursor.fetchall()]

def save_last_bets(user_id: int, bets: list):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO user_last_bets (user_id, bets_json) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET bets_json = EXCLUDED.bets_json", (user_id, json.dumps(bets)))
            conn.commit()

def get_last_bets(user_id: int) -> list:
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT bets_json FROM user_last_bets WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except:
                    return []
    return []

def get_promo(code: str):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT code, amount, uses FROM promocodes WHERE code = %s", (code,))
            row = cursor.fetchone()
            if row:
                return {"code": row[0], "amount": row[1], "uses": row[2]}
    return None

def use_promo(user_id: int, code: str, amount: int):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM used_promocodes WHERE user_id=%s AND code=%s", (user_id, code))
            if cursor.fetchone():
                return False
            cursor.execute("UPDATE promocodes SET uses = uses - 1 WHERE code = %s", (code,))
            cursor.execute("INSERT INTO used_promocodes (user_id, code) VALUES (%s,%s)", (user_id, code))
            cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount, user_id))
            conn.commit()
            return True

def delete_promo(code: str):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM promocodes WHERE code = %s", (code,))
            conn.commit()

def log_admin_action(admin_id: int, action: str, target_id: int = 0, amount: int = 0):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO admin_log (admin_id, action, target_id, amount, timestamp) VALUES (%s,%s,%s,%s,%s)", (admin_id, action, target_id, amount, datetime.now().isoformat()))
            conn.commit()

def save_balance_checkpoint(user_id: int):
    user = get_user(user_id)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO balance_checkpoints (user_id, balance, checkpoint_time) VALUES (%s,%s,%s)", (user_id, user['balance'], datetime.now().isoformat()))
            conn.commit()

def restore_last_checkpoint(user_id: int) -> bool:
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT balance FROM balance_checkpoints WHERE user_id=%s ORDER BY checkpoint_time DESC LIMIT 1", (user_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE users SET balance=%s WHERE user_id=%s", (row[0], user_id))
                conn.commit()
                return True
    return False

def get_mention(user_id: int, first_name: str) -> str:
    safe_name = first_name if first_name else "Игрок"
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'

def in_group(message: Message) -> bool:
    return message.chat.type in ["group", "supergroup"]

def check_group_only(message: Message, game_name: str) -> bool:
    if message.from_user:
        get_user(message.from_user.id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    if not in_group(message):
        asyncio.create_task(message.answer("🎮 Игры доступны только в групповых чатах."))
        return False
    if is_game_disabled(game_name):
        asyncio.create_task(message.answer(f"❌ Игра {game_name} отключена администратором."))
        return False
    return True

def format_balance(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₸"

def clean_first_name(first_name: str, user_id: int) -> str:
    if not first_name:
        return f"Пользователь - {user_id}"
    invisible = {"ㅤ", "ᅠ"}
    visible_found = False
    for ch in first_name:
        if ch.strip() and ch not in invisible:
            visible_found = True
            break
    if not visible_found:
        return f"Пользователь - {user_id}"
    return first_name

# ---------- VIP ----------
def get_vip_price(hours: int) -> int:
    prices = {24: 10000000, 72: 25000000, 168: 50000000, 336: 90000000, 720: 150000000}
    return prices.get(hours, 0)

def is_vip(user_id: int) -> bool:
    user = get_user(user_id)
    if user.get('vip_until'):
        if user['vip_until'] > datetime.now():
            return True
    return False

# ---------- Кланы ----------
def get_clan_by_name_or_id(identifier):
    with get_db() as conn:
        with conn.cursor() as cursor:
            if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
                cursor.execute("SELECT id, name, owner_id, balance FROM clans WHERE id = %s", (int(identifier),))
            else:
                cursor.execute("SELECT id, name, owner_id, balance FROM clans WHERE lower(name) = lower(%s)", (identifier,))
            row = cursor.fetchone()
            if row:
                return {"id": row[0], "name": row[1], "owner_id": row[2], "balance": row[3]}
    return None

def get_user_clan(user_id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT clan_id FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if row and row[0]:
                return get_clan_by_name_or_id(row[0])
    return None

# ---------- Кейсы ----------
def get_case_price(case_type: str) -> int:
    prices = {"деревянный": 10000000, "железный": 100000000, "золотой": 1000000000, "алмазный": 10000000000, "легендарный": 100000000000, "мифический": 1000000000000, "космический": 100000000000000}
    return prices.get(case_type.lower(), 0)

def get_case_reward(case_type: str):
    r = random.random()
    if case_type == "деревянный":
        if r < 0.50: return ("nothing", 0)
        elif r < 0.75: return ("money", 5000000)
        elif r < 0.90: return ("money", 15000000)
        elif r < 0.97: return ("money", 50000000)
        elif r < 0.99: return ("money", 100000000)
        else: return ("money", 1000000000)
    elif case_type == "железный":
        if r < 0.45: return ("nothing", 0)
        elif r < 0.70: return ("money", 30000000)
        elif r < 0.85: return ("money", 80000000)
        elif r < 0.95: return ("money", 200000000)
        elif r < 0.99: return ("money", 500000000)
        else: return ("vip", 6)
    elif case_type == "золотой":
        if r < 0.40: return ("nothing", 0)
        elif r < 0.65: return ("money", 100000000)
        elif r < 0.80: return ("money", 300000000)
        elif r < 0.93: return ("money", 800000000)
        elif r < 0.99: return ("money", 2000000000)
        else: return ("vip", 24)
    elif case_type == "алмазный":
        if r < 0.35: return ("nothing", 0)
        elif r < 0.60: return ("money", 500000000)
        elif r < 0.80: return ("money", 2000000000)
        elif r < 0.93: return ("money", 5000000000)
        elif r < 0.99: return ("money", 10000000000)
        else: return ("vip", 72)
    elif case_type == "легендарный":
        if r < 0.30: return ("nothing", 0)
        elif r < 0.55: return ("money", 2000000000)
        elif r < 0.75: return ("money", 10000000000)
        elif r < 0.90: return ("money", 50000000000)
        elif r < 0.99: return ("money", 100000000000)
        else: return ("vip", 168)
    elif case_type == "мифический":
        if r < 0.25: return ("nothing", 0)
        elif r < 0.50: return ("money", 10000000000)
        elif r < 0.70: return ("money", 100000000000)
        elif r < 0.85: return ("money", 500000000000)
        elif r < 0.97: return ("money", 1000000000000)
        else: return ("vip", 336)
    elif case_type == "космический":
        if r < 0.20: return ("nothing", 0)
        elif r < 0.45: return ("money", 100000000000)
        elif r < 0.70: return ("money", 1000000000000)
        elif r < 0.85: return ("money", 10000000000000)
        elif r < 0.97: return ("money", 100000000000000)
        else: return ("vip", 720)
    return ("nothing", 0)

# ---------- Биткоин ----------
def btc_to_tenge(btc_amount: float) -> int:
    rate = int(get_setting("btc_rate", "1000000000"))
    return int(btc_amount * rate)

def tenge_to_btc(tenge_amount: int) -> float:
    rate = int(get_setting("btc_rate", "1000000000"))
    return tenge_amount / rate

# ---------- Майнинг ----------
def get_mining_hash(user_id: int) -> float:
    total_hash = 0.0
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT card_type, count FROM miners WHERE user_id = %s", (user_id,))
            rows = cursor.fetchall()
    hash_rates = {"gt710": 0.00001, "rx580": 0.00005, "rtx3060": 0.0002, "rtx3080": 0.0005, "rtx3090": 0.002}
    for card_type, cnt in rows:
        total_hash += hash_rates.get(card_type, 0) * cnt
    return total_hash

def update_mining_accumulated(user_id: int):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT accumulated_btc, last_update FROM mining_stats WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("INSERT INTO mining_stats (user_id, accumulated_btc, last_update) VALUES (%s, 0, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, datetime.now()))
                conn.commit()
                return 0.0
            accumulated, last_update = row
            if last_update is None:
                last_update = datetime.now()
                cursor.execute("UPDATE mining_stats SET last_update = %s WHERE user_id = %s", (last_update, user_id))
                conn.commit()
                return accumulated or 0.0
            delta_hours = (datetime.now() - last_update).total_seconds() / 3600
            hash_rate = get_mining_hash(user_id)
            mined = hash_rate * delta_hours
            new_accumulated = float(accumulated or 0) + mined
            cursor.execute("UPDATE mining_stats SET accumulated_btc = %s, last_update = %s WHERE user_id = %s", (new_accumulated, datetime.now(), user_id))
            conn.commit()
            return new_accumulated

# ---------- Депозит ----------
def deposit_interest(start_time: datetime, amount: int) -> int:
    hours = max(0, (datetime.now() - start_time).total_seconds() // 3600)
    interest = int(amount * (0.0005 * hours))
    return interest

def can_rob(user_id: int) -> bool:
    user = get_user(user_id)
    if not user.get("last_robbery"):
        return True
    return (datetime.now() - user["last_robbery"]).total_seconds() >= 15 * 60

def perform_rob(user_id: int) -> dict:
    user = get_user(user_id)
    if user["balance"] < 500000:
        return {"success": False, "reason": "need_balance"}
    if not can_rob(user_id):
        return {"success": False, "reason": "cooldown"}
    roll = random.random()
    if roll < 0.40:
        win_amount = random.choice([100000, 125000, 150000, 175000, 200000, 225000, 250000, 275000, 300000, 325000, 350000, 375000, 400000, 425000, 450000, 475000, 500000])
        update_balance(user_id, win_amount)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE users SET last_robbery = %s WHERE user_id = %s", (datetime.now(), user_id))
                conn.commit()
        return {"success": True, "win_amount": win_amount}
    else:
        penalty = 200000
        new_balance = max(0, user["balance"] - penalty)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE users SET balance = %s, last_robbery = %s WHERE user_id = %s", (new_balance, datetime.now(), user_id))
                conn.commit()
        return {"success": False, "win_amount": 0, "penalty": penalty}

# ---------- Клавиатуры и бонус ----------
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎮 Мини-игры")],
            [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="👥 Кланы")],
            [KeyboardButton(text="📋 Команды"), KeyboardButton(text="👛 Сейф")],
            [KeyboardButton(text="📢 Новости"), KeyboardButton(text="💬 Чат")],
            [KeyboardButton(text="🛒 Донат")]
        ], resize_keyboard=True)

def get_balance_keyboard(user_data: dict, is_in_group: bool):
    b_cool = int(get_setting("bonus_cooldown", "8"))
    bonus_available = True
    if user_data.get("last_bonus"):
        try:
            last_time = datetime.fromisoformat(user_data["last_bonus"])
            if datetime.now() < last_time + timedelta(hours=b_cool):
                bonus_available = False
        except:
            pass
    if bonus_available:
        if is_in_group:
            url = f"https://t.me/{BOT_USERNAME}?start=bonus"
            return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Бонус 💰", url=url)]])
        else:
            return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Бонус 💰", callback_data="get_bonus_lc")]])
    return None

async def process_bonus_logic(user_id: int, first_name: str, is_group_context: bool):
    if is_group_context:
        return "❌ Ежедневный бонус можно получить только в личных сообщениях с ботом!"
    user = get_user(user_id, first_name)
    b_amt = int(get_setting("bonus_amount", "3000"))
    if is_vip(user_id):
        b_amt *= 2
    b_cool = int(get_setting("bonus_cooldown", "8"))
    if user.get("last_bonus"):
        try:
            last_time = datetime.fromisoformat(user["last_bonus"])
            cooldown_period = timedelta(hours=b_cool)
            if datetime.now() < last_time + cooldown_period:
                diff = (last_time + cooldown_period) - datetime.now()
                total_seconds = int(diff.total_seconds())
                hours, rem = divmod(total_seconds, 3600)
                mins, secs = divmod(rem, 60)
                time_str = f"{hours}:{mins:02d}:{secs:02d}"
                return f"⏳ Бонус уже получен.\n\nСледующий бонус через\n\n{time_str}"
        except:
            pass
    update_balance(user_id, b_amt)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET last_bonus = %s WHERE user_id = %s", (datetime.now().isoformat(), user_id))
            conn.commit()
    mention = get_mention(user_id, user['first_name'])
    updated_user = get_user(user_id)
    return f"🎁 {mention} получил бонус <b>{format_balance(b_amt)}</b> 💰!\n\n👤 {mention}\n💰 Баланс: {format_balance(updated_user['balance'])}"

# ---------- Основные команды ----------
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    fname = message.from_user.first_name or ""
    get_user(user_id, fname, message.from_user.last_name or "", message.from_user.username or "")
    args = message.text.split()
    if len(args) > 1 and args[1] == "bonus":
        if in_group(message):
            await message.answer("❌ Получить бонус можно только в личных сообщениях.")
            return
        result_text = await process_bonus_logic(user_id, fname, False)
        await message.answer(result_text)
        return
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            ref_id = int(args[1].replace("ref", ""))
            if ref_id != user_id:
                with get_db() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT 1 FROM referrals WHERE user_id = %s", (user_id,))
                        if not cursor.fetchone():
                            cursor.execute("INSERT INTO referrals (user_id, referrer_id) VALUES (%s, %s)", (user_id, ref_id))
                            conn.commit()
                            ref_user = get_user(ref_id)
                            if ref_user and ref_user.get('referal_bonus_claimed', 0) < int(get_setting('referral_limit', '25')):
                                bonus = int(get_setting('referral_bonus', '75000'))
                                update_balance(ref_id, bonus)
                                cursor.execute("UPDATE users SET referred_count = referred_count + 1, referal_bonus_claimed = referal_bonus_claimed + 1 WHERE user_id = %s", (ref_id,))
                                conn.commit()
        except:
            pass
    if in_group(message):
        await message.answer("Привет! Я бот Kaspi Red. Напиши мне в личные сообщения для главного меню.")
        return
    welcome_text = (
        "👋 Добро пожаловать в Kaspi Red!\n\n"
        "🎰 Игры: Рулетка, Джокер, Мины, Дуэли, Фортуна\n"
        "💎 Валюта: ₸ (тенге) и BTC\n"
        "💰 Начальный баланс: 4 000 ₸\n\n"
        "👨‍💻 Связь с разработчиком: @se7ze\n\n"
        "Используй кнопки ниже для навигации."
    )
    await message.answer(welcome_text, reply_markup=get_main_menu())

@router.message(F.text == "📢 Новости")
async def cmd_news_btn(message: Message):
    await message.answer("Подпишись на канал: https://t.me/kaspired_game")

@router.message(F.text == "💬 Чат")
async def cmd_chat_btn(message: Message):
    await message.answer("Общий чат: https://t.me/kaspired_chat")

@router.message(F.text == "💬 Чаты")
async def cmd_chats(message: Message):
    await message.answer("Чаты:\n1: https://t.me/kaspired_chat\n2: https://t.me/kaspired_chat_2")

@router.message(F.text == "🛒 Донат")
async def cmd_donate(message: Message):
    await message.answer("Для покупки тенге или VIP пиши @se7ze.")

@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📋 <b>Справка по Kaspi Red</b>\n\n"
        "• [ставка] [объекты...] — Рулетка\n"
        "• лог — История рулетки\n"
        "• ставки — Текущие ставки\n"
        "• джокер [ставка] — Джокер\n"
        "• мины [ставка] — Минное поле\n"
        "• дуэль [ставка] — Дуэль (ответом на сообщение)\n"
        "• coinflip [ставка] — Орёл/Решка\n"
        "• фортуна [ставка] — Слоты\n"
        "• п [сумма] [@user] [коммент] — Перевод\n"
        "• б / баланс — Баланс\n"
        "• /name [новое_имя] — Сменить игровой ник\n"
        "• /vip — VIP-статус\n"
        "• банк — Депозит\n"
        "• депозит [сумма] — Открыть депозит (мин 5 000 000)\n"
        "• снять депозит — Забрать депозит\n"
        "• ограбить — Ограбить казино (треб. 500 000)\n"
        "• сейф — Посмотреть кейсы и BTC\n"
        "• майнинг — Майнинг ферма\n"
        "• клан — Кланы\n"
        "• /top — Топ игроков\n"
        "• /promo [код] — Промокод\n"
        "• /daily — Ежедневное задание\n"
        "• /referral — Реферальная ссылка"
    )
    await message.answer(text)

@router.message(F.text == "🎮 Мини-игры")
async def cmd_minigames(message: Message):
    text = (
        "🎮 <b>Мини-игры</b>\n\n"
        "🎰 Рулетка: ставь на числа и цвета.\n"
        "🃏 Джокер: открывай карты, избегай скелетов.\n"
        "💣 Мины: сапёр с множителями (7 бомб).\n"
        "⚔️ Дуэли: камень-ножницы-бумага.\n"
        "🪙 Coinflip: орёл или решка.\n"
        "🎰 Фортуна: слоты с множителями."
    )
    await message.answer(text)

@router.message(F.text == "👥 Кланы")
async def cmd_clan_btn(message: Message):
    await clan_cmd(message)

@router.message(F.text == "👛 Сейф")
async def cmd_safe_btn(message: Message):
    await safe_cmd(message)

@router.message(F.text == "📋 Команды")
async def cmd_commands_btn(message: Message):
    await cmd_help(message)

@router.message(Command("balance"))
async def cmd_balance(message: Message):
    user_id = message.from_user.id
    user = get_user(user_id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    rank = get_rank(user_id)
    rank_str = get_rank_emoji(rank)
    mention = get_mention(user_id, user['first_name'])
    text = f"👤 Профиль {mention}\n"
    if rank_str:
        text += f"{rank_str}\n"
    if user.get('custom_nick'):
        text += f"🏷️ {user['custom_nick']}\n"
    if is_vip(user_id):
        text += "⚜️ VIP\n"
    text += f"💰 Баланс: {format_balance(user['balance'])}\n"
    if user.get('btc') and user['btc'] > 0:
        text += f"💎 BTC: {user['btc']:.4f}\n"
    text += f"🎮 Игр: {user['games_played']} | Побед: {user['games_won']}\n"
    if user.get('referrer_id'):
        ref_user = get_user(user['referrer_id'])
        text += f"👥 Пригласил: {get_mention(user['referrer_id'], ref_user['first_name'])}\n"
    kb = get_balance_keyboard(user, in_group(message))
    await message.answer(text, reply_markup=kb)

@router.message(F.text.lower().in_(["б", "баланс", "👤 профиль"]))
async def cmd_balance_text(message: Message):
    await cmd_balance(message)

@router.callback_query(F.data == "get_bonus_lc")
async def callback_bonus_lc(callback: CallbackQuery):
    await callback.answer()
    result = await process_bonus_logic(callback.from_user.id, callback.from_user.first_name or "", False)
    await callback.message.answer(result)

@router.message(Command("top"))
async def cmd_top(message: Message):
    limit = 10
    args = message.text.split()
    if len(args) > 1 and args[1].isdigit():
        limit = int(args[1])
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id, first_name, balance FROM users ORDER BY balance DESC LIMIT %s", (limit,))
            rows = cursor.fetchall()
    if not rows:
        await message.answer("🏆 Топ пуст.")
        return
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    text = f"🏆 <b>Топ {len(rows)} игроков:</b>\n\n"
    for i, row in enumerate(rows, 1):
        user_id, first_name, balance = row
        display_name = clean_first_name(first_name, user_id)
        mention = f'<a href="tg://user?id={user_id}">{display_name}</a>'
        medal = medals.get(i, "")
        text += f"{medal} {i}. {mention} — <b>{format_balance(balance)}</b>\n"
    await message.answer(text)

@router.message(F.text == "🏆 Топ")
async def cmd_top_btn(message: Message):
    await cmd_top(message)

@router.message(Command("bonus"))
async def cmd_bonus(message: Message):
    res = await process_bonus_logic(message.from_user.id, message.from_user.first_name or "", in_group(message))
    await message.answer(res)

@router.message(F.text.lower() == "бонус")
async def cmd_bonus_text(message: Message):
    await cmd_bonus(message)

@router.message(Command("daily"))
async def cmd_daily(message: Message):
    if in_group(message): return
    user_id = message.from_user.id
    user = get_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if user.get('last_daily') == today:
        await message.answer("✅ Вы уже получили ежедневное задание сегодня.")
        return
    tasks = [
        {"type": "roulette_play", "desc": "Сыграйте в рулетку 3 раза", "target": 3, "reward": 500},
        {"type": "joker_play", "desc": "Сыграйте в Джокера 2 раза", "target": 2, "reward": 400},
        {"type": "win_game", "desc": "Выиграйте 1 раз в любую игру", "target": 1, "reward": 600}
    ]
    task = random.choice(tasks)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO daily_tasks (user_id, date, task_type) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (user_id, today, task['type']))
            cursor.execute("UPDATE users SET last_daily = %s WHERE user_id = %s", (today, user_id))
            conn.commit()
    text = f"📋 <b>Ежедневное задание:</b>\n\n{task['desc']}\n\n🎁 Награда: <b>{format_balance(task['reward'])}</b>\n\nПрогресс: 0/{task['target']}"
    await message.answer(text)

@router.message(Command("referral"))
async def cmd_referral(message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"
    bonus = int(get_setting('referral_bonus', '75000'))
    limit = int(get_setting('referral_limit', '25'))
    claimed = user.get('referal_bonus_claimed', 0)
    text = (
        f"👥 <b>Реферальная система</b>\n\n"
        f"Ваша ссылка: {ref_link}\n"
        f"Приглашено: <b>{user['referred_count']}</b>\n"
        f"Бонус за каждого: <b>{format_balance(bonus)}</b>\n"
        f"Лимит бонусов: <b>{claimed}/{limit}</b>\n\n"
        f"Отправьте ссылку друзьям!"
    )
    await message.answer(text)

@router.message(Command("promo"))
async def cmd_promo(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Использование: /promo [код]")
        return
    code = args[1]
    promo = get_promo(code)
    if not promo:
        await message.answer("❌ Промокод не найден.")
        return
    if promo['uses'] <= 0:
        await message.answer("❌ Лимит использований промокода исчерпан.")
        return
    user_id = message.from_user.id
    if use_promo(user_id, code, promo['amount']):
        await message.answer(f"✅ Промокод активирован! Начислено <b>{format_balance(promo['amount'])}</b>.")
    else:
        await message.answer("❌ Вы уже активировали этот промокод.")

@router.message(F.text.lower().startswith("п "))
async def cmd_transfer(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit(): return
    amount = int(parts[1])
    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше 0.")
        return
    user = get_user(message.from_user.id)
    if user['balance'] < amount:
        await message.answer("❌ Недостаточно средств.")
        return
    target = None
    comment = ""
    if message.reply_to_message and message.reply_to_message.from_user:
        target = get_user(message.reply_to_message.from_user.id)
        if len(parts) > 2: comment = " ".join(parts[2:])
    elif len(parts) > 2:
        target = find_user_by_identifier(parts[2])
        if len(parts) > 3: comment = " ".join(parts[3:])
    if not target:
        await message.answer("❌ Получатель не найден.")
        return
    if target['user_id'] == message.from_user.id:
        await message.answer("❌ Нельзя перевести самому себе.")
        return
    update_balance(user['user_id'], -amount)
    update_balance(target['user_id'], amount)
    mention_from = get_mention(user['user_id'], user['first_name'])
    mention_to = get_mention(target['user_id'], target['first_name'])
    msg = f"💸 {mention_from} перевел {mention_to} <b>{format_balance(amount)}</b>."
    if comment: msg += f"\n💬 Комментарий: <i>{comment}</i>"
    await message.answer(msg)

# ---------- Смена ника ----------
@router.message(Command("name"))
async def cmd_change_name(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /name [новое_имя]")
        return
    new_nick = args[1].strip()
    if not new_nick:
        await message.answer("❌ Ник не может быть пустым.")
        return
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET custom_nick = %s WHERE user_id = %s", (new_nick, message.from_user.id))
            conn.commit()
    await message.answer(f"✅ Ваш игровой ник установлен: 🏷️ {new_nick}")

# ---------- VIP команда ----------
@router.message(Command("vip"))
async def cmd_vip(message: Message):
    user = get_user(message.from_user.id)
    if is_vip(message.from_user.id):
        until = user['vip_until'].strftime('%d.%m.%Y %H:%M')
        await message.answer(f"👑 VIP-статус\n⚜️ Ты VIP\n📅 До {until}\n🎁 Бонус x2 действует")
    else:
        await message.answer("👑 VIP-статус\nУ тебя нет VIP.\nКупить: vip_buy [часы]\n24 часа — 10 000 000 ₸\n72 часа — 25 000 000 ₸\n168 часов — 50 000 000 ₸\n336 часов — 90 000 000 ₸\n720 часов — 150 000 000 ₸")

@router.message(Command("vip_buy"))
async def cmd_vip_buy(message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /vip_buy [часы]")
        return
    hours = int(args[1])
    price = get_vip_price(hours)
    if price == 0:
        await message.answer("Неверный срок.")
        return
    user = get_user(message.from_user.id)
    if user['balance'] < price:
        await message.answer("Недостаточно средств.")
        return
    update_balance(message.from_user.id, -price)
    current_until = user.get('vip_until')
    if current_until and current_until > datetime.now():
        new_until = current_until + timedelta(hours=hours)
    else:
        new_until = datetime.now() + timedelta(hours=hours)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET vip_until = %s WHERE user_id = %s", (new_until, message.from_user.id))
            conn.commit()
    await message.answer(f"✅ VIP активирован до {new_until.strftime('%d.%m.%Y %H:%M')}")

# ---------- Кланы ----------
@router.message(F.text.lower().in_(["clan", "клан"]))
async def clan_cmd(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "🏰 <b>Кланы</b>\n\n"
            "Создать клан: clan создать <название> (англ., 100 млн ₸)\n"
            "Вступить: clan вступить <название или id>\n"
            "Выйти: clan выйти\n"
            "Инфо: clan инфо\n"
            "Пополнить казну: clan казна <сумма>\n"
            "Снять из казны (владелец): clan снять <сумма>\n"
            "Топ кланов: clan топ"
        )
        return
    sub = args[1].lower()
    if sub == "создать":
        if len(args) < 3:
            await message.answer("Укажи название клана (только английские буквы).")
            return
        name = args[2]
        if not re.match("^[a-zA-Z0-9_]+$", name):
            await message.answer("Название должно быть на английском, без пробелов.")
            return
        if get_user_clan(message.from_user.id):
            await message.answer("Ты уже в клане.")
            return
        price = 100000000
        user = get_user(message.from_user.id)
        if user['balance'] < price:
            await message.answer(f"Нужно {format_balance(price)} для создания клана.")
            return
        update_balance(message.from_user.id, -price)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO clans (name, owner_id, balance) VALUES (%s, %s, 0) RETURNING id", (name, message.from_user.id))
                clan_id = cursor.fetchone()[0]
                cursor.execute("INSERT INTO clan_members (clan_id, user_id) VALUES (%s, %s)", (clan_id, message.from_user.id))
                cursor.execute("UPDATE users SET clan_id = %s WHERE user_id = %s", (clan_id, message.from_user.id))
                conn.commit()
        await message.answer(f"🏰 Клан '{name}' создан! ID: {clan_id}")
    elif sub == "вступить":
        if len(args) < 3:
            await message.answer("Укажи название или id клана.")
            return
        identifier = args[2]
        clan = get_clan_by_name_or_id(identifier)
        if not clan:
            await message.answer("Клан не найден.")
            return
        if get_user_clan(message.from_user.id):
            await message.answer("Ты уже в клане.")
            return
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO clan_members (clan_id, user_id) VALUES (%s, %s)", (clan['id'], message.from_user.id))
                cursor.execute("UPDATE users SET clan_id = %s WHERE user_id = %s", (clan['id'], message.from_user.id))
                conn.commit()
        await message.answer(f"Ты вступил в клан {clan['name']}.")
    elif sub == "выйти":
        clan = get_user_clan(message.from_user.id)
        if not clan:
            await message.answer("Ты не в клане.")
            return
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM clan_members WHERE clan_id = %s AND user_id = %s", (clan['id'], message.from_user.id))
                cursor.execute("UPDATE users SET clan_id = NULL WHERE user_id = %s", (message.from_user.id,))
                conn.commit()
        await message.answer("Ты вышел из клана.")
    elif sub == "инфо":
        clan = get_user_clan(message.from_user.id)
        if not clan:
            await message.answer("Ты не в клане.")
            return
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM clan_members WHERE clan_id = %s", (clan['id'],))
                cnt = cursor.fetchone()[0]
        await message.answer(f"🏰 {clan['name']} (ID: {clan['id']})\n👑 Владелец: {get_mention(clan['owner_id'], get_user(clan['owner_id'])['first_name'])}\n👥 Участников: {cnt}\n💰 Казна: {format_balance(clan['balance'])}")
    elif sub == "казна":
        if len(args) < 3 or not args[2].isdigit():
            await message.answer("Укажи сумму: clan казна [сумма]")
            return
        amount = int(args[2])
        clan = get_user_clan(message.from_user.id)
        if not clan:
            await message.answer("Ты не в клане.")
            return
        user = get_user(message.from_user.id)
        if user['balance'] < amount:
            await message.answer("Недостаточно средств.")
            return
        update_balance(message.from_user.id, -amount)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE clans SET balance = balance + %s WHERE id = %s", (amount, clan['id']))
                conn.commit()
        await message.answer(f"Казна пополнена на {format_balance(amount)}.")
    elif sub == "снять":
        if len(args) < 3 or not args[2].isdigit():
            await message.answer("Укажи сумму: clan снять [сумма]")
            return
        amount = int(args[2])
        clan = get_user_clan(message.from_user.id)
        if not clan or clan['owner_id'] != message.from_user.id:
            await message.answer("Только владелец клана может снимать.")
            return
        if clan['balance'] < amount:
            await message.answer("В казне недостаточно.")
            return
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE clans SET balance = balance - %s WHERE id = %s", (amount, clan['id']))
                cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount, message.from_user.id))
                conn.commit()
        await message.answer(f"Снято {format_balance(amount)}.")
    elif sub == "топ":
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, name, balance FROM clans ORDER BY balance DESC LIMIT 10")
                rows = cursor.fetchall()
        if not rows:
            await message.answer("Кланов пока нет.")
            return
        text = "🏆 Топ кланов:\n\n"
        for i, r in enumerate(rows, 1):
            text += f"{i}. {r[1]} — {format_balance(r[2])}\n"
        await message.answer(text)

# ---------- Сейф и кейсы ----------
@router.message(F.text.lower().in_(["сейф", "safe"]))
async def safe_cmd(message: Message):
    user = get_user(message.from_user.id)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT case_type, count FROM cases_inventory WHERE user_id = %s", (message.from_user.id,))
            cases = cursor.fetchall()
    text = "👛 Твой сейф\n"
    if cases:
        for c in cases:
            text += f"📦 {c[0]} ×{c[1]}\n"
    else:
        text += "Кейсов нет.\n"
    btc = user.get('btc', 0)
    if btc:
        text += f"💎 BTC: {btc:.4f}\n"
    await message.answer(text)

@router.message(F.text.lower().in_(["кейс", "case"]))
async def case_cmd(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "📦 <b>Кейсы</b>\n\n"
            "Купить: кейс купить &lt;тип&gt;\n"
            "Открыть: кейс открыть &lt;тип&gt;\n"
            "Список кейсов:\n"
            "• деревянный — 10 млн\n"
            "• железный — 100 млн\n"
            "• золотой — 1 млрд\n"
            "• алмазный — 10 млрд\n"
            "• легендарный — 100 млрд\n"
            "• мифический — 1 трлн\n"
            "• космический — 100 трлн"
        )
        return
    sub = args[1].lower()
    if sub == "купить":
        if len(args) < 3:
            await message.answer("Укажи тип кейса. Доступные: деревянный, железный, золотой, алмазный, легендарный, мифический, космический")
            return
        case_type = args[2].lower()
        price = get_case_price(case_type)
        if price == 0:
            await message.answer("Неизвестный тип кейса.")
            return
        user = get_user(message.from_user.id)
        if user['balance'] < price:
            await message.answer("Недостаточно средств.")
            return
        update_balance(message.from_user.id, -price)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO cases_inventory (user_id, case_type, count) VALUES (%s,%s,1) "
                    "ON CONFLICT (user_id, case_type) DO UPDATE SET count = count + 1",
                    (message.from_user.id, case_type)
                )
                conn.commit()
        await message.answer(f"Кейс '{case_type}' куплен и положен в сейф.")
    elif sub == "открыть":
        if len(args) < 3:
            await message.answer("Укажи тип кейса.")
            return
        case_type = args[2].lower()
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT count FROM cases_inventory WHERE user_id = %s AND case_type = %s", (message.from_user.id, case_type))
                row = cursor.fetchone()
                if not row or row[0] <= 0:
                    await message.answer("У тебя нет такого кейса.")
                    return
                if row[0] == 1:
                    cursor.execute("DELETE FROM cases_inventory WHERE user_id = %s AND case_type = %s", (message.from_user.id, case_type))
                else:
                    cursor.execute("UPDATE cases_inventory SET count = count - 1 WHERE user_id = %s AND case_type = %s", (message.from_user.id, case_type))
                conn.commit()
        reward_type, reward_value = get_case_reward(case_type)
        if reward_type == "nothing":
            await message.answer("Открыл кейс... пусто.")
        elif reward_type == "money":
            update_balance(message.from_user.id, reward_value)
            await message.answer(f"Открыл кейс... выпало {format_balance(reward_value)}!")
        elif reward_type == "vip":
            hours = reward_value
            user = get_user(message.from_user.id)
            current_until = user.get('vip_until')
            if current_until and current_until > datetime.now():
                new_until = current_until + timedelta(hours=hours)
            else:
                new_until = datetime.now() + timedelta(hours=hours)
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE users SET vip_until = %s WHERE user_id = %s", (new_until, message.from_user.id))
                    conn.commit()
            await message.answer(f"Открыл кейс... выпал VIP на {hours} часов!")
        else:
            await message.answer("Что-то пошло не так.")
    else:
        await message.answer("Неизвестная подкоманда. Используй: купить или открыть")

# ---------- Биткоин и майнинг ----------
@router.message(F.text.lower().in_(["btc", "биткоин"]))
async def btc_cmd(message: Message):
    user = get_user(message.from_user.id)
    rate = int(get_setting("btc_rate", "1000000000"))
    await message.answer(f"Курс BTC: 1 BTC = {format_balance(rate)}\nТвой BTC: {user['btc']:.4f}")

@router.message(F.text.lower().in_(["майнинг", "mining"]))
async def mining_cmd(message: Message):
    args = message.text.split()
    if len(args) < 2:
        accumulated = update_mining_accumulated(message.from_user.id)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT card_type, count FROM miners WHERE user_id = %s", (message.from_user.id,))
                rows = cursor.fetchall()
        if not rows:
            await message.answer(
                "⛏ У тебя нет видеокарт.\n"
                "Купи через команду:\n"
                "майнинг купить &lt;тип&gt; &lt;кол-во&gt;\n\n"
                "Доступные типы:\n"
                "• gt710 — 1 000 000 ₸ (0.00001 BTC/час)\n"
                "• rx580 — 10 000 000 ₸ (0.00005 BTC/час)\n"
                "• rtx3060 — 100 000 000 ₸ (0.0002 BTC/час)\n"
                "• rtx3080 — 500 000 000 ₸ (0.0005 BTC/час)\n"
                "• rtx3090 — 2 000 000 000 ₸ (0.002 BTC/час)"
            )
            return
        total_hash = get_mining_hash(message.from_user.id)
        text = "⛏ <b>Твоя майнинг ферма</b>\n\n"
        hash_rates = {"gt710": 0.00001, "rx580": 0.00005, "rtx3060": 0.0002, "rtx3080": 0.0005, "rtx3090": 0.002}
        for card_type, cnt in rows:
            rate = hash_rates.get(card_type, 0)
            text += f"• {card_type} ×{cnt}: {rate * cnt:.8f} BTC/час\n"
        text += f"\nВсего: {total_hash:.8f} BTC/час\n"
        text += f"Накоплено: {accumulated:.8f} BTC\n"
        text += "Забрать: <b>забрать</b>"
        await message.answer(text)
        return
    if args[1].lower() in ["купить", "buy"]:
        if len(args) < 4:
            await message.answer("❌ Использование: майнинг купить &lt;тип&gt; &lt;кол-во&gt;")
            return
        card_type = args[2].lower()
        try:
            count = int(args[3])
        except ValueError:
            await message.answer("❌ Количество должно быть числом.")
            return
        prices = {"gt710": 1000000, "rx580": 10000000, "rtx3060": 100000000, "rtx3080": 500000000, "rtx3090": 2000000000}
        if card_type not in prices:
            await message.answer("❌ Неизвестный тип карты.\nДоступно: gt710, rx580, rtx3060, rtx3080, rtx3090")
            return
        if count < 1:
            await message.answer("❌ Количество должно быть больше 0.")
            return
        total_cost = prices[card_type] * count
        user = get_user(message.from_user.id)
        if user['balance'] < total_cost:
            await message.answer(f"❌ Недостаточно средств. Нужно {format_balance(total_cost)}")
            return
        update_balance(message.from_user.id, -total_cost)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO miners (user_id, card_type, count) VALUES (%s,%s,%s) "
                    "ON CONFLICT (user_id, card_type) DO UPDATE SET count = count + %s",
                    (message.from_user.id, card_type, count, count)
                )
                conn.commit()
        await message.answer(f"✅ Куплено {count} шт. {card_type}.\nСписано: {format_balance(total_cost)}")
        return
    await message.answer("❌ Неизвестная команда.\nИспользуй:\nмайнинг — посмотреть ферму\nмайнинг купить &lt;тип&gt; &lt;кол-во&gt;")

@router.message(F.text.lower().in_(["забрать", "collect"]))
async def collect_mining(message: Message):
    accumulated = update_mining_accumulated(message.from_user.id)
    if accumulated <= 0:
        await message.answer("Нет накопленного BTC.")
        return
    btc_amount = float(accumulated)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE mining_stats SET accumulated_btc = 0 WHERE user_id = %s", (message.from_user.id,))
            cursor.execute("UPDATE users SET btc = btc + %s WHERE user_id = %s", (btc_amount, message.from_user.id))
            conn.commit()
    await message.answer(f"Забрано {btc_amount:.8f} BTC.")

@router.message(F.text.lower().in_(["продать", "sell"]))
async def sell_btc(message: Message):
    args = message.text.split()
    user = get_user(message.from_user.id)
    if len(args) > 1 and args[1].lower() in ["всё", "все", "all"]:
        btc_amount = user['btc']
        if btc_amount <= 0:
            await message.answer("У тебя нет BTC.")
            return
    else:
        if len(args) < 2 or not args[1].isdigit():
            await message.answer("Использование: продать &lt;кол-во BTC&gt; или продать всё")
            return
        btc_amount = float(args[1])
    if user['btc'] < btc_amount:
        await message.answer("У тебя недостаточно BTC.")
        return
    rate = int(get_setting("btc_rate", "1000000000"))
    tenge_amount = int(btc_amount * rate)
    commission = int(tenge_amount * 0.02)
    receive = tenge_amount - commission
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET btc = btc - %s, balance = balance + %s WHERE user_id = %s", (btc_amount, receive, message.from_user.id))
            conn.commit()
    await message.answer(f"Ты продал {btc_amount:.4f} BTC за {format_balance(receive)} (комиссия {format_balance(commission)}).")

# ---------- Банк / Депозит ----------
@router.message(F.text.lower() == "банк")
async def bank_info(message: Message):
    user_id = message.from_user.id
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT amount, start_time FROM deposits WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
    if not row:
        await message.answer("🏦 У вас нет открытого депозита.\nЧтобы открыть, введите: депозит [сумма]\nМинимальная сумма: 5 000 000 ₸")
        return
    amount, start_time = row
    interest = deposit_interest(start_time, amount)
    total = amount + interest
    await message.answer(
        f"🏦 Ваш депозит:\n"
        f"💰 Сумма: {format_balance(amount)}\n"
        f"📈 Начислено процентов: {format_balance(interest)}\n"
        f"💎 Итого: {format_balance(total)}\n"
        f"Комиссия за снятие: 2%\n"
        f"Чтобы снять: снять депозит"
    )

@router.message(F.text.lower().startswith("депозит"))
async def deposit_open(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: депозит [сумма]\nМинимальная сумма: 5 000 000 ₸")
        return
    amount = int(parts[1])
    if amount < 5000000:
        await message.answer("❌ Минимальная сумма для депозита: 5 000 000 ₸")
        return
    user = get_user(message.from_user.id)
    if user['balance'] < amount:
        await message.answer("❌ Недостаточно средств.")
        return
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM deposits WHERE user_id = %s", (message.from_user.id,))
            if cursor.fetchone():
                await message.answer("❌ У вас уже есть открытый депозит. Сначала снимите его.")
                return
            update_balance(message.from_user.id, -amount)
            cursor.execute("INSERT INTO deposits (user_id, amount, start_time) VALUES (%s, %s, %s)", (message.from_user.id, amount, datetime.now()))
            conn.commit()
    await message.answer(f"✅ Депозит открыт на сумму {format_balance(amount)}.\nПроценты начисляются каждый час (0.05%).")

@router.message(F.text.lower() == "снять депозит")
async def deposit_withdraw(message: Message):
    user_id = message.from_user.id
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT amount, start_time FROM deposits WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                await message.answer("❌ У вас нет открытого депозита.")
                return
            amount, start_time = row
            interest = deposit_interest(start_time, amount)
            total = amount + interest
            commission = int(total * 0.02)
            to_balance = total - commission
            cursor.execute("DELETE FROM deposits WHERE user_id = %s", (user_id,))
            cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (to_balance, user_id))
            conn.commit()
    await message.answer(
        f"✅ Депозит закрыт.\n"
        f"Сумма: {format_balance(amount)}\n"
        f"Проценты: {format_balance(interest)}\n"
        f"Комиссия (2%): {format_balance(commission)}\n"
        f"Зачислено на баланс: {format_balance(to_balance)}"
    )

# ---------- Ограбление ----------
@router.message(F.text.lower() == "ограбить")
async def rob_cmd(message: Message):
    if not check_group_only(message, "ограбление"):
        return
    result = perform_rob(message.from_user.id)
    if not result["success"]:
        if result.get("reason") == "need_balance":
            await message.answer("❌ Для ограбления нужно минимум 500 000 ₸.")
        elif result.get("reason") == "cooldown":
            await message.answer("❌ Ограбление доступно раз в 15 минут.")
        else:
            await message.answer(f"❌ Ограбление не удалось. Штраф: {format_balance(result.get('penalty', 0))}")
    else:
        await message.answer(f"🎉 Ограбление успешно! Ты украл {format_balance(result['win_amount'])}.")

# ---------- Админ-команды и секретные (полный набор возьмите из предыдущих сообщений) ----------
# Здесь должны идти все админ-команды и секретные команды из вашего файла.
# Они не изменились, кроме экранирования угловых скобок в текстах.
# Скопируйте их сюда.

# ==================== ИГРЫ ====================
# (следующая часть)
# ==================== ИГРЫ ====================
# ---------- Джокер ----------
joker_sessions = {}
JOKER_MULTIS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0]

@router.message(F.text.lower().startswith("джокер"))
async def game_joker(message: Message):
    if not check_group_only(message, "джокер"):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("❌ Использование: джокер [ставка]")
        return
    bet = int(args[1])
    if bet < MIN_BET:
        await message.answer(f"Минимальная ставка {MIN_BET} ₸")
        return
    user = get_user(message.from_user.id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    if user["balance"] < bet:
        await message.answer("Недостаточно средств")
        return
    update_balance(user["user_id"], -bet)
    session_id = f"{message.from_user.id}_{message.message_id}"
    skull_pos = random.randint(0, 2)
    joker_sessions[session_id] = {
        "user_id": user["user_id"], "user_name": user["first_name"],
        "bet": bet, "level": 0, "skull_pos": skull_pos, "history": []
    }
    mention = get_mention(user["user_id"], user["first_name"])
    await message.answer(
        f"{mention}, вы начали игру Джокер!\n💰 Ставка: {format_balance(bet)}\n💵 Выигрыш: x{JOKER_MULTIS[0]} = {format_balance(bet)}",
        reply_markup=get_joker_kb(session_id, finished=False)
    )

def get_joker_kb(session_id, finished=False):
    sess = joker_sessions.get(session_id)
    kb = []
    if sess and "history" in sess:
        for row in sess["history"]:
            kb.append(row)
    if not finished:
        row = [
            InlineKeyboardButton(text="🎴", callback_data=f"jk_{session_id}_0"),
            InlineKeyboardButton(text="🎴", callback_data=f"jk_{session_id}_1"),
            InlineKeyboardButton(text="🎴", callback_data=f"jk_{session_id}_2")
        ]
        kb.append(row)
        btn_text = "🟢 💰 Забрать выигрыш" if sess and sess["level"] > 0 else "💰 Забрать выигрыш"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"jk_cash_{session_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@router.callback_query(F.data.startswith("jk_"))
async def joker_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    if parts[1] == "noop":
        await callback.answer("Этаж пройден.")
        return
    if parts[1] == "cash":
        session_id = f"{parts[2]}_{parts[3]}"
        if session_id not in joker_sessions:
            await callback.answer("Игра завершена.")
            return
        sess = joker_sessions[session_id]
        if sess["user_id"] != callback.from_user.id:
            await callback.answer("Чужая игра!")
            return
        lvl = sess["level"]
        win = int(sess["bet"] * JOKER_MULTIS[lvl])
        update_balance(sess["user_id"], win)
        del joker_sessions[session_id]
        mention = get_mention(sess["user_id"], sess["user_name"])
        await callback.message.edit_text(f"{mention}, вы забрали выигрыш <b>{format_balance(win)}</b>!")
        return

    session_id = f"{parts[1]}_{parts[2]}"
    choice = int(parts[3])
    if session_id not in joker_sessions:
        await callback.answer("Игра завершена.")
        return
    sess = joker_sessions[session_id]
    if sess["user_id"] != callback.from_user.id:
        await callback.answer("Чужая игра!")
        return

    skull_pos = sess["skull_pos"]

    if is_cursed(callback.from_user.id) and random.random() < 0.25:
        choice = skull_pos

    mention = get_mention(sess["user_id"], sess["user_name"])

    if choice == skull_pos:
        row_buttons = []
        for i in range(3):
            if i == skull_pos:
                row_buttons.append(InlineKeyboardButton(text="💀", callback_data="jk_noop"))
            else:
                row_buttons.append(InlineKeyboardButton(text="🃏", callback_data="jk_noop"))
        sess["history"].append(row_buttons)
        del joker_sessions[session_id]
        await callback.message.edit_text(
            f"{mention}, вы проиграли! Проиграно {format_balance(sess['bet'])}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=sess["history"])
        )
        return

    row_buttons = []
    for i in range(3):
        if i == choice:
            row_buttons.append(InlineKeyboardButton(text="🃏", callback_data="jk_noop"))
        elif i == skull_pos:
            row_buttons.append(InlineKeyboardButton(text="💀", callback_data="jk_noop"))
        else:
            row_buttons.append(InlineKeyboardButton(text="🃏", callback_data="jk_noop"))
    sess["history"].append(row_buttons)
    sess["level"] += 1
    lvl = sess["level"]
    sess["skull_pos"] = random.randint(0, 2)

    if lvl >= len(JOKER_MULTIS) - 1:
        win = int(sess["bet"] * JOKER_MULTIS[-1])
        update_balance(sess["user_id"], win)
        del joker_sessions[session_id]
        await callback.message.edit_text(
            f"{mention}, максимальный множитель! Выигрыш <b>{format_balance(win)}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=sess["history"])
        )
    else:
        cur_win = int(sess["bet"] * JOKER_MULTIS[lvl])
        await callback.message.edit_text(
            f"{mention}, вы продолжаете игру Джокер!\n"
            f"💰 Ставка: {format_balance(sess['bet'])}\n"
            f"💵 Выигрыш: x{JOKER_MULTIS[lvl]} = {format_balance(cur_win)}",
            reply_markup=get_joker_kb(session_id, finished=False)
        )

# ---------- Мины (7 бомб) ----------
mines_sessions = {}
MINES_MULTIS = [1.2, 1.5, 1.9, 2.5, 3.5, 5.0, 7.0]

@router.message(F.text.lower().startswith("мины"))
async def game_mines(message: Message):
    if not check_group_only(message, "мины"):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("❌ Использование: мины [ставка]")
        return
    bet = int(args[1])
    if bet < MIN_BET:
        await message.answer(f"Минимальная ставка {MIN_BET} ₸")
        return
    user = get_user(message.from_user.id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    if user["balance"] < bet:
        await message.answer("Недостаточно средств")
        return
    update_balance(user["user_id"], -bet)
    session_id = f"{message.from_user.id}_{message.message_id}"
    mines_sessions[session_id] = {"user_id": user["user_id"], "user_name": user["first_name"], "bet": bet, "opened": [], "mines": random.sample(range(25), 7), "game_over": False}
    mention = get_mention(user["user_id"], user["first_name"])
    await message.answer(
        get_mines_text(mention, bet, 1.2, bet),
        reply_markup=get_mines_kb(session_id, [], False))

def get_mines_text(mention, bet, multi, current_win):
    return f"{mention}, вы начали игру Минное поле!\n💰 Ставка: {format_balance(bet)}\n💵 Выигрыш: x{multi} = {format_balance(current_win)}"

def get_mines_kb(session_id, opened, game_over, mines=None):
    buttons = []
    for i in range(25):
        if not game_over:
            text = "ᅠ" if i in opened else "❓"
            buttons.append(InlineKeyboardButton(text=text, callback_data=f"mn_{session_id}_{i}"))
        else:
            if i in mines:
                text = "💣"
            elif i in opened:
                text = "ᅠ"
            else:
                text = "❓"
            buttons.append(InlineKeyboardButton(text=text, callback_data=f"mn_noop_{i}"))
    kb = [buttons[r*5:(r+1)*5] for r in range(5)]
    if not game_over:
        kb.append([InlineKeyboardButton(text="💰 Забрать выигрыш", callback_data=f"mn_cash_{session_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@router.callback_query(F.data.startswith("mn_"))
async def mines_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]
    if action == "noop":
        await callback.answer("Игра завершена.")
        return
    if action == "cash":
        session_id = f"{parts[2]}_{parts[3]}"
        if session_id not in mines_sessions:
            await callback.answer("Игра не активна.")
            return
        sess = mines_sessions[session_id]
        if sess["user_id"] != callback.from_user.id:
            await callback.answer("Чужая игра!")
            return
        opened_cnt = len(sess["opened"])
        multi = MINES_MULTIS[opened_cnt-1] if opened_cnt > 0 else 1.0
        win = int(sess["bet"] * multi)
        update_balance(sess["user_id"], win)
        del mines_sessions[session_id]
        mention = get_mention(sess["user_id"], sess["user_name"])
        await callback.message.edit_text(f"{mention}, вы забрали выигрыш <b>{format_balance(win)}</b>!")
        return
    session_id = f"{parts[1]}_{parts[2]}"
    cell = int(parts[3])
    if session_id not in mines_sessions:
        await callback.answer("Игра завершена.")
        return
    sess = mines_sessions[session_id]
    if sess["user_id"] != callback.from_user.id:
        await callback.answer("Чужая игра!")
        return
    if cell in sess["opened"]:
        await callback.answer("Уже открыто!")
        return
    mention = get_mention(sess["user_id"], sess["user_name"])

    if is_cursed(callback.from_user.id) and cell not in sess["mines"] and random.random() < 0.2:
        sess["mines"].append(cell)

    if cell in sess["mines"]:
        sess["game_over"] = True
        del mines_sessions[session_id]
        await callback.message.edit_text(f"{mention}, вы подорвались! Проиграно {format_balance(sess['bet'])}.",
                                         reply_markup=get_mines_kb(session_id, sess["opened"], True, sess["mines"]))
    else:
        sess["opened"].append(cell)
        opened_cnt = len(sess["opened"])
        multi = MINES_MULTIS[opened_cnt-1]
        current_win = int(sess["bet"] * multi)
        if opened_cnt >= len(MINES_MULTIS):
            win = int(sess["bet"] * MINES_MULTIS[-1])
            update_balance(sess["user_id"], win)
            del mines_sessions[session_id]
            await callback.message.edit_text(f"{mention}, максимальный множитель! Выигрыш <b>{format_balance(win)}</b>",
                                             reply_markup=get_mines_kb(session_id, sess["opened"], True, sess["mines"]))
        else:
            await callback.message.edit_text(
                get_mines_text(mention, sess["bet"], multi, current_win),
                reply_markup=get_mines_kb(session_id, sess["opened"], False))

# ---------- Coinflip ----------
coinflip_sessions = {}

@router.message(F.text.lower().startswith("coinflip"))
async def game_coinflip(message: Message):
    if not check_group_only(message, "coinflip"):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("❌ Использование: coinflip [ставка]")
        return
    bet = int(args[1])
    if bet < MIN_BET:
        await message.answer(f"Минимальная ставка {MIN_BET} ₸")
        return
    user = get_user(message.from_user.id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    if user["balance"] < bet:
        await message.answer("Недостаточно средств")
        return
    update_balance(user["user_id"], -bet)
    session_id = f"{message.from_user.id}_{message.message_id}"
    coinflip_sessions[session_id] = {"user_id": user["user_id"], "bet": bet}
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🦅 Орёл", callback_data=f"cf_{session_id}_heads"),
         InlineKeyboardButton(text="🪙 Решка", callback_data=f"cf_{session_id}_tails")]
    ])
    mention = get_mention(user["user_id"], user["first_name"])
    await message.answer(f"🪙 {mention} подбрасывает монетку!\n💰 Ставка: <b>{format_balance(bet)}</b>\nВыберите сторону:", reply_markup=kb)

@router.callback_query(F.data.startswith("cf_"))
async def coinflip_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    session_id = f"{parts[1]}_{parts[2]}"
    choice = parts[3]
    if session_id not in coinflip_sessions:
        await callback.answer("Игра завершена.")
        return
    sess = coinflip_sessions[session_id]
    if callback.from_user.id != sess["user_id"]:
        await callback.answer("Чужая игра!")
        return
    del coinflip_sessions[session_id]
    result = random.choice(["heads", "tails"])
    emoji = "🦅" if result == "heads" else "🪙"
    if choice == result:
        win = sess["bet"] * 2
        update_balance(sess["user_id"], win)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE users SET games_played = games_played + 1, games_won = games_won + 1 WHERE user_id = %s", (sess["user_id"],))
                conn.commit()
        await callback.message.edit_text(f"🪙 Выпало: {emoji}\n🎉 Вы выиграли <b>{format_balance(win)}</b>!")
    else:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE users SET games_played = games_played + 1 WHERE user_id = %s", (sess["user_id"],))
                conn.commit()
        await callback.message.edit_text(f"🪙 Выпало: {emoji}\n❌ Вы проиграли <b>{format_balance(sess['bet'])}</b>.")
    await callback.answer()

# ---------- Дуэли ----------
duels = {}

@router.message(F.text.lower().startswith("дуэль"))
async def game_duel(message: Message):
    if not check_group_only(message, "дуэль"):
        return
    if not message.reply_to_message:
        await message.answer("❌ Ответьте на сообщение соперника.")
        return
    target = message.reply_to_message.from_user
    if target.id == message.from_user.id or target.is_bot:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("❌ Использование: дуэль [ставка] в ответ на сообщение.")
        return
    bet = int(args[1])
    if bet < MIN_BET:
        await message.answer(f"Минимальная ставка {MIN_BET} ₸")
        return
    p1_data = get_user(message.from_user.id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    p2_data = get_user(target.id, target.first_name or "", target.last_name or "", target.username or "")
    if p1_data["balance"] < bet or p2_data["balance"] < bet:
        await message.answer("❌ У одного из участников недостаточно средств.")
        return
    duel_id = f"{message.chat.id}_{message.message_id}"
    duels[duel_id] = {"p1_id": p1_data["user_id"], "p1_name": p1_data["first_name"], "p1_choice": None,
                      "p2_id": p2_data["user_id"], "p2_name": p2_data["first_name"], "p2_choice": None,
                      "bet": bet, "chat_id": message.chat.id}
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Сражаться", callback_data=f"duel_acc_{duel_id}"),
         InlineKeyboardButton(text="❌ Отказаться", callback_data=f"duel_den_{duel_id}")]])
    p1_mention = get_mention(p1_data["user_id"], p1_data["first_name"])
    p2_mention = get_mention(p2_data["user_id"], p2_data["first_name"])
    msg = await message.answer(f"⚔️ {p1_mention} вызывает {p2_mention} на дуэль! Ставка: {format_balance(bet)}", reply_markup=kb)
    asyncio.create_task(duel_accept_timeout(duel_id, msg))

async def duel_accept_timeout(duel_id, msg: Message):
    await asyncio.sleep(60)
    if duel_id in duels:
        if not duels[duel_id].get("accepted"):
            del duels[duel_id]
            try:
                await msg.edit_text("⏱ Время вышло! Ничья, ставки возвращены.")
            except:
                pass

@router.callback_query(F.data.startswith("duel_"))
async def duel_init_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]
    duel_id = f"{parts[2]}_{parts[3]}"
    if duel_id not in duels:
        await callback.answer("Дуэль устарела.")
        return
    duel = duels[duel_id]
    uid = callback.from_user.id
    if action == "den":
        if uid not in (duel["p1_id"], duel["p2_id"]):
            await callback.answer("Вы не участник!")
            return
        del duels[duel_id]
        await callback.message.edit_text("❌ Дуэль отклонена.")
        return
    if action == "acc":
        if uid != duel["p2_id"]:
            await callback.answer("Сражаться может только вызываемый!")
            return
        u1 = get_user(duel["p1_id"])
        u2 = get_user(duel["p2_id"])
        if u1["balance"] < duel["bet"] or u2["balance"] < duel["bet"]:
            await callback.message.edit_text("❌ Недостаточно средств.")
            del duels[duel_id]
            return
        duel["accepted"] = True
        update_balance(duel["p1_id"], -duel["bet"])
        update_balance(duel["p2_id"], -duel["bet"])
        p1_mention = get_mention(duel["p1_id"], duel["p1_name"])
        p2_mention = get_mention(duel["p2_id"], duel["p2_name"])
        kb1 = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🪨", callback_data=f"rps_{duel_id}_rock"),
            InlineKeyboardButton(text="📄", callback_data=f"rps_{duel_id}_paper"),
            InlineKeyboardButton(text="✂️", callback_data=f"rps_{duel_id}_scissors")]])
        kb2 = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🪨", callback_data=f"rps_{duel_id}_rock"),
            InlineKeyboardButton(text="📄", callback_data=f"rps_{duel_id}_paper"),
            InlineKeyboardButton(text="✂️", callback_data=f"rps_{duel_id}_scissors")]])
        try:
            await bot.send_message(duel["p1_id"], f"⚔️ Дуэль против {p2_mention}! Ставка: {format_balance(duel['bet'])}.", reply_markup=kb1)
            await bot.send_message(duel["p2_id"], f"⚔️ Дуэль против {p1_mention}! Ставка: {format_balance(duel['bet'])}.", reply_markup=kb2)
            await callback.message.edit_text("⏳ Игроки делают выбор...")
            asyncio.create_task(duel_choice_timeout(duel_id, callback.message))
        except:
            update_balance(duel["p1_id"], duel["bet"])
            update_balance(duel["p2_id"], duel["bet"])
            del duels[duel_id]
            await callback.message.edit_text("❌ Дуэль отменена.")

async def duel_choice_timeout(duel_id, msg: Message):
    await asyncio.sleep(60)
    if duel_id in duels:
        d = duels[duel_id]
        update_balance(d["p1_id"], d["bet"])
        update_balance(d["p2_id"], d["bet"])
        del duels[duel_id]
        try:
            await bot.edit_message_text("⏱ Время вышло! Ничья.", chat_id=msg.chat.id, message_id=msg.message_id)
        except:
            pass

@router.callback_query(F.data.startswith("rps_"))
async def rps_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    duel_id = f"{parts[1]}_{parts[2]}"
    choice = parts[3]
    if duel_id not in duels:
        await callback.answer("Дуэль устарела.")
        return
    duel = duels[duel_id]
    uid = callback.from_user.id
    if uid == duel["p1_id"]:
        duel["p1_choice"] = choice
    elif uid == duel["p2_id"]:
        duel["p2_choice"] = choice
    else:
        return
    await callback.message.edit_text("✅ Выбор сделан. Ожидаем соперника...")
    if duel["p1_choice"] and duel["p2_choice"]:
        c1, c2 = duel["p1_choice"], duel["p2_choice"]
        bank = duel["bet"] * 2
        if c1 == c2:
            update_balance(duel["p1_id"], duel["bet"])
            update_balance(duel["p2_id"], duel["bet"])
            res_text = "🤝 Ничья! Ставки возвращены."
        else:
            rules = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
            if rules[c1] == c2:
                win_id = duel["p1_id"]
                winner_mention = get_mention(duel["p1_id"], duel["p1_name"])
            else:
                win_id = duel["p2_id"]
                winner_mention = get_mention(duel["p2_id"], duel["p2_name"])
            update_balance(win_id, bank)
            res_text = f"🏆 Победитель: {winner_mention} забирает {format_balance(bank)}!"
        try:
            await bot.send_message(duel["chat_id"], f"⚔️ <b>Результат дуэли</b>\n{res_text}")
        except:
            pass
        del duels[duel_id]

# ---------- Фортуна (слоты) ----------
@router.message(F.text.lower().startswith("фортуна"))
async def game_fortune(message: Message):
    if not check_group_only(message, "фортуна"):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("❌ Использование: фортуна [ставка]")
        return
    bet = int(args[1])
    if bet < MIN_BET:
        await message.answer(f"Минимальная ставка {MIN_BET} ₸")
        return
    user = get_user(message.from_user.id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    if user["balance"] < bet:
        await message.answer("Недостаточно средств")
        return
    update_balance(user["user_id"], -bet)
    emojis = ["🍒", "🍋", "🍊", "💎", "7"]
    r1, r2, r3 = random.choice(emojis), random.choice(emojis), random.choice(emojis)
    if r1 == r2 == r3:
        if r1 == "7":
            multi = 50
        elif r1 == "💎":
            multi = 25
        elif r1 == "🍒":
            multi = 5
        else:
            multi = 3
    elif r1 == r2 or r1 == r3 or r2 == r3:
        multi = 1
    else:
        multi = 0
    win = int(bet * multi)
    if win > 0:
        update_balance(user["user_id"], win)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET games_played = games_played + 1 WHERE user_id = %s", (user["user_id"],))
            if win > 0:
                cursor.execute("UPDATE users SET games_won = games_won + 1 WHERE user_id = %s", (user["user_id"],))
            conn.commit()
    text = f"🎰 Фортуна\n{r1} {r2} {r3}\n"
    if multi == 0:
        text += "Ты проиграл."
    else:
        text += f"Ты выиграл x{multi}!\nВыигрыш: {format_balance(win)}"
    await message.answer(text)

# ---------- Рулетка ----------
REDS = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
chat_roulette_bets = {}
chat_last_bet_time = {}

def parse_roulette_target(target_str: str):
    t = target_str.lower()
    if t in ["к", "red", "красное"]:
        return "к", "RED"
    elif t in ["ч", "black", "черное"]:
        return "ч", "BLACK"
    elif t in ["even", "чет"]:
        return "even", "EVEN"
    elif t in ["odd", "нечет"]:
        return "odd", "ODD"
    elif t in ["1-12", "13-24", "25-36"]:
        return t, t
    elif "-" in t:
        try:
            s, e = map(int, t.split("-"))
            if 0 <= s <= 36 and 0 <= e <= 36 and s <= e:
                count = e - s + 1
                if count <= 6:
                    return t, f"{s}-{e}"
                else:
                    return None, None
        except:
            pass
    elif t.isdigit():
        val = int(t)
        if 0 <= val <= 36:
            return str(val), str(val)
    return None, None

def choice_emoji(choice):
    if choice in ["RED", "к", "red", "красное"]:
        return "🔴"
    elif choice in ["BLACK", "ч", "black", "черное"]:
        return "⚫"
    elif choice in ["EVEN", "even", "чет"]:
        return "🔵"
    elif choice in ["ODD", "odd", "нечет"]:
        return "🟠"
    elif choice == "0":
        return "🟢"
    else:
        return ""

@router.message(F.text.lower() == "ставки")
async def roulette_bets_list(message: Message):
    if not check_group_only(message, "рулетка"):
        return
    chat_id = message.chat.id
    bets = chat_roulette_bets.get(chat_id, [])
    if not bets:
        await message.answer("📋 Нет активных ставок.")
        return
    text = "📋 <b>Активные ставки</b>\n\n"
    for b in bets:
        mention = get_mention(b["user_id"], b["user_name"])
        emoji = choice_emoji(b["choice_display"])
        text += f"• {mention} — {b['bet']} ₸ на {emoji} {b['choice_display']}\n"
    await message.answer(text)

@router.message(F.text.lower() == "лог")
async def cmd_roulette_log(message: Message):
    if not check_group_only(message, "рулетка"):
        return
    history = get_roulette_history()
    if not history:
        await message.answer("📜 История пуста.")
        return
    lines = [f"{item['roll']}{item['color']}" for item in history]
    await message.answer("\n".join(lines))

@router.message(F.text.lower().in_(["го", "старт"]))
async def roulette_go(message: Message):
    if not check_group_only(message, "рулетка"):
        return
    chat_id = message.chat.id
    user_id = message.from_user.id
    if chat_id not in chat_roulette_bets or not any(b["user_id"] == user_id for b in chat_roulette_bets[chat_id]):
        return
    last_time = chat_last_bet_time.get((chat_id, user_id))
    if last_time:
        diff = (datetime.now() - last_time).total_seconds()
        if diff < 15:
            rem = int(15 - diff)
            await message.answer(f"Ошибка. Раунд можно закончить через {rem} сек.")
            return
    bets_to_play = chat_roulette_bets[chat_id]
    chat_roulette_bets[chat_id] = []
    valid_bets = bets_to_play
    if not valid_bets:
        return
    try:
        animation_file = get_cached_animation()
        if animation_file:
            sent_msg = await message.answer_animation(animation=animation_file)
        else:
            sent_msg = await message.answer("🎰 Крутим рулетку...")
        await asyncio.sleep(5)
        try:
            await bot.delete_message(chat_id, sent_msg.message_id)
        except:
            pass
    except:
        await asyncio.sleep(5)
    roll = random.randint(0, 36)
    color = "🔴" if roll in REDS else ("🟢" if roll == 0 else "⚫")
    add_roulette_log(roll, color)
    res_lines = [f"Рулетка: {roll}{color}"]
    for b in valid_bets:
        mention = get_mention(b["user_id"], b["user_name"])
        emoji = choice_emoji(b["choice_display"])
        res_lines.append(f"{mention} {b['bet']} ₸ на {emoji} {b['choice_display']}")
    res_lines.append("")

    unique_ids = set(b["user_id"] for b in valid_bets)
    cursed_users = {}
    for uid in unique_ids:
        cursed_users[uid] = is_cursed(uid)

    wins = []
    losses = []
    for b in valid_bets:
        choice = b["choice"]
        bet = b["bet"]
        is_win = False
        multi = 0
        if choice in ["к", "red"]:
            is_win = roll in REDS
            multi = 2
        elif choice in ["ч", "black"]:
            is_win = roll not in REDS and roll != 0
            multi = 2
        elif choice in ["even", "чет"]:
            is_win = roll != 0 and roll % 2 == 0
            multi = 2
        elif choice in ["odd", "нечет"]:
            is_win = roll != 0 and roll % 2 != 0
            multi = 2
        elif choice in ["1-12", "13-24", "25-36"]:
            s, e = map(int, choice.split("-"))
            is_win = s <= roll <= e
            multi = 3
        elif "-" in choice:
            s, e = map(int, choice.split("-"))
            is_win = s <= roll <= e
            n = e - s + 1
            multi = max(2, int(36 / n))
        else:
            is_win = roll == int(choice)
            multi = 36
        if is_win and cursed_users.get(b["user_id"], False) and random.random() < 0.3:
            is_win = False
        if is_win:
            win_amount = bet * multi
            wins.append((b["user_id"], win_amount, bet, b["choice_display"]))
        else:
            losses.append(b["user_id"])

    for uid, win_amount, bet, display in wins:
        user = get_user(uid)
        mention = get_mention(uid, user["first_name"])
        res_lines.append(f"{mention} ставка {bet} ₸ выиграл")
        res_lines.append(f"{format_balance(win_amount)} на {display}")
        res_lines.append("")

    if wins or losses:
        with get_db() as conn:
            with conn.cursor() as cursor:
                for uid, win_amount, _, _ in wins:
                    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (win_amount, uid))
                for uid, _, _, _ in wins:
                    cursor.execute("UPDATE users SET games_played = games_played + 1, games_won = games_won + 1 WHERE user_id = %s", (uid,))
                for uid in losses:
                    cursor.execute("UPDATE users SET games_played = games_played + 1 WHERE user_id = %s", (uid,))
                conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Повторить", callback_data=f"rl_rep_{user_id}"),
        InlineKeyboardButton(text="Удвоить", callback_data=f"rl_dbl_{user_id}")
    ]])
    await message.answer("\n".join(res_lines), reply_markup=kb)

@router.callback_query(F.data.startswith("rl_rep_") | F.data.startswith("rl_dbl_"))
async def roulette_action_callback(callback: CallbackQuery):
    data = callback.data
    parts = data.split("_")
    action = parts[1]
    target_uid = int(parts[2])
    if callback.from_user.id != target_uid:
        await callback.answer("❌ Не ваша ставка!", show_alert=True)
        return
    user_id = target_uid
    user = get_user(user_id, callback.from_user.first_name or "", callback.from_user.last_name or "", callback.from_user.username or "")
    last_bets = get_last_bets(user_id)
    if not last_bets:
        await callback.answer("❌ Нет сохранённой ставки!", show_alert=True)
        return
    multiplier = 2 if action == "dbl" else 1
    total_cost = sum(b["bet"] * multiplier for b in last_bets)
    if user["balance"] < total_cost:
        await callback.answer(f"❌ Недостаточно средств! Требуется {format_balance(total_cost)}", show_alert=True)
        return
    update_balance(user_id, -total_cost)
    chat_id = callback.message.chat.id
    if chat_id not in chat_roulette_bets:
        chat_roulette_bets[chat_id] = []
    updated_last_bets = []
    displays = []
    for b in last_bets:
        new_bet_amt = b["bet"] * multiplier
        chat_roulette_bets[chat_id].append({
            "user_id": user_id,
            "user_name": user["first_name"],
            "bet": new_bet_amt,
            "choice": b["choice"],
            "choice_display": b["choice_display"]
        })
        updated_last_bets.append({
            "bet": new_bet_amt,
            "choice": b["choice"],
            "choice_display": b["choice_display"]
        })
        displays.append(f"{new_bet_amt} ₸ на {b['choice_display']}")
    chat_last_bet_time[(chat_id, user_id)] = datetime.now()
    save_last_bets(user_id, updated_last_bets)
    await callback.answer("✅ Ставка сделана!")
    mention = get_mention(user_id, user["first_name"])
    formatted_bets = "\n".join(displays)
    await callback.message.answer(f"Ставка принята: {mention} всего {format_balance(total_cost)}\n\n{formatted_bets}")

@router.message(F.text.lower() == "отмена")
async def roulette_cancel(message: Message):
    if not check_group_only(message, "рулетка"):
        return
    chat_id = message.chat.id
    user_id = message.from_user.id
    bets = chat_roulette_bets.get(chat_id, [])
    user_bets = [b for b in bets if b["user_id"] != user_id]
    removed = len(bets) - len(user_bets)
    if removed == 0:
        await message.answer("У вас нет активных ставок.")
        return
    for b in bets:
        if b["user_id"] == user_id:
            update_balance(user_id, b["bet"])
    if user_bets:
        chat_roulette_bets[chat_id] = user_bets
    else:
        chat_roulette_bets.pop(chat_id, None)
    chat_last_bet_time.pop((chat_id, user_id), None)
    await message.answer(f"✅ Отменено ставок: {removed}. Все ваши ставки удалены.")

# ---------- ОБРАБОТЧИК СТАВОК (РУЛЕТКА) ----------
@router.message(F.text)
async def generic_message_handler(message: Message):
    if message.text.startswith("/"):
        return
    text = message.text.strip()
    parts = text.split()
    if not parts or not parts[0].isdigit():
        return
    bet_per_item = int(parts[0])
    targets = parts[1:]
    if not targets:
        return
    valid_targets = []
    for tgt in targets:
        code, display = parse_roulette_target(tgt)
        if code is not None:
            valid_targets.append((code, display))
    if not valid_targets:
        return
    if len(valid_targets) > 75:
        valid_targets = valid_targets[:75]
    if not check_group_only(message, "рулетка"):
        return
    if bet_per_item < MIN_BET:
        await message.answer(f"Минимальная ставка на один объект: {MIN_BET} ₸")
        return
    user = get_user(message.from_user.id, message.from_user.first_name or "", message.from_user.last_name or "", message.from_user.username or "")
    total_bet = bet_per_item * len(valid_targets)
    if user["balance"] < total_bet:
        await message.answer(f"❌ Недостаточно средств. Требуется {format_balance(total_bet)} для {len(valid_targets)} ставок.")
        return
    update_balance(user["user_id"], -total_bet)
    chat_id = message.chat.id
    if chat_id not in chat_roulette_bets:
        chat_roulette_bets[chat_id] = []
    new_user_bets = []
    displays = []
    for code, display in valid_targets:
        chat_roulette_bets[chat_id].append({
            "user_id": user["user_id"],
            "user_name": user["first_name"],
            "bet": bet_per_item,
            "choice": code,
            "choice_display": display
        })
        new_user_bets.append({
            "bet": bet_per_item,
            "choice": code,
            "choice_display": display
        })
        displays.append(f"{bet_per_item} ₸ на {display}")
    chat_last_bet_time[(chat_id, user["user_id"])] = datetime.now()
    save_last_bets(user["user_id"], new_user_bets)
    mention = get_mention(user["user_id"], user["first_name"])
    formatted_bets = "\n".join(displays)
    await message.answer(f"Ставка принята: {mention} всего {format_balance(total_bet)}\n\n{formatted_bets}")

# ==================== ВЕБ-СЕРВЕР ДЛЯ RENDER ====================
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# ==================== ЗАПУСК ====================
async def main():
    global BOT_USERNAME
    bot_info = await bot.get_me()
    if bot_info.username:
        BOT_USERNAME = bot_info.username

    @dp.message.middleware()
    async def check_bans(handler, event, data):
        if event.from_user:
            user = get_user(event.from_user.id)
            if user.get('is_banned'):
                if user.get('ban_until'):
                    if user['ban_until'] > datetime.now():
                        return
                    else:
                        with get_db() as conn:
                            with conn.cursor() as cursor:
                                cursor.execute("UPDATE users SET is_banned = FALSE, ban_until = NULL WHERE user_id = %s", (event.from_user.id,))
                                conn.commit()
                else:
                    return
        return await handler(event, data)

    @dp.message.middleware()
    async def remove_bot_mention(handler, event, data):
        if event.text and event.text.startswith("/"):
            parts = event.text.split()
            if parts:
                cmd_part = parts[0]
                if "@" in cmd_part:
                    cmd, _, mention = cmd_part.partition("@")
                    if mention.lower() == BOT_USERNAME.lower():
                        new_text = cmd
                        if len(parts) > 1:
                            new_text += " " + " ".join(parts[1:])
                        event.text = new_text
        return await handler(event, data)

    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await web_server()
    logging.info("Бот Kaspi Red запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
