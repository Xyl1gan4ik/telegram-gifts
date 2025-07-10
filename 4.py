import logging
import asyncio
import time
import json
import sqlite3

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.exceptions import TelegramBadRequest # Добавляем для обработки ошибок при редактировании сообщений

# Для планировщика задач
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import httpx
import curl_cffi

import config
import db

# ⚙️ Настройки логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера Aiogram
bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# 🔧 Настройки пользователя в оперативной памяти (для текущего сеанса)
# В этот словарь будем кэшировать настройки пользователей из БД + временный notified_ids
user_settings = {}

# Инициализируем сессию curl_cffi с имитацией Chrome
session = curl_cffi.Session(impersonate="chrome131")

# Планировщик задач
scheduler = AsyncIOScheduler()

# Переменная для хранения юзернейма бота
bot_username: str = None # Указываем тип для ясности, будет установлен в main()

# --- Вспомогательные функции ---

def get_user_actual_settings(user_id: int) -> dict:
    """
    Получает текущие настройки пользователя, загружая их из БД, если они ещё не в кэше.
    """
    if user_id not in user_settings:
        user_settings[user_id] = db.get_user_prefs(user_id)
        # Убедимся, что notified_ids всегда set() при первом получении
        if 'notified_ids' not in user_settings[user_id]:
            user_settings[user_id]['notified_ids'] = set()
    return user_settings[user_id]

def get_floor_price(name, model):
    """
    Получает минимальную (floor) цену для конкретного подарка (по имени и модели)
    с помощью API gifts3.tonnel.network/api/filterStats.
    """
    key = f"{name}_{model}"
    try:
        # Ключ в payload должен быть "authData", как показано на скриншотах
        payload = {
            "authData": config.AUTH_DATA # Отправляем AUTH_DATA как строку
        }

        # Отправляем POST-запрос с помощью curl_cffi session
        res = session.post(
            "https://gifts3.tonnel.network/api/filterStats",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 OPR/112.0.0.0",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://market.tonnel.network/",
                "Content-Type": "application/json", # Важно: указываем JSON тип контента
                "Origin": "https://market.tonnel.network",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
                "Sec-GPC": "1",
                "Priority": "u=4",
            },
            json=payload, # Отправляем данные как JSON
            timeout=10, # Таймаут для запроса
            verify=False # ВНИМАНИЕ: Отключение проверки SSL-сертификата не рекомендуется в продакшене!
        )

        # Проверяем статус код HTTP ответа
        if res.status_code != 200:
            logger.error("[ERROR] floorPrice: HTTP %s - %s", res.status_code, res.text)
            return None

        # Пробуем декодировать ответ как JSON
        try:
            data = res.json()
        except json.JSONDecodeError:
            logger.warning("[WARN] floorPrice: Не удалось декодировать JSON из ответа:\n%s", res.text[:500])
            return None

        # Ожидаем, что ответ будет словарем с ключом 'data',
        # и внутри него статистика по ключам вида "ИмяПодарка_МодельПодарка"
        # Пример ожидаемой структуры: {"data": {"GiftName_ModelName": {"floorPrice": 123.45, ...}}}
        floor_data = data.get("data", {})
        return floor_data.get(key, {}).get("floorPrice")

    except Exception as e:
        logger.error("[ERROR] Ошибка при получении floor price для %s_%s: %s", name, model, e)
        return None

# --- Функции проверки подписки ---

async def check_subscription_status(user_id: int, message_or_query: types.Message | types.CallbackQuery) -> bool:
    """
    Проверяет статус подписки пользователя.
    Возвращает True, если подписка активна или пользователь является админом, иначе False.
    Отправляет соответствующее сообщение пользователю, если подписки нет.
    """
    if db.is_admin(user_id):
        return True

    end_date = db.get_subscription_end_date(user_id)
    if time.time() < end_date:
        return True
    else:
        current_settings = get_user_actual_settings(user_id)
        if current_settings['active']:
            current_settings['active'] = False
            # Сохраняем изменение статуса активности в БД
            db.set_user_prefs(user_id, current_settings['min_profit'], current_settings['interval'], 
                              current_settings['price_range'][0], current_settings['price_range'][1], current_settings['active'])
            # Если бот активен, но подписка истекла, отправляем сообщение.
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="Ваша подписка истекла. Пожалуйста, продлите её, чтобы продолжить использование бота. "
                         "Используйте команду /subscribe."
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить сообщение об истечении подписки пользователю {user_id}: {e}")
            logger.info("Подписка пользователя %s истекла. Бот деактивирован.", user_id)
        else:
            # Если подписки нет, и пользователь пытался использовать команду/кнопку
            try:
                if isinstance(message_or_query, types.Message):
                    await message_or_query.reply(
                        "Чтобы использовать бота, вам необходимо оформить подписку. "
                        "Используйте команду /subscribe."
                    )
                elif isinstance(message_or_query, types.CallbackQuery):
                    await message_or_query.answer() # Ответить на колбэк, чтобы избежать зависания
                    await message_or_query.message.edit_text(
                        "Чтобы использовать бота, вам необходимо оформить подписку. "
                        "Используйте команду /subscribe."
                    )
            except TelegramBadRequest as e:
                logger.warning(f"Не удалось отредактировать/ответить на сообщение для пользователя {user_id} (вероятно, сообщение слишком старое): {e}")
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения о необходимости подписки пользователю {user_id}: {e}")
        return False

# --- Основная логика проверки аукционов ---

async def check_auctions_job():
    """
    Асинхронная функция для периодической проверки активных аукционов.
    Выполняется в фоновом режиме.
    """
    logger.info("Запущена фоновая задача check_auctions_job.")
    
    while True: # Бесконечный цикл для периодической проверки
        all_user_ids = []
        conn = sqlite3.connect(db.DATABASE_NAME)
        cursor = conn.cursor()
        # Получаем все user_id, которые есть в таблицах subscriptions или user_prefs
        cursor.execute("SELECT user_id FROM subscriptions UNION SELECT user_id FROM user_prefs")
        for row in cursor.fetchall():
            all_user_ids.append(row[0])
        conn.close()

        if not all_user_ids:
            logger.info("Нет пользователей в базе данных. Ожидание...")
            await asyncio.sleep(config.DEFAULT_INTERVAL) # Ждем, если нет пользователей
            continue

        active_users_for_check = []
        for user_id in all_user_ids:
            current_settings = get_user_actual_settings(user_id) # Получаем настройки (из кэша или БД)

            is_user_admin = db.is_admin(user_id)
            end_date = db.get_subscription_end_date(user_id)
            has_active_subscription = time.time() < end_date

            # Если пользователь админ ИЛИ у него активная подписка, то он считается активным
            if is_user_admin or has_active_subscription:
                if not current_settings['active']: # Если в кэше/БД стоит False, но должен быть активен
                    current_settings['active'] = True # Активируем его в кэше
                    db.set_user_prefs(user_id, current_settings['min_profit'], current_settings['interval'], 
                                      current_settings['price_range'][0], current_settings['price_range'][1], True)
                    logger.info("Пользователь %s стал активным (подписка/админ).", user_id)
                active_users_for_check.append(user_id)
            else:
                # Если подписка истекла и не админ
                if current_settings['active']: # Если ранее был активен, но подписка истекла
                    current_settings['active'] = False # Деактивируем его в кэше
                    db.set_user_prefs(user_id, current_settings['min_profit'], current_settings['interval'], 
                                      current_settings['price_range'][0], current_settings['price_range'][1], False)
                    try:
                        await bot.send_message(chat_id=user_id, text="Ваша подписка истекла. Пожалуйста, продлите её, чтобы продолжить использование бота. Используйте команду /subscribe.")
                    except Exception as e:
                        logger.warning("Не удалось отправить сообщение пользователю %s о закончившейся подписке: %s", user_id, e)
                    logger.info("Подписка пользователя %s истекла. Бот деактивирован.", user_id)
                logger.debug("Бот не активен для пользователя %s. Пропуск проверки аукционов.", user_id)

        if not active_users_for_check:
            logger.info("Нет активных пользователей для проверки аукционов. Ожидание...")
            await asyncio.sleep(config.DEFAULT_INTERVAL) # Ждем, если нет активных пользователей
            continue

        # Собираем все интервалы активных пользователей для определения следующего sleep
        intervals = [get_user_actual_settings(uid)['interval'] for uid in active_users_for_check]
        max_interval = max(intervals) if intervals else config.DEFAULT_INTERVAL


        for user_id in active_users_for_check:
            current_settings = get_user_actual_settings(user_id) # Получаем настройки для текущего пользователя
            
            try:
                payload = {
                    "page": 1,
                    "limit": 30,
                    "sort": '{"auctionEndTime":1,"gift_id":-1}',
                    "filter": '{"auction_id":{"$exists":true},"status":"active","asset":"TON"}',
                    "price_range": None,
                    "ref": 0,
                    "user_auth": config.AUTH_DATA # <--- ИСПОЛЬЗУЕМ config.AUTH_DATA
                }

                res = session.post(
                    "https://gifts3.tonnel.network/api/pageGifts",
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 OPR/112.0.0.0",
                        "Accept": "*/*",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Referer": "https://market.tonnel.network/",
                        "Content-Type": "application/json",
                        "Origin": "https://market.tonnel.network",
                        "Connection": "keep-alive",
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "cross-site",
                        "Sec-GPC": "1",
                        "Priority": "u=4",
                    },
                    json=payload,
                    timeout=10,
                    verify=False # <--- ИСПОЛЬЗУЕМ verify=False как временное решение
                )

                if res.status_code != 200:
                    logger.error("[ERROR] check_auctions_job (для %s): HTTP %s - %s", user_id, res.status_code, res.text)
                    continue

                try:
                    data = res.json()
                except json.JSONDecodeError:
                    logger.warning("[WARN] check_auctions_job (для %s): Не удалось декодировать JSON из ответа:\n%s", user_id, res.text[:500])
                    continue

                auctions = data if isinstance(data, list) else data.get('auctions', [])
                logger.info("Для пользователя %s: Найдено активных аукционов: %d", user_id, len(auctions))

                for gift in auctions:
                    gift_id = gift.get('gift_id')
                    if gift_id is None:
                        logger.warning("Объект подарка без gift_id для пользователя %s: %s", user_id, gift)
                        continue

                    # Проверка, был ли этот подарок уже уведомлен в текущей сессии
                    if gift_id in current_settings['notified_ids']:
                        continue

                    name = gift.get('name', 'N/A')
                    model = gift.get('model', 'N/A')
                    backdrop = gift.get('backdrop', 'N/A')
                    auction_data = gift.get('auction', {})
                    bid_history = auction_data.get('bidHistory', [])

                    bid = float(bid_history[-1]['amount']) if bid_history else float(auction_data.get('startingBid', 0))

                    end_time_raw = auction_data.get('auctionEndTime', '')
                    end_time = end_time_raw[:19].replace('T',' ') if end_time_raw else 'N/A'

                    gift_num = gift.get('gift_num', gift.get('gift_id', 'N/A'))

                    min_price_range, max_price_range = current_settings['price_range']
                    if not (min_price_range <= bid <= max_price_range):
                        logger.debug("Аукцион %s (ставка %.2f) вне диапазона цен %s для пользователя %s",
                                     gift_id, bid, current_settings['price_range'], user_id)
                        continue

                    min_price = get_floor_price(name, model)
                    if min_price is None:
                        logger.warning("Не удалось получить floor price для %s_%s. Пропускаем подарок %s для пользователя %s.", name, model, gift_id, user_id)
                        continue
                    
                    floor_with_markup = min_price * 1.06
                    after_commission = floor_with_markup * 0.9
                    profit = after_commission - bid
                    percent = (profit / bid) * 100 if bid > 0 else -100

                    if percent < current_settings['min_profit']:
                        logger.debug("Аукцион %s (прибыль %.1f%%) ниже минимальной прибыли %d%% для пользователя %s",
                                    gift_id, percent, current_settings['min_profit'], user_id)
                        continue

                    gift_link = f"https://t.me/tonnel_network_bot/gift?startapp={gift_num}" if gift_num != 'N/A' else 'Ссылка недоступна'

                    message_text = (
                        f"🎁Название: {name}\n"
                        f"Модель: {model}\n"
                        f"Фон: {backdrop}\n"
                        f"⏳Заканчивается: {end_time}\n"
                        f"💰Ставка: {bid:.2f} TON\n"
                        f"Tonnel Floor: {min_price:.2f} TON\n"
                        f"💵Прибыль: +{percent:.1f}% ({profit:.2f} TON)\n"
                        f"🔗Прямая ссылка: {gift_link}"
                    )

                    await bot.send_message(chat_id=user_id, text=message_text) # Используем глобальный 'bot'
                    logger.info("Отправлено уведомление о подарке %s (прибыль %.1f%%) пользователю %s", gift_id, percent, user_id)
                    current_settings['notified_ids'].add(gift_id)

            except Exception as e:
                logger.error("[ERROR] Общая ошибка в check_auctions_job для пользователя %s: %s", user_id, e)
        
        await asyncio.sleep(max_interval) # Ждем наибольший интервал среди активных пользователей

        
        # Определяем максимальный интервал среди активных пользователей
        max_interval = config.DEFAULT_INTERVAL
        if active_users_for_check:
            for user_id in active_users_for_check:
                settings = get_user_actual_settings(user_id)
                max_interval = max(max_interval, settings['interval'])
        else:
            logger.info("Нет активных пользователей для поиска. Используется интервал по умолчанию: %d сек.", max_interval)

        await asyncio.sleep(max_interval) # Ждем наибольший интервал среди активных пользователей

# --- Команды Telegram (Aiogram) ---

@dp.message(Command("start"))
async def start_command(message: types.Message):
    """
    Обработчик команды /start.
    """
    user_id = message.from_user.id
    username = message.from_user.username  # <-- Добавлено
    db.save_user(user_id, username)   
    user_prefs = get_user_actual_settings(user_id)

    if db.is_admin(user_id):
        user_prefs['active'] = True
        db.set_user_prefs(user_id, user_prefs['min_profit'], user_prefs['interval'], user_prefs['price_range'][0], user_prefs['price_range'][1], True)
        await message.reply("Привет, Админ! Бот запущен и всегда активен для вас.")
        logger.info("Бот запущен для Админа %s", user_id)
        return

    end_date = db.get_subscription_end_date(user_id)
    if time.time() < end_date:
        user_prefs['active'] = True
        db.set_user_prefs(user_id, user_prefs['min_profit'], user_prefs['interval'], user_prefs['price_range'][0], user_prefs['price_range'][1], True)
        await message.reply(
            f"Бот запущен. Поиск арбитража каждые {user_prefs['interval']} сек.\n"
            f"Ваша подписка активна до: {time.ctime(end_date)}"
        )
        logger.info("Бот запущен для пользователя %s", user_id)
    else:
        user_prefs['active'] = False # Убеждаемся, что неактивен, если подписки нет
        db.set_user_prefs(user_id, user_prefs['min_profit'], user_prefs['interval'], user_prefs['price_range'][0], user_prefs['price_range'][1], False)
        await message.reply(
            "Привет! Для использования всех функций бота необходима подписка. "
            "Используйте команду /subscribe для выбора плана."
        )
        logger.info("Пользователь %s без активной подписки пытался запустить бота.", user_id)


@dp.message(Command("stop"))
async def stop_command(message: types.Message):
    """
    Обработчик команды /stop. Деактивирует бота для пользователя и очищает список уведомленных ID.
    """
    user_id = message.from_user.id
    current_settings = get_user_actual_settings(user_id)

    if not db.is_admin(user_id):
        end_date = db.get_subscription_end_date(user_id)
        if time.time() > end_date:
            await message.reply("Вы не можете остановить бота, так как у вас нет активной подписки.")
            return

    current_settings['active'] = False
    current_settings['notified_ids'].clear()
    db.set_user_prefs(user_id, current_settings['min_profit'], current_settings['interval'], current_settings['price_range'][0], current_settings['price_range'][1], False) # Сохраняем флаг active
    await message.reply("Уведомления остановлены.")
    logger.info("Бот остановлен для пользователя %s", user_id)

@dp.message(Command("settings"))
async def settings_command(message: types.Message):
    """
    Обработчик команды /settings. Показывает текущие настройки пользователя.
    """
    user_id = message.from_user.id
    current_settings = get_user_actual_settings(user_id)

    if not await check_subscription_status(user_id, message): # Передаем message для ответов
        return

    status = "активен" if current_settings['active'] else "остановлен"
    end_date = db.get_subscription_end_date(user_id)
    sub_status = f"Активна до: {time.ctime(end_date)}" if end_date > time.time() else "Неактивна"

    msg_text = (
        f"Текущие настройки:\n"
        f"Статус бота: {status}\n"
        f"Статус подписки: {sub_status}\n"
        f"Интервал проверки: {current_settings['interval']} секунд\n"
        f"Минимальная прибыль: {current_settings['min_profit']}%\n"
        f"Диапазон ставок: от {current_settings['price_range'][0]} до {current_settings['price_range'][1]} TON\n\n"
        f"Для изменения настроек используйте:\n"
        f"/setprofit <процент>\n"
        f"/setinterval <секунды>\n"
        f"/setpricerange <мин_тон> <макс_тон>"
    )
    await message.reply(msg_text)
    logger.info("Настройки запрошены пользователем %s", user_id)

@dp.message(Command("setprofit"))
async def set_profit_command(message: types.Message):
    """
    Обработчик команды /setprofit. Устанавливает минимальный процент прибыли.
    """
    user_id = message.from_user.id
    if not await check_subscription_status(user_id, message):
        return

    args = message.text.split()[1:]
    if not args or not args[0].isdigit():
        await message.reply("Пожалуйста, укажите процент прибыли. Пример: /setprofit 7")
        return
    try:
        profit = int(args[0])
        if profit < 0:
            await message.reply("Процент прибыли не может быть отрицательным.")
            return
        current_settings = get_user_actual_settings(user_id)
        current_settings['min_profit'] = profit
        db.set_user_prefs(user_id, profit, current_settings['interval'], current_settings['price_range'][0], current_settings['price_range'][1], current_settings['active'])
        await message.reply(f"Минимальная прибыль установлена на {profit}%.")
        logger.info("Пользователь %s установил мин. прибыль: %d%%", user_id, profit)
    except ValueError:
        await message.reply("Неверный формат числа. Пожалуйста, введите целое число.")

@dp.message(Command("setinterval"))
async def set_interval_command(message: types.Message):
    """
    Обработчик команды /setinterval. Устанавливает интервал проверки аукционов.
    """
    user_id = message.from_user.id
    if not await check_subscription_status(user_id, message):
        return

    args = message.text.split()[1:]
    if not args or not args[0].isdigit():
        await message.reply("Пожалуйста, укажите интервал в секундах. Пример: /setinterval 60")
        return
    try:
        interval = int(args[0])
        if interval < 5:
            await message.reply("Интервал не может быть меньше 5 секунд.")
            return
        current_settings = get_user_actual_settings(user_id)
        current_settings['interval'] = interval
        db.set_user_prefs(user_id, current_settings['min_profit'], interval, current_settings['price_range'][0], current_settings['price_range'][1], current_settings['active'])
        await message.reply(f"Интервал проверки установлен на {interval} секунд.")
        logger.info("Пользователь %s установил интервал: %d сек.", user_id, interval)
    except ValueError:
        await message.reply("Неверный формат числа. Пожалуйста, введите целое число.")

@dp.message(Command("setpricerange"))
async def set_price_range_command(message: types.Message):
    """
    Обработчик команды /setpricerange. Устанавливает диапазон цен для ставок.
    """
    user_id = message.from_user.id
    if not await check_subscription_status(user_id, message):
        return

    args = message.text.split()[1:]
    if len(args) != 2 or not all(arg.replace('.', '', 1).isdigit() for arg in args):
        await message.reply("Пожалуйста, укажите минимальную и максимальную цену. Пример: /setpricerange 10 50")
        return
    try:
        min_price = float(args[0])
        max_price = float(args[1])
        if min_price < 0 or max_price < min_price:
            await message.reply("Неверный диапазон цен. Минимальная цена должна быть >= 0, а максимальная >= минимальной.")
            return
        current_settings = get_user_actual_settings(user_id)
        current_settings['price_range'] = (min_price, max_price)
        db.set_user_prefs(user_id, current_settings['min_profit'], current_settings['interval'], min_price, max_price, current_settings['active'])
        await message.reply(f"Диапазон цен установлен на от {min_price} до {max_price} TON.")
        logger.info("Пользователь %s установил диапазон цен: %.2f-%.2f TON", user_id, min_price, max_price)
    except ValueError:
        await message.reply("Неверный формат числа. Пожалуйста, введите числа.")

# --- Функции оплаты и подписок (Aiogram) ---

@dp.message(Command("subscribe"))
async def subscribe_command(message: types.Message):
    """
    Обработчик команды /subscribe. Предлагает пользователю выбрать способ оплаты.
    """
    user_id = message.from_user.id
    end_date = db.get_subscription_end_date(user_id)
    if end_date > time.time():
        await message.reply(f"У вас уже есть активная подписка до: {time.ctime(end_date)}.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить через Telegram Stars ⭐", callback_data="choose_payment_stars")],
        [InlineKeyboardButton(text="Оплатить через CryptoBot 💰", callback_data="choose_payment_cryptobot")],
    ])
    await message.reply("Выберите способ оплаты:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("choose_payment_"))
async def handle_payment_choice_callback(callback_query: types.CallbackQuery):
    """
    Обработчик колбэк-кнопок для выбора способа оплаты.
    """
    await callback_query.answer() # Отвечаем на callback_query, чтобы убрать "часики"
    user_id = callback_query.from_user.id
    data = callback_query.data # Например: "choose_payment_stars"

    if data == "choose_payment_stars":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{config.SUBSCRIPTION_PRICES['24h']['name_ru']} ({config.SUBSCRIPTION_PRICES['24h']['stars']} ⭐)", callback_data="sub_24h_stars")],
            [InlineKeyboardButton(text=f"{config.SUBSCRIPTION_PRICES['7days']['name_ru']} ({config.SUBSCRIPTION_PRICES['7days']['stars']} ⭐)", callback_data="sub_7days_stars")],
            [InlineKeyboardButton(text=f"{config.SUBSCRIPTION_PRICES['1month']['name_ru']} ({config.SUBSCRIPTION_PRICES['1month']['stars']} ⭐)", callback_data="sub_1month_stars")],
        ])
        await callback_query.message.edit_text("Выберите период подписки (Telegram Stars):", reply_markup=keyboard)
    elif data == "choose_payment_cryptobot":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{config.SUBSCRIPTION_PRICES['24h']['name_ru']} ({config.SUBSCRIPTION_PRICES['24h']['usd']}$)", callback_data="sub_24h_cryptobot")],
            [InlineKeyboardButton(text=f"{config.SUBSCRIPTION_PRICES['7days']['name_ru']} ({config.SUBSCRIPTION_PRICES['7days']['usd']}$)", callback_data="sub_7days_cryptobot")],
            [InlineKeyboardButton(text=f"{config.SUBSCRIPTION_PRICES['1month']['name_ru']} ({config.SUBSCRIPTION_PRICES['1month']['usd']}$)", callback_data="sub_1month_cryptobot")],
        ])
        await callback_query.message.edit_text("Выберите период подписки (CryptoBot):", reply_markup=keyboard)

@dp.message(Command("give"))
async def give_command(message: types.Message):
    user_id = message.from_user.id
    if not db.is_admin(user_id):
        await message.reply("⛔ У вас нет прав для этой команды.")
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].startswith("@"):
        await message.reply("⚠️ Используйте формат: /give @username")
        return

    username = args[1][1:]
    tg_id = db.get_user_id_by_username(username)
    if not tg_id:
        await message.reply("❌ Пользователь не найден в базе.")
        return

    await message.reply(f"✅ Команда /give успешно применена к пользователю @{username} (ID: {tg_id}).")
    # Здесь можешь выдать подписку, активировать доступ и т.п.


@dp.callback_query(F.data.startswith("sub_"))
async def handle_subscription_callback(callback_query: types.CallbackQuery):
    """
    Обработчик колбэк-кнопок для выбора конкретной подписки (период + метод).
    """
    await callback_query.answer()
    user_id = callback_query.from_user.id
    data = callback_query.data # Например: "sub_24h_stars" или "sub_7days_cryptobot"

    parts = data.split('_')
    if len(parts) != 3 or parts[0] != "sub":
        logger.error("Неверный формат callback_data: %s", data)
        await callback_query.message.edit_text("Произошла ошибка. Пожалуйста, попробуйте снова.")
        return

    period = parts[1]
    payment_method = parts[2]

    # Проверяем, есть ли уже активная подписка, чтобы не создавать лишний инвойс
    end_date = db.get_subscription_end_date(user_id)
    if time.time() < end_date:
        await callback_query.message.edit_text(f"У вас уже есть активная подписка до: {time.ctime(end_date)}.")
        return

    if payment_method == "stars":
        price_stars = config.SUBSCRIPTION_PRICES[period]["stars"]
        period_name_ru = config.SUBSCRIPTION_PRICES[period]["name_ru"]

        title = f"Подписка на {period_name_ru}"
        description = f"Доступ к функционалу бота на {period_name_ru}."
        payload = f"{user_id}_{period}_stars_invoice"

        try:
            # Для Telegram Stars provider_token - это ваш токен бота
            await bot.send_invoice(
                chat_id=user_id,
                title=title,
                description=description,
                payload=payload,
                provider_token=config.TELEGRAM_BOT_TOKEN, # Ваш токен бота
                currency="XTR", # Валюта Telegram Stars
                prices=[LabeledPrice(label="Стоимость", amount=int(price_stars * 100))], # Цена в минимальных единицах (100 для Stars)
                is_flexible=False, # Stars не являются гибкими платежами
            )
            await callback_query.message.edit_text(f"Инвойс для оплаты через Telegram Stars на {period_name_ru} отправлен. Проверьте сообщения от бота.")
            logger.info("Инвойс Stars отправлен пользователю %s для периода %s", user_id, period)
        except Exception as e:
            logger.error("Ошибка при отправке инвойса Stars пользователю %s: %s", user_id, e)
            await callback_query.message.edit_text("Не удалось создать инвойс Telegram Stars. Попробуйте еще раз позже.")

    elif payment_method == "cryptobot":
        price_usd = config.SUBSCRIPTION_PRICES[period]["usd"]
        await create_cryptobot_invoice(user_id, period, price_usd, callback_query.message)
        # Сообщение о создании инвойса CryptoBot будет отправлено из create_cryptobot_invoice
        await callback_query.message.edit_text(f"Запрос на создание инвойса CryptoBot для {config.SUBSCRIPTION_PRICES[period]['name_ru']} отправлен. Ожидайте ссылку.")


@dp.pre_checkout_query()
async def pre_checkout_callback(pre_checkout_query: types.PreCheckoutQuery):
    """
    Обработчик предварительной проверки платежа Telegram Stars.
    """
    payload_parts = pre_checkout_query.invoice_payload.split('_')
    # Ожидаем payload в формате user_id_period_stars_invoice
    if len(payload_parts) == 4 and payload_parts[3] == "invoice" and payload_parts[2] == "stars":
        user_id = int(payload_parts[0])
        end_date = db.get_subscription_end_date(user_id)
        if time.time() < end_date:
            await pre_checkout_query.answer(ok=False, error_message="У вас уже есть активная подписка.")
        else:
            await pre_checkout_query.answer(ok=True)
    else:
        await pre_checkout_query.answer(ok=False, error_message="Что-то пошло не так с вашей оплатой.")
        logger.error("Неверный payload в pre_checkout_query: %s", pre_checkout_query.invoice_payload)


@dp.message(F.successful_payment)
async def successful_payment_callback(message: types.Message):
    """
    Обработчик успешной оплаты Telegram Stars.
    """
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    payload_parts = payload.split('_')

    # Ожидаем payload в формате user_id_period_stars_invoice
    if len(payload_parts) == 4 and payload_parts[3] == "invoice" and payload_parts[2] == "stars":
        period = payload_parts[1]
        
        duration_seconds = 0
        if period == "24h":
            duration_seconds = 24 * 3600
        elif period == "7days":
            duration_seconds = 7 * 24 * 3600
        elif period == "1month":
            duration_seconds = 30 * 24 * 3600

        current_end_date = db.get_subscription_end_date(user_id)
        if current_end_date > time.time():
            new_end_date = current_end_date + duration_seconds
        else:
            new_end_date = time.time() + duration_seconds

        db.set_subscription_end_date(user_id, new_end_date)
        period_name_ru = config.SUBSCRIPTION_PRICES[period]["name_ru"]

        # Активируем пользователя в кэше и БД
        user_prefs = get_user_actual_settings(user_id)
        user_prefs['active'] = True
        db.set_user_prefs(user_id, user_prefs['min_profit'], user_prefs['interval'], user_prefs['price_range'][0], user_prefs['price_range'][1], True)

        await message.reply(
            f"🎉 Поздравляем! Ваша подписка на {period_name_ru} успешно активирована.\n"
            f"Действительна до: {time.ctime(new_end_date)}\n"
            "Теперь вы можете использовать все функции бота."
        )
        logger.info("Подписка активирована для пользователя %s через Telegram Stars (Период: %s)", user_id, period)
    else:
        logger.warning("Получен успешный платеж с неизвестным payload: %s", payload)
        await message.reply("Спасибо за оплату! Однако, произошла ошибка при активации подписки. Пожалуйста, свяжитесь с администратором.")

# --- CryptoBot функции (Aiogram) ---

async def create_cryptobot_invoice(user_id: int, period: str, amount_usd: float, message_object: types.Message):
    """
    Создает инвойс через CryptoBot API.
    """
    global bot_username # Объявляем, что будем использовать глобальную переменную
    
    # Убедимся, что username бота доступен
    if bot_username is None:
        try:
            me = await bot.get_me()
            bot_username = me.username
            logger.info("Получен username бота: %s", bot_username)
        except Exception as e:
            logger.error("Не удалось получить username бота: %s", e)
            await message_object.answer("Не удалось получить информацию о боте. Попробуйте еще раз позже.")
            return

    period_name_ru = config.SUBSCRIPTION_PRICES[period]["name_ru"]
    description = f"Подписка на {period_name_ru}."
    payload = f"{user_id}_{period}_cryptobot"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config.CRYPTOBOT_API_URL}/createInvoice",
                headers={"Crypto-Pay-API-Token": config.CRYPTOBOT_API_TOKEN},
                json={
                    "asset": "USDT",
                    "amount": amount_usd,
                    "description": description,
                    "payload": payload,
                    "currency_type": "fiat",
                    "fiat": "USD",
                    # Используем глобальную переменную bot_username
                    "url": f"https://t.me/{bot_username}?start={payload}"
                },
                timeout=10
            )
            response.raise_for_status()
            invoice_data = response.json()

            if invoice_data.get("ok") and invoice_data["result"]:
                invoice_url = invoice_data["result"]["pay_url"]
                invoice_id = invoice_data["result"]["invoice_id"]
                await message_object.answer( # Используем message_object для ответа
                    text=(
                        f"Для оплаты подписки на {period_name_ru} ({amount_usd}$) "
                        f"через CryptoBot перейдите по ссылке:\n{invoice_url}\n\n"
                        "Я сообщу вам, как только оплата будет подтверждена."
                    )
                )
                logger.info("CryptoBot инвойс создан для пользователя %s: %s", user_id, invoice_url)
                
                # Добавляем задачу проверки инвойса в планировщик
                scheduler.add_job(
                    check_cryptobot_invoice_status,
                    IntervalTrigger(seconds=60), # Проверяем каждые 60 секунд
                    args=[user_id, invoice_id, period],
                    id=f"check_crypto_invoice_{invoice_id}",
                    misfire_grace_time=30 # Если пропустили запуск, выполнить в течение 30 сек
                )
                
            else:
                await message_object.answer("Не удалось создать инвойс CryptoBot. Попробуйте еще раз позже.")
                logger.error("Не удалось создать CryptoBot инвойс для пользователя %s: %s", user_id, invoice_data)

    except httpx.HTTPStatusError as e:
        logger.error("HTTP ошибка при создании CryptoBot инвойса: %s", e)
        await message_object.answer("Произошла ошибка при создании инвойса (HTTP).")
    except httpx.RequestError as e:
        logger.error("Сетевая ошибка при создании CryptoBot инвойса: %s", e)
        await message_object.answer("Произошла ошибка сети при создании инвойса.")
    except Exception as e:
        logger.error("Непредвиденная ошибка при создании CryptoBot инвойса: %s", e)
        await message_object.answer("Произошла непредвиденная ошибка при создании инвойса.")

async def check_cryptobot_invoice_status(user_id: int, invoice_id: str, period: str):
    """
    Функция для периодической проверки статуса инвойса CryptoBot.
    Запускается планировщиком APScheduler.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{config.CRYPTOBOT_API_URL}/getInvoices",
                headers={"Crypto-Pay-API-Token": config.CRYPTOBOT_API_TOKEN},
                params={"invoice_ids": invoice_id},
                timeout=10
            )
            response.raise_for_status()
            invoice_status_data = response.json()

            if invoice_status_data.get("ok") and invoice_status_data["result"] and invoice_status_data["result"]["items"]:
                invoice = invoice_status_data["result"]["items"][0]
                if invoice["status"] == "paid":
                    duration_seconds = 0
                    if period == "24h":
                        duration_seconds = 24 * 3600
                    elif period == "7days":
                        duration_seconds = 7 * 24 * 3600
                    elif period == "1month":
                        duration_seconds = 30 * 24 * 3600

                    current_end_date = db.get_subscription_end_date(user_id)
                    if current_end_date > time.time():
                        new_end_date = current_end_date + duration_seconds
                    else:
                        new_end_date = time.time() + duration_seconds

                    db.set_subscription_end_date(user_id, new_end_date)
                    period_name_ru = config.SUBSCRIPTION_PRICES[period]["name_ru"]

                    # Активируем пользователя в кэше и БД
                    user_prefs = get_user_actual_settings(user_id)
                    user_prefs['active'] = True
                    db.set_user_prefs(user_id, user_prefs['min_profit'], user_prefs['interval'], user_prefs['price_range'][0], user_prefs['price_range'][1], True)

                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"🎉 Поздравляем! Ваша подписка на {period_name_ru} успешно активирована через CryptoBot.\n"
                            f"Действительна до: {time.ctime(new_end_date)}\n"
                            "Теперь вы можете использовать все функции бота."
                        )
                    )
                    logger.info("Подписка активирована для пользователя %s через CryptoBot (Инвойс ID: %s, Период: %s)", user_id, invoice_id, period)
                    # Удаляем задачу из планировщика, т.к. оплата получена
                    scheduler.remove_job(f"check_crypto_invoice_{invoice_id}")

                elif invoice["status"] == "active":
                    logger.debug("Инвойс CryptoBot %s всё ещё активен для пользователя %s. Повторная проверка через 60 сек.", invoice_id, user_id)
                    # Задача автоматически перепланируется APScheduler'ом, если не удалена
                else:
                    await bot.send_message(user_id, "Оплата через CryptoBot не подтверждена или инвойс истек. Пожалуйста, попробуйте снова или выберите другой способ оплаты.")
                    logger.warning("Статус инвойса CryptoBot %s: %s для пользователя %s", invoice_id, invoice["status"], user_id)
                    # Удаляем задачу из планировщика
                    scheduler.remove_job(f"check_crypto_invoice_{invoice_id}")
            else:
                logger.error("Не удалось получить статус инвойса CryptoBot для %s: %s", invoice_id, invoice_status_data)
                await bot.send_message(user_id, "Произошла ошибка при проверке статуса инвойса CryptoBot. Пожалуйста, обратитесь в поддержку.")
                scheduler.remove_job(f"check_crypto_invoice_{invoice_id}") # Удаляем задачу

    except httpx.HTTPStatusError as e:
        logger.error("HTTP ошибка при проверке инвойса CryptoBot: %s", e)
    except httpx.RequestError as e:
        logger.error("Сетевая ошибка при проверке инвойса CryptoBot: %s", e)
    except Exception as e:
        logger.error("Непредвиденная ошибка при проверке инвойса CryptoBot: %s", e)


async def main():
    """
    Основная функция, запускающая Telegram-бота.
    """
    global bot_username # Объявляем, что будем использовать глобальную переменную
    
    db.init_db()
    db.set_admin_status(config.TELEGRAM_USER_ID, True)
    logger.info("Администратор %s установлен в базе данных.", config.TELEGRAM_USER_ID)

    # Получаем username бота при запуске
    try:
        me = await bot.get_me()
        bot_username = me.username
        logger.info("Username бота получен: %s", bot_username)
    except Exception as e:
        logger.critical("Критическая ошибка: Не удалось получить username бота при запуске! %s", e)
        # Если не можем получить username, это серьезная проблема, выходим
        import sys
        sys.exit(1) # Выход с ошибкой

    # Запускаем APScheduler
    scheduler.start()
    logger.info("APScheduler запущен.")

    logger.info("Бот запущен и готов принимать команды...")
    try:
        # Запускаем фоновую задачу проверки аукционов как часть loop'а диспетчера
        # Она сама управляет своим интервалом через asyncio.sleep
        asyncio.create_task(check_auctions_job())
        await dp.start_polling(bot) # Запускает опрос обновлений от Telegram
    finally:
        # Закрытие сессии бота и остановка планировщика при завершении работы
        await bot.session.close()
        scheduler.shutdown()
        logger.info("Бот остановлен. APScheduler остановлен.")


if __name__ == "__main__":
    # Для запуска, если `main` уже корутина
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную.")
    except Exception as e:
        logger.error("Непредвиденная ошибка при запуске бота: %s", e)