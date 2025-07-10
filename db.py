import sqlite3

DB_PATH = "bot_subscriptions.db"

def initialize_database():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_admin BOOLEAN DEFAULT 0,
            min_profit INTEGER DEFAULT 0,
            price_min REAL DEFAULT 0,
            price_max REAL DEFAULT 100,
            interval INTEGER DEFAULT 60,
            active BOOLEAN DEFAULT 1
        )
    ''')
    conn.commit()
    conn.close()

# Вызов инициализации базы при импорте
initialize_database()

import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

DATABASE_NAME = 'bot_subscriptions.db'




def init_db():
    """Инициализирует базу данных и создает таблицу подписок, если её нет."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            end_date REAL DEFAULT 0, -- Unix timestamp, 0 если нет активной подписки
            is_admin INTEGER DEFAULT 0 -- 1 если администратор, 0 если обычный пользователь
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id INTEGER PRIMARY KEY,
            min_profit INTEGER DEFAULT 5,
            interval INTEGER DEFAULT 30,
            price_range_min REAL DEFAULT 5.0,
            price_range_max REAL DEFAULT 25.0,
            active INTEGER DEFAULT 0 -- 1 если бот активен для пользователя, 0 если остановлен
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usernames (
            user_id INTEGER PRIMARY KEY,
            username TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована.")

def get_subscription_end_date(user_id: int) -> float:
    """Получает дату окончания подписки для пользователя."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT end_date FROM subscriptions WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0.0

def set_subscription_end_date(user_id: int, end_date: float):
    """Устанавливает или обновляет дату окончания подписки для пользователя."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO subscriptions (user_id, end_date) VALUES (?, ?)', (user_id, end_date))
    conn.commit()
    conn.close()
    logger.info("Подписка для пользователя %s установлена до %s", user_id, time.ctime(end_date))

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT is_admin FROM subscriptions WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] == 1 if result else False

def set_admin_status(user_id: int, status: bool):
    """Устанавливает или снимает статус администратора для пользователя."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO subscriptions (user_id, is_admin) VALUES (?, ?)', (user_id, 1 if status else 0))
    conn.commit()
    conn.close()
    logger.info("Статус администратора для пользователя %s установлен: %s", user_id, status)

def get_user_prefs(user_id: int) -> dict:
    """Получает настройки пользователя из базы данных."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT min_profit, interval, price_range_min, price_range_max, active FROM user_prefs WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {
            'min_profit': result[0],
            'interval': result[1],
            'price_range': (result[2], result[3]),
            'active': bool(result[4]),
            'notified_ids': set()
        }
    else:
        prefs = {
            'min_profit': 5,
            'interval': 30,
            'price_range': (5.0, 25.0),
            'active': False,
            'notified_ids': set()
        }
        set_user_prefs(user_id, prefs['min_profit'], prefs['interval'], prefs['price_range'][0], prefs['price_range'][1], prefs['active'])
        return prefs

def set_user_prefs(user_id: int, min_profit: int, interval: int, price_min: float, price_max: float, active: bool):
    """Устанавливает настройки пользователя в базу данных."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_prefs (user_id, min_profit, interval, price_range_min, price_range_max, active)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, min_profit, interval, price_min, price_max, 1 if active else 0))
    conn.commit()
    conn.close()
    logger.info("Настройки пользователя %s обновлены: Прибыль=%s, Интервал=%s, Диапазон=%s-%s, Активен=%s", user_id, min_profit, interval, price_min, price_max, active)

def save_user(user_id: int, username: str):
    """Сохраняет username пользователя."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO usernames (user_id, username) VALUES (?, ?)
    ''', (user_id, username))
    conn.commit()
    conn.close()

def get_user_id_by_username(username: str) -> int | None:
    with sqlite3.connect(DATABASE_NAME) as conn:
        cur = conn.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        result = cur.fetchone()
        return result[0] if result else None


def add_user_if_not_exists(user_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    if cur.fetchone() is None:
        cur.execute('''
            INSERT INTO users (user_id, username, is_admin, min_profit, price_min, price_max, interval, active)
            VALUES (?, ?, 0, 10, 0, 100, 60, 1)
        ''', (user_id, username))
        conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, active FROM users")
    users = cur.fetchall()
    conn.close()
    return users


def get_user_by_id(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()
    return user
