# TgPlayer

Отправьте боту в Telegram фото, видео или GIF — получите ссылку на плеер. Всё работает через один URL: плеер + webhook.

## Docker (рекомендуется)

```bash
# 1. Создайте .env
cp .env.example .env
# Заполните TELEGRAM_BOT_TOKEN

# 2. Запуск
docker compose up -d

# 3. Откройте http://localhost:8000 (на компьютере)
```

**Важно:** `PUBLIC_URL` — единственный адрес, который используется и для ссылок, и для webhook.

- **С компьютера:** `PUBLIC_URL=http://localhost:8000` — ссылки открываются в браузере на этом же ПК.
- **С телефона (та же Wi‑Fi):** Узнайте IP (`ifconfig | grep "inet "`) и укажите `PUBLIC_URL=http://192.168.x.x:8000`.
- **Из интернета:** [ngrok](https://ngrok.com): `ngrok http 8000`, затем `PUBLIC_URL=https://xxxx.ngrok-free.app`.

Переменные:
- `TELEGRAM_BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
- `PUBLIC_URL` — адрес, по которому доступно приложение (без слэша в конце). Используется и для webhook.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Терминал 1
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Файлы >20 МБ

По умолчанию работает стандартный лимит Telegram (20 МБ). Для больших файлов можно настроить локальный Bot API (опционально):

```
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef...
TELEGRAM_BOT_API_URL=http://telegram-bot-api:8081
```

Перезапустите: `docker compose up -d --build`.
