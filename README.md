# Telegram Media Bot 🎬

Простой бот для загрузки медиа-файлов любого размера (включая 200+ МБ) с веб-плеером.

## ✨ Возможности

- ✅ Файлы **любого размера** (200 МБ, 500 МБ, 1 ГБ+)
- ✅ Видео, фото, GIF, аудио, документы
- ✅ HTTPS ссылки на все файлы
- ✅ Адаптивный плеер для всех устройств
- ✅ Превью с кнопкой Play посередине
- ✅ Автоматический полноэкранный режим
- ✅ Правильная ориентация экрана (горизонтальное видео → горизонтальный экран)
- ✅ Без потери качества контента
- ✅ **Один файл** - всё включено!

## 📦 Установка

1. Установите Python 3.8+ если еще не установлен

2. Установите зависимости:
```bash
pip install -r requirements.txt
```

## ⚙️ Настройка

Откройте файл `bot.py` и измените настройки в начале файла:

```python
# Получите от https://my.telegram.org/apps
API_ID = 12345678  # Ваш API ID
API_HASH = 'your_api_hash_here'  # Ваш API Hash
BOT_TOKEN = 'your_bot_token_here'  # Токен от @BotFather

# Настройки сервера
DOMAIN = 'https://yourdomain.com'  # Ваш домен с HTTPS
PORT = 8080  # Порт веб-сервера
```

### Как получить API_ID и API_HASH:

1. Перейдите на https://my.telegram.org/apps
2. Войдите с вашим номером телефона
3. Создайте новое приложение (любое название)
4. Скопируйте **API ID** и **API Hash**

### Как получить BOT_TOKEN:

1. Напишите [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте команду `/newbot`
3. Следуйте инструкциям (придумайте имя и username)
4. Скопируйте токен

## 🚀 Запуск

```bash
python bot.py
```

Или в фоне:
```bash
nohup python bot.py &
```

## 📖 Как использовать

1. Отправьте боту любой медиа-файл
2. Дождитесь загрузки
3. Получите:
   - **🎬 Плеер** - красивый веб-плеер с превью
   - **🔗 Прямая ссылка** - прямая ссылка на файл

## 🌐 Деплой на сервер

### С Nginx (рекомендуется):

1. Установите Nginx:
```bash
sudo apt update
sudo apt install nginx
```

2. Настройте Nginx (`/etc/nginx/sites-available/mediabot`):
```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 2000M;
    }
}
```

3. Включите сайт:
```bash
sudo ln -s /etc/nginx/sites-available/mediabot /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

4. Установите SSL (Let's Encrypt):
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

### Автозапуск через systemd:

Создайте файл `/etc/systemd/system/mediabot.service`:
```ini
[Unit]
Description=Telegram Media Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/bot
ExecStart=/usr/bin/python3 /path/to/bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Запустите:
```bash
sudo systemctl daemon-reload
sudo systemctl enable mediabot
sudo systemctl start mediabot
sudo systemctl status mediabot
```

## 🔧 Технологии

- **Telethon** - MTProto клиент для Telegram
- **aiohttp** - Асинхронный веб-сервер
- Python 3.8+

## 💡 Преимущества Telethon

- ✅ Нет ограничений на размер файлов (Telegram API позволяет до 2 ГБ)
- ✅ Быстрая загрузка больших файлов
- ✅ Полный доступ к Telegram API
- ✅ Асинхронная работа

## 📝 Примечания

- Бот автоматически создаст папку `uploads` для файлов
- Файлы хранятся на вашем сервере
- Рекомендуется настроить автоочистку старых файлов (cron)

## 🔒 Безопасность

- Используйте HTTPS (настройте SSL)
- Не делитесь API_HASH и BOT_TOKEN
- Настройте firewall на сервере
- Ограничьте доступ к папке uploads

## 📄 Лицензия

MIT License - используйте свободно!

---

**Сделано с ❤️ для работы с медиа в Telegram**