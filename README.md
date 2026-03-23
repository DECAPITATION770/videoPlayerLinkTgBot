# TgPlayer

Telegram-бот: отправьте фото, видео или GIF — получите ссылку на плеер. Один сервис: FastAPI + webhook.

## Деплой

### 1. Docker (VPS, свой сервер)

```bash
cp .env.example .env
# Заполните TELEGRAM_BOT_TOKEN и PUBLIC_URL (например https://your-domain.com)

docker compose up -d
```

**Переменные:**
- `TELEGRAM_BOT_TOKEN` — от [@BotFather](https://t.me/BotFather)
- `PUBLIC_URL` — публичный URL приложения (без `/` в конце)
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` — для файлов >20 МБ, получить на [my.telegram.org](https://my.telegram.org)

### 2. Railway

1. Создайте проект → **Deploy from GitHub**
2. В **Variables** добавьте:
   - `TELEGRAM_BOT_TOKEN`
   - `PUBLIC_URL` = `https://ВАШ-ПРОЕКТ.up.railway.app`
3. В **Settings** → **Docker** включите Dockerfile (или Root Directory)
4. Railway сам найдёт Dockerfile и соберёт образ

### 3. Render

1. **New** → **Web Service**, подключите репозиторий
2. **Environment**: Docker
3. В **Environment Variables**:
   - `TELEGRAM_BOT_TOKEN`
   - `PUBLIC_URL` = `https://ВАШ-СЕРВИС.onrender.com`
4. Deploy

### 4. Fly.io

```bash
fly launch
# Укажите регион

fly secrets set TELEGRAM_BOT_TOKEN=ваш_токен
fly secrets set PUBLIC_URL=https://ВАШ-ПРОЕКТ.fly.dev

fly deploy
```

### 5. Локально (разработка)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# .env: TELEGRAM_BOT_TOKEN, PUBLIC_URL=http://localhost:8000
uvicorn app:app --host 0.0.0.0 --port 8000
```

С телефона (та же Wi‑Fi): узнайте IP (`ifconfig | grep "inet "`), `PUBLIC_URL=http://192.168.x.x:8000`.

Из интернета: [ngrok](https://ngrok.com) → `ngrok http 8000` → `PUBLIC_URL=https://xxxx.ngrok-free.app`.

---

## Файлы >20 МБ

Docker-конфигурация включает локальный Bot API. В `.env` укажите `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` с [my.telegram.org](https://my.telegram.org) → API development tools.
