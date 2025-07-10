# --- Конфигурация Telegram Бота ---
# ЗАМЕНИТЕ ЭТО: Токен вашего Telegram бота, полученный от @BotFather
TELEGRAM_BOT_TOKEN = '8129732211:AAEYZBMnHaACOHszn4zbsI01JQtLsseh_ro'
# ЗАМЕНИТЕ ЭТО: Ваш ID чата для тестирования.
# Можно получить, отправив сообщение боту @userinfobot и посмотрев 'id'.
TELEGRAM_USER_ID = 1291677325 # Ваш Telegram ID как администратора

# --- Авторизационные данные для Tonnel Network ---
# ВНИМАНИЕ: Этот токен AUTH_DATA крайне чувствителен и, скорее всего, временный.
# Для стабильной работы бота его нужно регулярно обновлять из сетевых запросов в DevTools браузера.
# Ищите запрос, содержащий "authData" в теле или заголовках.
AUTH_DATA = "user=%7B%22id%22%3A1291677325%2C%22first_name%22%3A%22%D0%94%D0%B8%D0%BC%D0%B0%22%2C%22last_name%22%3A%22%22%2C%22username%22%3A%22otcseller132%22%2C%22language_code%22%3A%22ru%22%2C%22allows_write_to_pm%22%3Atrue%2C%22photo_url%22%3A%22https%3A%5C%2F%5C%2Ft.me%5C%2Fi%5C%2Fuserpic%5C%2F320%5C%2Fi_6TgYWBnuSgDGilK8pmZuIUf-kC7SV2VP9GETs7WwU.svg%22%7D&chat_instance=8370872738174365064&chat_type=sender&auth_date=1751722177&signature=duBioXksZJiZTCpo8piGJBK-EnxXMLqrFen58V3fS7voQWEwiBXM_vq73hLr0NZheuWbjhYi7B4WUR_asIl6BA&hash=5f61305b56acfda600e24c57668110ea80a3ece4a25be79e2189527b3d06f011"

# --- CryptoBot API Конфигурация ---
# ЗАМЕНИТЕ ЭТО: Ваш API токен от CryptoBot (получается в @CryptoBot -> BotFather -> API Tokens)
CRYPTOBOT_API_TOKEN = '425705:AAavBiceorKiZZ4Pqogsjyqy8edwAfyVVwR'
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"

# --- Настройки по умолчанию и цены подписок ---
DEFAULT_INTERVAL = 30 # Интервал проверки аукционов по умолчанию (в секундах)
DEFAULT_MIN_PROFIT = 5 # Минимальный процент прибыли по умолчанию

SUBSCRIPTION_PRICES = {
    "24h": {"stars": 15, "usd": 0.4, "name_ru": "24 часа"},
    "7days": {"stars": 80, "usd": 2.0, "name_ru": "7 дней"},
    "1month": {"stars": 350, "usd": 6.0, "name_ru": "1 месяц"}
}