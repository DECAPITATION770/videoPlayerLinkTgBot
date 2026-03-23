"""
Telegram Media Bot - стриминг медиа файлов напрямую из Telegram
Без скачивания, мгновенная раздача
"""

import asyncio
import mimetypes
from pathlib import Path
from datetime import datetime
import hashlib

from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
from aiohttp import web

# ============================================================================
# НАСТРОЙКИ - ИЗМЕНИТЕ ЭТИ ЗНАЧЕНИЯ
# ============================================================================

# Получите от https://my.telegram.org/apps
API_ID = 123456789  
API_HASH = 'HEis8dfhsdfjkshfdkjshjkdfjhksdjhfkjksdfk'  # Ваш API Hash
BOT_TOKEN = 'xxxxxx:aaaaaaaaaaaaaaaaaaaaa'  # Токен от @BotFather

# Настройки сервера
DOMAIN = 'https://ngrokFreeapp.ngrok-free.app'  # Ваш домен с HTTPS
PORT = 8070  # Порт веб-сервера


# ============================================================================
# КОД БОТА
# ============================================================================

# Telegram клиент (start вызывается в main() — внутри event loop)
bot = TelegramClient('media_bot', API_ID, API_HASH)

# Хранилище медиа {file_id: {message_id, chat_id, filename, mime_type, size}}
media_db = {}


def generate_file_id():
    """Генерировать уникальный ID для файла"""
    timestamp = str(datetime.now().timestamp())
    random_hash = hashlib.md5(timestamp.encode()).hexdigest()[:12]
    return random_hash


def get_file_extension(filename):
    """Получить расширение файла"""
    if not filename:
        return ''
    return Path(filename).suffix.lower()


def get_media_type(filename, mime_type=None):
    """Определить тип медиа файла"""
    ext = get_file_extension(filename)
    
    video_exts = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v'}
    audio_exts = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma'}
    image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.svg'}
    
    if ext in video_exts:
        return 'video'
    elif ext in audio_exts:
        return 'audio'
    elif ext in image_exts:
        return 'image'
    elif ext == '.gif':
        return 'gif'
    
    if mime_type:
        if mime_type.startswith('video/'):
            return 'video'
        elif mime_type.startswith('audio/'):
            return 'audio'
        elif mime_type.startswith('image/gif'):
            return 'gif'
        elif mime_type.startswith('image/'):
            return 'image'
    
    return 'file'


def get_filename_from_message(message):
    """Извлечь имя файла из сообщения"""
    filename = None
    mime_type = None
    size = 0
    
    if message.photo:
        filename = f'photo_{generate_file_id()}.jpg'
        mime_type = 'image/jpeg'
        size = message.photo.sizes[-1].size if hasattr(message.photo.sizes[-1], 'size') else 0
    
    elif message.video:
        mime_type = message.video.mime_type or 'video/mp4'
        size = message.video.size
        
        for attr in message.video.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break
        
        if not filename:
            ext = mimetypes.guess_extension(mime_type) or '.mp4'
            filename = f'video_{generate_file_id()}{ext}'
    
    elif message.audio:
        mime_type = message.audio.mime_type or 'audio/mpeg'
        size = message.audio.size
        
        for attr in message.audio.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break
        
        if not filename:
            ext = mimetypes.guess_extension(mime_type) or '.mp3'
            filename = f'audio_{generate_file_id()}{ext}'
    
    elif message.document:
        mime_type = message.document.mime_type or 'application/octet-stream'
        size = message.document.size
        
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break
        
        if not filename:
            ext = mimetypes.guess_extension(mime_type) or '.bin'
            filename = f'file_{generate_file_id()}{ext}'
    
    elif message.animation:
        mime_type = message.animation.mime_type or 'image/gif'
        size = message.animation.size
        
        for attr in message.animation.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break
        
        if not filename:
            filename = f'animation_{generate_file_id()}.gif'
    
    elif message.voice:
        mime_type = message.voice.mime_type or 'audio/ogg'
        size = message.voice.size
        filename = f'voice_{generate_file_id()}.ogg'
    
    return filename, mime_type, size


@bot.on(events.NewMessage)
async def handle_message(event):
    """Обработчик всех сообщений"""
    
    if not event.media:
        await event.reply('👋 Привет! Отправь мне медиа-файл (видео, фото, GIF, аудио, документ) любого размера, и я дам тебе ссылку для стриминга!')
        return
    
    status_msg = await event.reply('⚡ Обрабатываю...')
    
    try:
        filename, mime_type, size = get_filename_from_message(event.message)
        
        if not filename:
            await status_msg.edit('❌ Не удалось определить тип файла')
            return
        
        file_id = generate_file_id()
        
        # Сохраняем в базу БЕЗ СКАЧИВАНИЯ!
        media_db[file_id] = {
            'message_id': event.message.id,
            'chat_id': event.chat_id,
            'filename': filename,
            'mime_type': mime_type,
            'size': size
        }
        
        file_url = f'{DOMAIN}/file/{file_id}'
        player_url = f'{DOMAIN}/play/{file_id}'
        media_type = get_media_type(filename, mime_type)
        
        media_name = 'файл'
        if event.photo:
            media_name = 'фото'
        elif event.video:
            media_name = 'видео'
        elif event.audio:
            media_name = 'аудио'
        elif event.animation:
            media_name = 'GIF'
        elif event.voice:
            media_name = 'голосовое'
        
        size_mb = size / (1024 * 1024)
        size_text = f'{size_mb:.1f} МБ' if size_mb > 1 else f'{size / 1024:.1f} КБ'
        
        if media_type == 'image':
            await status_msg.edit(
                f'✅ {media_name.title()} готово! ({size_text})\n\n'
                f'🔗 Ссылка:\n{file_url}'
            )
        else:
            await status_msg.edit(
                f'✅ {media_name.title()} готово! ({size_text})\n\n'
                f'🎬 Плеер:\n{player_url}\n\n'
                f'🔗 Прямая ссылка:\n{file_url}'
            )
    
    except Exception as e:
        print(f'Ошибка: {e}')
        await status_msg.edit('❌ Ошибка при обработке')


# ============================================================================
# ВЕБ-СЕРВЕР СО СТРИМИНГОМ
# ============================================================================

async def stream_telegram_file(request):
    """Стриминг файла напрямую из Telegram"""
    file_id = request.match_info['file_id']
    
    if file_id not in media_db:
        return web.Response(text='Файл не найден', status=404)
    
    media_info = media_db[file_id]
    
    try:
        message = await bot.get_messages(
            media_info['chat_id'],
            ids=media_info['message_id']
        )
        
        if not message or not message.media:
            return web.Response(text='Медиа не найдено', status=404)
        
        mime_type = media_info['mime_type'] or 'application/octet-stream'
        file_size = media_info['size']
        
        # Поддержка Range для видео
        range_header = request.headers.get('Range')
        
        if range_header:
            range_match = range_header.replace('bytes=', '').split('-')
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if len(range_match) > 1 and range_match[1] else file_size - 1
            
            chunk_size = min(end - start + 1, file_size - start)
            
            response = web.StreamResponse(
                status=206,
                reason='Partial Content',
                headers={
                    'Content-Type': mime_type,
                    'Content-Range': f'bytes {start}-{end}/{file_size}',
                    'Content-Length': str(chunk_size),
                    'Accept-Ranges': 'bytes',
                    'Cache-Control': 'public, max-age=31536000',
                }
            )
            
            await response.prepare(request)
            
            async for chunk in bot.iter_download(message.media, offset=start, limit=chunk_size):
                await response.write(chunk)
            
            await response.write_eof()
            return response
        
        else:
            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': mime_type,
                    'Content-Length': str(file_size),
                    'Accept-Ranges': 'bytes',
                    'Cache-Control': 'public, max-age=31536000',
                }
            )
            
            await response.prepare(request)
            
            async for chunk in bot.iter_download(message.media):
                await response.write(chunk)
            
            await response.write_eof()
            return response
    
    except Exception as e:
        print(f'Ошибка стриминга: {e}')
        return web.Response(text='Ошибка загрузки', status=500)


async def handle_player(request):
    """Веб-плеер"""
    file_id = request.match_info['file_id']
    
    if file_id not in media_db:
        return web.Response(text='Файл не найден', status=404)
    
    media_info = media_db[file_id]
    filename = media_info['filename']
    file_url = f'{DOMAIN}/file/{file_id}'
    media_type = get_media_type(filename, media_info['mime_type'])
    
    if media_type in ('video', 'gif'):
        html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Видео</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #000; display: flex; justify-content: center; align-items: center; min-height: 100vh; overflow: hidden; }}
        .player-container {{ position: relative; width: 100%; height: 100vh; display: flex; justify-content: center; align-items: center; }}
        video {{ max-width: 100%; max-height: 100vh; width: auto; height: auto; display: block; object-fit: contain; }}
        .play-button {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 80px; height: 80px; background: rgba(255, 255, 255, 0.9); border-radius: 50%; cursor: pointer; display: flex; justify-content: center; align-items: center; transition: all 0.3s; z-index: 10; }}
        .play-button:hover {{ background: rgba(255, 255, 255, 1); transform: translate(-50%, -50%) scale(1.1); }}
        .play-button::after {{ content: ''; width: 0; height: 0; border-left: 25px solid #000; border-top: 15px solid transparent; border-bottom: 15px solid transparent; margin-left: 5px; }}
        .play-button.hidden {{ opacity: 0; pointer-events: none; }}
    </style>
</head>
<body>
    <div class="player-container">
        <video id="video" src="{file_url}" {"loop" if media_type == "gif" else ""} playsinline preload="metadata"></video>
        <div class="play-button" id="playBtn"></div>
    </div>
    <script>
        const video = document.getElementById('video');
        const playBtn = document.getElementById('playBtn');
        
        playBtn.addEventListener('click', async () => {{
            playBtn.classList.add('hidden');
            try {{
                await video.play();
                const elem = video;
                if (elem.requestFullscreen) await elem.requestFullscreen();
                else if (elem.webkitRequestFullscreen) await elem.webkitRequestFullscreen();
                else if (elem.mozRequestFullScreen) await elem.mozRequestFullScreen();
                
                const isLandscape = video.videoWidth > video.videoHeight;
                if (isLandscape && screen.orientation && screen.orientation.lock) {{
                    screen.orientation.lock('landscape').catch(() => {{}});
                }}
            }} catch (err) {{
                playBtn.classList.remove('hidden');
            }}
        }});
        
        video.addEventListener('play', () => playBtn.classList.add('hidden'));
        video.addEventListener('pause', () => {{ if (!document.fullscreenElement) playBtn.classList.remove('hidden'); }});
        video.addEventListener('ended', () => playBtn.classList.remove('hidden'));
        document.addEventListener('fullscreenchange', () => {{ if (!document.fullscreenElement) video.pause(); }});
    </script>
</body>
</html>'''
    
    elif media_type == 'audio':
        html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Аудио</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); display: flex; justify-content: center; align-items: center; min-height: 100vh; font-family: -apple-system, sans-serif; }}
        .player {{ background: rgba(255, 255, 255, 0.95); padding: 40px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3); text-align: center; max-width: 400px; width: 90%; }}
        .play-button {{ width: 80px; height: 80px; background: #667eea; border-radius: 50%; cursor: pointer; display: flex; justify-content: center; align-items: center; margin: 0 auto 20px; transition: all 0.3s; }}
        .play-button:hover {{ transform: scale(1.1); background: #764ba2; }}
        .play-button::after {{ content: ''; width: 0; height: 0; border-left: 20px solid #fff; border-top: 12px solid transparent; border-bottom: 12px solid transparent; margin-left: 5px; }}
        .play-button.playing::after {{ border: none; width: 6px; height: 24px; background: linear-gradient(to right, #fff 0%, #fff 35%, transparent 35%, transparent 65%, #fff 65%, #fff 100%); margin-left: 0; }}
        h2 {{ color: #333; }}
    </style>
</head>
<body>
    <div class="player">
        <div class="play-button" id="playBtn"></div>
        <h2>🎵 Аудио</h2>
        <audio id="audio" src="{file_url}"></audio>
    </div>
    <script>
        const audio = document.getElementById('audio');
        const playBtn = document.getElementById('playBtn');
        playBtn.addEventListener('click', () => {{
            if (audio.paused) {{ audio.play(); playBtn.classList.add('playing'); playBtn.style.background = '#764ba2'; }}
            else {{ audio.pause(); playBtn.classList.remove('playing'); playBtn.style.background = '#667eea'; }}
        }});
        audio.addEventListener('ended', () => {{ playBtn.classList.remove('playing'); playBtn.style.background = '#667eea'; }});
    </script>
</body>
</html>'''
    
    elif media_type == 'image':
        html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Изображение</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #000; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
        img {{ max-width: 100%; max-height: 100vh; display: block; }}
    </style>
</head>
<body>
    <img src="{file_url}" alt="Изображение">
</body>
</html>'''
    
    else:
        html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Файл</title>
    <style>
        body {{ background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; font-family: -apple-system, sans-serif; }}
        .container {{ background: white; padding: 40px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; }}
        a {{ color: #667eea; text-decoration: none; font-size: 18px; padding: 12px 24px; background: #f0f0f0; border-radius: 8px; display: inline-block; margin-top: 20px; }}
        a:hover {{ background: #e0e0e0; }}
    </style>
</head>
<body>
    <div class="container">
        <h2>📁 Файл</h2>
        <a href="{file_url}" download>Скачать</a>
    </div>
</body>
</html>'''
    
    return web.Response(text=html, content_type='text/html')


async def start_web_server():
    """Запуск веб-сервера"""
    app = web.Application(client_max_size=1024**3 * 2)
    app.router.add_get('/file/{file_id}', stream_telegram_file)
    app.router.add_get('/play/{file_id}', handle_player)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    print(f'✅ Веб-сервер запущен на порту {PORT}')


async def main():
    """Главная функция"""
    print('🤖 Telegram Media Bot (Streaming Mode)')
    print('⚡ Файлы стримятся напрямую из Telegram!')
    
    # Сначала подключаем бота (важно: в том же event loop, что и веб-сервер)
    await bot.start(bot_token=BOT_TOKEN)
    
    await start_web_server()
    
    print('✅ Бот готов!')
    print(f'📡 {DOMAIN}')
    print(f'🔌 Порт {PORT}')
    
    await bot.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())



