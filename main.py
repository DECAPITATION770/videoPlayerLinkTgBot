#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Media Bot - Ultimate Version v3
Умный плеер для ВСЕХ типов файлов
"""

import asyncio
import mimetypes
import logging
import secrets
import zipfile
import io
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict
import signal
import ssl
import sqlite3

from telethon import TelegramClient, Button
from telethon.tl.types import (
    DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio,
    MessageMediaPhoto, MessageMediaDocument
)
from aiohttp import web
import aiohttp

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name
    from pygments.formatters import HtmlFormatter
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False

# ============================================================================
# НАСТРОЙКИ
# ============================================================================
from dotenv import load_dotenv
load_dotenv()
import os

API_ID      = os.getenv("API_ID")
API_HASH    = os.getenv("API_HASH")
BOT_TOKEN   = os.getenv("BOT_TOKEN")

DOMAIN         = os.getenv("DOMAIN")
PORT           = os.getenv("PORT")
WEBHOOK_PATH   = os.getenv("WEBHOOK_PATH")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

ALLOWED_CHAT_IDS: list = []   # пусто = все

DB_PATH          = 'media_bot.db'
LOG_LEVEL        = logging.INFO
LOG_FILE         = 'bot.log'
QUEUE_SIZE       = 200
STREAM_CHUNK     = 512 * 1024   # 512 КБ — кратно 4096

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# БАЗА ДАННЫХ
# ============================================================================

class MediaDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS media (
                    file_id      TEXT PRIMARY KEY,
                    message_id   INTEGER NOT NULL,
                    chat_id      INTEGER NOT NULL,
                    filename     TEXT NOT NULL,
                    mime_type    TEXT,
                    size         INTEGER NOT NULL DEFAULT 0,
                    media_type   TEXT,
                    is_photo     INTEGER DEFAULT 0,
                    duration     INTEGER,
                    width        INTEGER,
                    height       INTEGER,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 0,
                    last_access  TIMESTAMP
                )
            ''')
            # Миграция: добавляем is_photo если колонки ещё нет
            try:
                conn.execute('ALTER TABLE media ADD COLUMN is_photo INTEGER DEFAULT 0')
            except Exception:
                pass
            conn.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON media(created_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_id    ON media(chat_id)')
        logger.info(f"БД инициализирована: {self.db_path}")

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_media(self, *, file_id, message_id, chat_id, filename,
                  mime_type, size, media_type=None, is_photo=False,
                  duration=None, width=None, height=None):
        with self._conn() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO media
                    (file_id, message_id, chat_id, filename, mime_type,
                     size, media_type, is_photo, duration, width, height)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ''', (file_id, message_id, chat_id, filename, mime_type,
                  size, media_type, int(is_photo), duration, width, height))
        logger.info(f"Сохранён: {file_id} — {filename}")

    def get_media(self, file_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                'SELECT * FROM media WHERE file_id = ?', (file_id,)
            ).fetchone()
            if row:
                conn.execute('''
                    UPDATE media
                    SET access_count = access_count + 1,
                        last_access  = CURRENT_TIMESTAMP
                    WHERE file_id = ?
                ''', (file_id,))
        return dict(row) if row else None

    def get_stats(self) -> Dict:
        with self._conn() as conn:
            total     = conn.execute('SELECT COUNT(*) FROM media').fetchone()[0]
            tot_size  = conn.execute('SELECT SUM(size) FROM media').fetchone()[0] or 0
            tot_acc   = conn.execute('SELECT SUM(access_count) FROM media').fetchone()[0] or 0
        return {
            'total_files':   total,
            'total_size_gb': round(tot_size / (1024 ** 3), 2),
            'total_access':  tot_acc,
        }

    def get_recent(self, limit=50, offset=0) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT * FROM media ORDER BY created_at DESC LIMIT ? OFFSET ?',
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit=50) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM media WHERE filename LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f'%{query}%', limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def total_count(self) -> int:
        with self._conn() as conn:
            return conn.execute('SELECT COUNT(*) FROM media').fetchone()[0]


# ============================================================================
# ГЛОБАЛЬНЫЕ ОБЪЕКТЫ
# ============================================================================

bot = None  # Инициализируется в main() для Python 3.14 (требуется event loop)
db  = MediaDatabase(DB_PATH)
_message_queue: asyncio.Queue = None


# ============================================================================
# УТИЛИТЫ
# ============================================================================

def generate_file_id() -> str:
    return secrets.token_hex(8)

def get_file_extension(filename: str) -> str:
    return Path(filename).suffix.lower() if filename else ''

def get_media_type(filename: str, mime_type: str = '') -> str:
    ext = get_file_extension(filename)
    VIDEO   = {'.mp4','.webm','.mov','.avi','.mkv','.flv','.wmv','.m4v','.mpeg','.mpg'}
    AUDIO   = {'.mp3','.wav','.ogg','.m4a','.flac','.aac','.wma','.opus'}
    IMAGE   = {'.jpg','.jpeg','.png','.webp','.bmp','.svg'}
    CODE    = {'.html','.htm','.css','.js','.json','.xml','.py','.java','.cpp',
               '.c','.h','.php','.rb','.go','.rs','.ts','.tsx','.jsx','.vue',
               '.swift','.kt','.sql','.sh','.bash','.yml','.yaml','.md',
               '.txt','.log','.conf','.ini','.toml','.env'}
    ARCHIVE = {'.zip','.rar','.7z','.tar','.gz','.bz2','.xz'}

    if ext in VIDEO:             return 'video'
    if ext in AUDIO:             return 'audio'
    if ext in IMAGE:             return 'image'
    if ext == '.gif':            return 'gif'
    if ext == '.pdf':            return 'pdf'
    if ext in ('.doc','.docx'):  return 'word'
    if ext in ('.xls','.xlsx'):  return 'excel'
    if ext in ('.ppt','.pptx'):  return 'powerpoint'
    if ext in CODE:              return 'code'
    if ext == '.zip':            return 'zip'
    if ext in ARCHIVE:           return 'archive'

    mt = mime_type or ''
    if mt.startswith('video/'):               return 'video'
    if mt.startswith('audio/'):               return 'audio'
    if 'gif' in mt:                           return 'gif'
    if mt.startswith('image/'):               return 'image'
    if 'pdf' in mt:                           return 'pdf'
    if 'word' in mt or 'document' in mt:      return 'word'
    if 'excel' in mt or 'spreadsheet' in mt:  return 'excel'
    if 'presentation' in mt:                  return 'powerpoint'
    if 'zip' in mt:                           return 'zip'
    if 'text' in mt:                          return 'code'
    return 'file'

def format_size(b) -> str:
    b = b or 0
    for u in ('Б','КБ','МБ','ГБ'):
        if b < 1024: return f'{b:.1f} {u}'
        b /= 1024
    return f'{b:.1f} ТБ'

def format_duration(sec) -> str:
    if not sec: return ''
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

def is_allowed_chat(chat_id: int) -> bool:
    return not ALLOWED_CHAT_IDS or chat_id in ALLOWED_CHAT_IDS

def escape_html(text: str) -> str:
    return str(text).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def extract_media_info(message) -> tuple:
    """Возвращает (filename, mime_type, size, is_photo, duration, width, height)."""
    filename = mime_type = None
    size = duration = width = height = 0
    is_photo = False

    if message.photo:
        is_photo  = True
        filename  = f'photo_{generate_file_id()}.jpg'
        mime_type = 'image/jpeg'
        # Перебираем sizes в обратном порядке — ищем PhotoSize с реальным .size
        # PhotoStrippedSize и PhotoCachedSize не имеют .size — пропускаем
        for ps in reversed(message.photo.sizes):
            s = getattr(ps, 'size', 0)
            if s:
                size = s
                width  = getattr(ps, 'w', 0)
                height = getattr(ps, 'h', 0)
                break

    elif message.video:
        mime_type = message.video.mime_type or 'video/mp4'
        size      = message.video.size or 0
        for a in message.video.attributes:
            if isinstance(a, DocumentAttributeFilename): filename = a.file_name
            elif isinstance(a, DocumentAttributeVideo):
                duration, width, height = a.duration, a.w, a.h
        if not filename:
            filename = f'video_{generate_file_id()}{mimetypes.guess_extension(mime_type) or ".mp4"}'

    elif message.audio:
        mime_type = message.audio.mime_type or 'audio/mpeg'
        size      = message.audio.size or 0
        for a in message.audio.attributes:
            if isinstance(a, DocumentAttributeFilename): filename = a.file_name
            elif isinstance(a, DocumentAttributeAudio):  duration = a.duration
        if not filename:
            filename = f'audio_{generate_file_id()}{mimetypes.guess_extension(mime_type) or ".mp3"}'

    elif message.document:
        mime_type = message.document.mime_type or 'application/octet-stream'
        size      = message.document.size or 0
        for a in message.document.attributes:
            if isinstance(a, DocumentAttributeFilename): filename = a.file_name
        if not filename:
            filename = f'file_{generate_file_id()}{mimetypes.guess_extension(mime_type) or ".bin"}'

    elif message.animation:
        mime_type = message.animation.mime_type or 'image/gif'
        size      = message.animation.size or 0
        for a in message.animation.attributes:
            if isinstance(a, DocumentAttributeFilename): filename = a.file_name
        if not filename:
            filename = f'animation_{generate_file_id()}.gif'

    elif message.voice:
        mime_type = message.voice.mime_type or 'audio/ogg'
        size      = message.voice.size or 0
        duration  = message.voice.duration or 0
        filename  = f'voice_{generate_file_id()}.ogg'

    return filename, mime_type, size, is_photo, duration, width, height


# ============================================================================
# ОЧЕРЕДЬ
# ============================================================================

async def queue_worker():
    while True:
        message = await _message_queue.get()
        try:
            await _process_message(message)
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}", exc_info=True)
        finally:
            _message_queue.task_done()

async def handle_telegram_message(message):
    if not is_allowed_chat(message.chat_id):
        await bot.send_message(message.chat_id, '❌ Нет доступа')
        return
    try:
        _message_queue.put_nowait(message)
    except asyncio.QueueFull:
        await message.reply('⏳ Очередь переполнена, попробуйте позже')

async def _process_message(message):
    if not message.media:
        stats    = db.get_stats()
        list_url = f'{DOMAIN}/list'
        await bot.send_message(
            message.chat_id,
            f'👋 **Telegram Media Bot**\n\n'
            f'📊 Статистика:\n'
            f'📁 Файлов: **{stats["total_files"]}**\n'
            f'💾 Размер: **{stats["total_size_gb"]} ГБ**\n'
            f'👁 Просмотров: **{stats["total_access"]}**\n\n'
            f'📂 [Все файлы]({list_url})\n\n'
            f'⚡ Отправь мне любой файл!',
            parse_mode='markdown'
        )
        return

    filename, mime_type, size, is_photo, duration, width, height = extract_media_info(message)
    if not filename:
        await message.reply('❌ Не удалось определить тип файла')
        return

    file_id    = generate_file_id()
    media_type = get_media_type(filename, mime_type)

    db.add_media(
        file_id=file_id, message_id=message.id, chat_id=message.chat_id,
        filename=filename, mime_type=mime_type, size=size,
        media_type=media_type, is_photo=is_photo,
        duration=duration, width=width, height=height
    )

    file_url   = f'{DOMAIN}/file/{file_id}'
    player_url = f'{DOMAIN}/play/{file_id}'
    info_parts = [format_size(size)]
    if duration:       info_parts.append(format_duration(duration))
    if width and height: info_parts.append(f'{width}×{height}')

    ICONS = {
        'video':'🎬','gif':'🎬','audio':'🎵','image':'🖼','pdf':'📄',
        'word':'📝','excel':'📊','powerpoint':'📑','code':'💻','zip':'📦',
    }
    emoji = ICONS.get(media_type, '📂')
    LABELS = {
        'video':'Смотреть','gif':'Смотреть','audio':'Слушать','image':'Открыть',
        'pdf':'Читать PDF','word':'Открыть','excel':'Открыть','powerpoint':'Открыть',
        'code':'Просмотр кода','zip':'Содержимое',
    }
    label = LABELS.get(media_type, 'Открыть')

    # Для изображений кнопка ведёт на /play (там красивый просмотрщик)
    buttons = [
        [Button.url(f'{emoji} {label}', player_url)],
        [Button.url('🔗 Скачать', file_url)],
    ]

    await message.reply(
        f'📊 {" • ".join(info_parts)}',
        buttons=buttons, link_preview=False
    )
    logger.info(f"Ссылки отправлены: {filename} ({format_size(size)})")


# ============================================================================
# WEBHOOK
# ============================================================================

async def webhook_handler(request: web.Request):
    if WEBHOOK_SECRET:
        if request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
            return web.Response(status=403)
    try:
        data = await request.json()
        if 'message' in data:
            d  = data['message']
            msg = await bot.get_messages(d['chat']['id'], ids=d['message_id'])
            if msg:
                await handle_telegram_message(msg)
        return web.Response(text='OK')
    except Exception as e:
        logger.error(f"Webhook: {e}", exc_info=True)
        return web.Response(text='ERROR', status=500)


# ============================================================================
# СТРИМИНГ
# Фото скачиваем целиком (Telethon не поддерживает Range для Photo),
# документы — стримим с Range-поддержкой.
# ============================================================================

async def stream_telegram_file(request: web.Request):
    file_id    = request.match_info['file_id']
    media_info = db.get_media(file_id)
    if not media_info:
        return web.Response(text='Файл не найден', status=404)

    try:
        message = await bot.get_messages(media_info['chat_id'], ids=media_info['message_id'])
        if not message or not message.media:
            return web.Response(text='Медиа не найдено', status=404)

        mime_type  = media_info['mime_type'] or 'application/octet-stream'
        file_size  = media_info['size'] or 0
        is_photo   = bool(media_info.get('is_photo', 0))
        range_hdr  = request.headers.get('Range')

        base_headers = {
            'Accept-Ranges':     'bytes',
            'Cache-Control':     'public, max-age=86400',
            'Content-Type':      mime_type,
            'Content-Disposition': f'inline; filename="{media_info["filename"]}"',
        }

        # ── Фото: скачиваем через message (не message.media!)
        # Telethon для Photo требует именно объект Message, иначе вернёт None
        if is_photo:
            data = await bot.download_media(message, bytes)
            if not data:
                logger.error(f"download_media вернул None для фото {file_id}")
                return web.Response(text='Не удалось скачать фото', status=500)
            return web.Response(
                body=data,
                headers={
                    **base_headers,
                    'Content-Length': str(len(data)),
                    'Content-Type':   'image/jpeg',
                    'Cache-Control':  'public, max-age=86400',
                }
            )

        # ── Документы: стриминг с Range ───────────────────────────────────
        if range_hdr and file_size:
            m     = re.match(r'bytes=(\d*)-(\d*)', range_hdr)
            start = int(m.group(1)) if m.group(1) else 0
            end   = int(m.group(2)) if m.group(2) else file_size - 1
            end   = min(end, file_size - 1)

            # Выравниваем offset до кратного 4096 (требование Telethon/MTProto)
            aligned = (start // 4096) * 4096
            skip    = start - aligned
            length  = end - start + 1

            response = web.StreamResponse(
                status=206,
                headers={
                    **base_headers,
                    'Content-Range':  f'bytes {start}-{end}/{file_size}',
                    'Content-Length': str(length),
                }
            )
            await response.prepare(request)

            downloaded = 0
            async for chunk in bot.iter_download(
                message.media,
                offset=aligned,
                chunk_size=STREAM_CHUNK,
                request_size=STREAM_CHUNK,
            ):
                if downloaded == 0 and skip:
                    chunk = chunk[skip:]
                remaining = length - downloaded
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]
                await response.write(chunk)
                downloaded += len(chunk)
                if downloaded >= length:
                    break

        else:
            response = web.StreamResponse(
                status=200,
                headers={
                    **base_headers,
                    'Content-Length': str(file_size) if file_size else '',
                }
            )
            await response.prepare(request)
            async for chunk in bot.iter_download(
                message.media,
                chunk_size=STREAM_CHUNK,
                request_size=STREAM_CHUNK,
            ):
                await response.write(chunk)

        await response.write_eof()
        return response

    except Exception as e:
        logger.error(f"Стриминг {file_id}: {e}", exc_info=True)
        return web.Response(text='Ошибка загрузки', status=500)


# ============================================================================
# ОБЩИЙ HTML-ШЕЛЛ
# ============================================================================

def _page(title: str, body: str) -> str:
    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape_html(title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;500;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg:      #0d0d0d;
      --surface: #161616;
      --border:  #2a2a2a;
      --accent:  #e8ff47;
      --muted:   #555;
      --text:    #e8e8e8;
      --radius:  6px;
      --mono:    'IBM Plex Mono', monospace;
      --sans:    'IBM Plex Sans', sans-serif;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: var(--sans); min-height: 100vh; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>{body}</body>
</html>'''


# ============================================================================
# УМНЫЙ ПЛЕЕР
# ============================================================================

async def handle_player(request: web.Request):
    file_id    = request.match_info['file_id']
    media_info = db.get_media(file_id)
    if not media_info:
        return web.Response(text='Файл не найден', status=404)

    filename   = media_info['filename']
    file_url   = f'{DOMAIN}/file/{file_id}'
    media_type = media_info['media_type'] or get_media_type(filename, media_info.get('mime_type',''))
    safe_name  = escape_html(filename)

    # ── ВИДЕО / GIF ──────────────────────────────────────────────────────────
    if media_type in ('video', 'gif'):
        loop_attr = 'loop muted' if media_type == 'gif' else ''
        html = _page(filename, f'''
  <style>
    body {{ display:flex; justify-content:center; align-items:center; background:#000; overflow:hidden; }}
    video {{ max-width:100%; max-height:100vh; display:block; object-fit:contain; }}
    #overlay {{ position:fixed; inset:0; display:flex; justify-content:center; align-items:center; cursor:pointer; }}
    #btn {{ width:72px; height:72px; background:rgba(232,255,71,.9); border-radius:50%;
            display:flex; justify-content:center; align-items:center; transition:.2s; }}
    #btn:hover {{ transform:scale(1.1); }}
    #btn::after {{ content:''; border-left:26px solid #000; border-top:15px solid transparent;
                   border-bottom:15px solid transparent; margin-left:6px; }}
  </style>
  <video id="v" src="{file_url}" {loop_attr} playsinline preload="metadata" controls></video>
  <div id="overlay"><div id="btn"></div></div>
  <script>
    const v=document.getElementById('v'),o=document.getElementById('overlay');
    o.onclick=async()=>{{o.style.display='none';
      try{{await v.play();
        if(v.requestFullscreen)v.requestFullscreen();
        if(v.videoWidth>v.videoHeight&&screen.orientation?.lock)
          screen.orientation.lock('landscape').catch(()=>{{}});
      }}catch(e){{o.style.display='flex';}}
    }};
    v.onpause=()=>{{if(!document.fullscreenElement)o.style.display='flex';}};
  </script>''')

    # ── АУДИО ─────────────────────────────────────────────────────────────────
    elif media_type == 'audio':
        html = _page(filename, f'''
  <style>
    body {{ display:flex; justify-content:center; align-items:center; min-height:100vh;
            background:radial-gradient(ellipse at 60% 40%,#1a1a2e 0%,#0d0d0d 70%); }}
    .card {{ background:var(--surface); border:1px solid var(--border); border-radius:16px;
             padding:48px 40px; text-align:center; max-width:480px; width:90%;
             box-shadow:0 0 60px rgba(232,255,71,.05); }}
    .icon {{ font-size:56px; margin-bottom:24px; }}
    h2 {{ font-size:16px; font-weight:500; margin-bottom:32px; word-break:break-all; line-height:1.5; }}
    audio {{ width:100%; accent-color:var(--accent); }}
  </style>
  <div class="card">
    <div class="icon">🎵</div>
    <h2>{safe_name}</h2>
    <audio src="{file_url}" controls autoplay></audio>
  </div>''')

    # ── ИЗОБРАЖЕНИЕ ───────────────────────────────────────────────────────────
    elif media_type == 'image':
        html = _page(filename, f'''
  <style>
    body {{ background:#000; overflow:hidden; }}
    #wrap {{ width:100vw; height:100vh; display:flex; justify-content:center;
             align-items:center; cursor:grab; overflow:hidden; position:relative; }}
    #wrap.grabbing {{ cursor:grabbing; }}
    #img {{ max-width:100%; max-height:100vh; display:block; object-fit:contain;
            transform-origin:center; transition:transform .1s; user-select:none;
            -webkit-user-drag:none; }}
    .toolbar {{ position:fixed; bottom:20px; left:50%; transform:translateX(-50%);
                background:rgba(22,22,22,.85); border:1px solid var(--border);
                backdrop-filter:blur(8px); border-radius:40px; padding:10px 20px;
                display:flex; gap:12px; align-items:center; z-index:10; }}
    .toolbar button {{ background:transparent; border:none; color:var(--text);
                       font-size:18px; cursor:pointer; padding:4px 8px; border-radius:4px;
                       transition:.15s; font-family:var(--mono); }}
    .toolbar button:hover {{ background:rgba(255,255,255,.1); }}
    .toolbar span {{ font-family:var(--mono); font-size:12px; color:var(--muted);
                     min-width:42px; text-align:center; }}
    .loader {{ position:fixed; inset:0; background:#000; display:flex;
               justify-content:center; align-items:center; font-family:var(--mono);
               font-size:13px; color:var(--muted); letter-spacing:.05em; }}
  </style>
  <div class="loader" id="loader">загрузка…</div>
  <div id="wrap">
    <img id="img" src="{file_url}" alt="{safe_name}"
         onload="document.getElementById('loader').style.display='none'"
         onerror="document.getElementById('loader').textContent='Ошибка загрузки'">
  </div>
  <div class="toolbar">
    <button onclick="zoom(-0.25)" title="Уменьшить">−</button>
    <span id="zl">100%</span>
    <button onclick="zoom(+0.25)" title="Увеличить">+</button>
    <button onclick="resetZoom()" title="По размеру">⊡</button>
    <button onclick="rotate()" title="Повернуть">↻</button>
    <a href="{file_url}" download style="color:var(--text);font-size:16px;padding:4px 8px;border-radius:4px;transition:.15s;"
       onmouseover="this.style.background='rgba(255,255,255,.1)'"
       onmouseout="this.style.background='transparent'">↓</a>
  </div>
  <script>
    let scale=1, rot=0, dx=0, dy=0, dragging=false, ox=0, oy=0;
    const img=document.getElementById('img'), wrap=document.getElementById('wrap'), zl=document.getElementById('zl');
    function apply(){{img.style.transform=`translate(${{dx}}px,${{dy}}px) rotate(${{rot}}deg) scale(${{scale}})`;zl.textContent=Math.round(scale*100)+'%';}}
    function zoom(d){{scale=Math.max(.1,Math.min(10,scale+d));apply();}}
    function resetZoom(){{scale=1;dx=0;dy=0;rot=0;apply();}}
    function rotate(){{rot=(rot+90)%360;apply();}}

    // Колёсико
    wrap.addEventListener('wheel',e=>{{e.preventDefault();zoom(e.deltaY<0?.15:-.15);}},{{passive:false}});

    // Перетаскивание
    wrap.addEventListener('mousedown',e=>{{dragging=true;ox=e.clientX-dx;oy=e.clientY-dy;wrap.classList.add('grabbing');}});
    window.addEventListener('mousemove',e=>{{if(!dragging)return;dx=e.clientX-ox;dy=e.clientY-oy;apply();}});
    window.addEventListener('mouseup',()=>{{dragging=false;wrap.classList.remove('grabbing');}});

    // Двойной клик — сброс
    wrap.addEventListener('dblclick',()=>{{scale===1?zoom(1):resetZoom();}});

    // Тач-зум (pinch)
    let lastDist=0;
    wrap.addEventListener('touchstart',e=>{{if(e.touches.length===2)lastDist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);}});
    wrap.addEventListener('touchmove',e=>{{if(e.touches.length===2){{e.preventDefault();const d=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);scale=Math.max(.1,Math.min(10,scale*(d/lastDist)));lastDist=d;apply();}}}},{{passive:false}});
  </script>''')

    # ── PDF ───────────────────────────────────────────────────────────────────
    elif media_type == 'pdf':
        # PDF.js — локальный рендер прямо в браузере
        html = _page(filename, f'''
  <style>
    body {{ overflow:hidden; display:flex; flex-direction:column; height:100vh; background:#525659; }}
    .toolbar {{ background:#3d3d3d; border-bottom:1px solid #222; padding:10px 16px;
                display:flex; align-items:center; gap:12px; flex-shrink:0; }}
    .toolbar span {{ font-family:var(--mono); font-size:13px; color:#ccc; margin-right:auto; }}
    a.dl {{ background:var(--accent); color:#000; padding:6px 16px; border-radius:4px;
            font-size:13px; font-weight:600; }}
    #pdf-container {{ flex:1; overflow:auto; display:flex; flex-direction:column; align-items:center; padding:16px; gap:8px; }}
    canvas {{ box-shadow:0 4px 16px rgba(0,0,0,.4); display:block; max-width:100%; }}
    .page-num {{ font-family:var(--mono); font-size:11px; color:#888; }}
    #loading {{ color:#ccc; font-family:var(--mono); font-size:13px; padding:60px; }}
  </style>
  <div class="toolbar">
    <span>📄 {safe_name}</span>
    <a class="dl" href="{file_url}" download>↓ скачать</a>
  </div>
  <div id="pdf-container"><div id="loading">загрузка PDF…</div></div>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs" type="module"></script>
  <script type="module">
    import * as pdfjsLib from 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs';
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs';

    const container = document.getElementById('pdf-container');
    document.getElementById('loading').textContent = 'загрузка…';

    try {{
      const pdf = await pdfjsLib.getDocument('{file_url}').promise;
      container.innerHTML = '';
      const W = Math.min(container.clientWidth - 32, 900);

      for (let i = 1; i <= pdf.numPages; i++) {{
        const page = await pdf.getPage(i);
        const vp   = page.getViewport({{ scale: 1 }});
        const scale = W / vp.width;
        const viewport = page.getViewport({{ scale }});

        const canvas = document.createElement('canvas');
        canvas.width  = viewport.width;
        canvas.height = viewport.height;
        await page.render({{ canvasContext: canvas.getContext('2d'), viewport }}).promise;

        const label = document.createElement('div');
        label.className = 'page-num';
        label.textContent = `стр. ${{i}} / ${{pdf.numPages}}`;

        container.appendChild(canvas);
        container.appendChild(label);
      }}
    }} catch(e) {{
      container.innerHTML = `<div style="color:#f88;padding:40px;font-family:monospace">Ошибка: ${{e.message}}<br><br><a href="{file_url}" style="color:var(--accent)">Скачать PDF</a></div>`;
    }}
  </script>''')

    # ── КОД ───────────────────────────────────────────────────────────────────
    elif media_type == 'code':
        try:
            message    = await bot.get_messages(media_info['chat_id'], ids=media_info['message_id'])
            if not message or not message.media:
                raise ValueError("Сообщение или медиа не найдено")
            file_bytes = await bot.download_media(message.media, bytes)
            if not file_bytes:
                raise ValueError("download_media вернул None")

            # Определяем кодировку
            content = None
            for enc in ('utf-8', 'cp1251', 'latin-1'):
                try:
                    content = file_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if content is None:
                content = file_bytes.decode('utf-8', errors='replace')

            ext = get_file_extension(filename)

            # HTML — рендерим как есть
            if ext in ('.html', '.htm'):
                return web.Response(text=content, content_type='text/html', charset='utf-8')

            # Pygments подсветка
            if PYGMENTS_AVAILABLE:
                LANG_MAP = {
                    '.py':'python', '.js':'javascript', '.ts':'typescript',
                    '.jsx':'jsx',   '.tsx':'tsx',       '.css':'css',
                    '.json':'json', '.xml':'xml',       '.java':'java',
                    '.cpp':'cpp',   '.c':'c',           '.php':'php',
                    '.rb':'ruby',   '.go':'go',         '.rs':'rust',
                    '.sql':'sql',   '.sh':'bash',       '.bash':'bash',
                    '.yml':'yaml',  '.yaml':'yaml',     '.md':'markdown',
                    '.toml':'toml', '.vue':'html',      '.swift':'swift',
                    '.kt':'kotlin', '.txt':'text',      '.log':'text',
                    '.conf':'text', '.ini':'ini',       '.env':'bash',
                }
                lang = LANG_MAP.get(ext, 'text')
                try:
                    all_styles = list(HtmlFormatter.get_all_styles())
                    style = 'one-dark' if 'one-dark' in all_styles else 'monokai'
                    formatter = HtmlFormatter(
                        style=style,
                        full=True,
                        linenos=True,
                        lineanchors='L',
                        anchorlinenos=True,
                        prestyles='font-family:"IBM Plex Mono",monospace;font-size:13px;line-height:1.7;',
                    )
                    highlighted = highlight(content, get_lexer_by_name(lang), formatter)
                    # Вставляем toolbar поверх pygments-страницы
                    toolbar_css = (
                        "<style>"
                        ".media-toolbar{position:fixed;top:0;left:0;right:0;background:#1a1a1a;"
                        "border-bottom:1px solid #333;padding:10px 16px;display:flex;"
                        "align-items:center;gap:16px;z-index:9999;font-family:'IBM Plex Mono',monospace;}"
                        ".media-toolbar span{font-size:13px;color:#aaa;margin-right:auto;}"
                        ".media-toolbar a{background:#e8ff47;color:#000;padding:5px 14px;"
                        "border-radius:4px;font-size:12px;font-weight:700;text-decoration:none;}"
                        "body{padding-top:44px !important;}"
                        "</style>"
                    )
                    toolbar_html = (
                        f'<div class="media-toolbar">' +
                        f'<span>💻 {safe_name}</span>' +
                        f'<a href="{file_url}" download>↓ скачать</a>' +
                        '</div>'
                    )
                    highlighted = highlighted.replace('</head>', toolbar_css + '</head>', 1)
                    highlighted = highlighted.replace('<body>', '<body>' + toolbar_html, 1)
                    return web.Response(text=highlighted, content_type='text/html', charset='utf-8')
                except Exception as pyg_err:
                    logger.warning(f"Pygments ошибка для {filename}: {pyg_err}")

            # Fallback — монохромный, без Pygments
            html = _page(filename, (
                '<style>' +
                '.hdr{background:var(--surface);border-bottom:1px solid var(--border);' +
                'padding:14px 20px;font-family:var(--mono);font-size:13px;color:var(--muted);' +
                'position:sticky;top:0;display:flex;justify-content:space-between;align-items:center;}' +
                'pre{padding:24px;font-family:var(--mono);font-size:13px;line-height:1.7;' +
                'white-space:pre-wrap;word-break:break-all;color:#c9d1d9;}' +
                '.dl{color:var(--accent);font-size:12px;}' +
                '</style>' +
                f'<div class="hdr"><span>💻 {safe_name}</span>' +
                f'<a class="dl" href="{file_url}" download>↓ скачать</a></div>' +
                f'<pre>{escape_html(content)}</pre>'
            ))

        except Exception as e:
            logger.error(f"Ошибка чтения кода {filename}: {e}", exc_info=True)
            html = _page('Ошибка', (
                '<div style="padding:40px;font-family:var(--mono);color:var(--muted)">' +
                f'<p style="margin-bottom:16px">Ошибка загрузки файла</p>' +
                f'<p style="font-size:12px;color:#555">{escape_html(str(e))}</p>' +
                f'<br><a href="{file_url}" download style="color:var(--accent)">↓ Скачать файл</a>' +
                '</div>'
            ))

    # ── WORD (docx-preview) ───────────────────────────────────────────────────
    elif media_type == 'word':
        try:
            message    = await bot.get_messages(media_info['chat_id'], ids=media_info['message_id'])
            file_bytes = await bot.download_media(message.media, bytes)
            b64        = __import__('base64').b64encode(file_bytes).decode()

            html = _page(filename, f'''
  <style>
    body {{ background:#f0ede8; display:flex; flex-direction:column; min-height:100vh; }}
    .toolbar {{ background:#1a1a1a; padding:12px 20px; display:flex; align-items:center;
                gap:16px; position:sticky; top:0; z-index:100; flex-shrink:0; }}
    .toolbar span {{ font-family:var(--mono); font-size:13px; color:#aaa; margin-right:auto; }}
    a.dl {{ background:var(--accent); color:#000; padding:6px 16px; border-radius:4px;
            font-size:13px; font-weight:600; }}
    #docx-container {{ flex:1; padding:32px 16px; }}
    /* docx-preview переопределяет размер внутри себя */
    .docx-wrapper {{ background:#fff; max-width:860px; margin:0 auto;
                     box-shadow:0 4px 24px rgba(0,0,0,.12); border-radius:4px; }}
    #fallback {{ display:none; padding:60px; text-align:center; color:#666; }}
  </style>
  <div class="toolbar">
    <span>📝 {safe_name}</span>
    <a class="dl" href="{file_url}" download>↓ скачать</a>
  </div>
  <div id="docx-container"></div>
  <div id="fallback">
    <p style="margin-bottom:20px">Не удалось отобразить документ</p>
    <a class="dl" href="{file_url}" download>↓ Скачать файл</a>
  </div>
  <div id='docx-b64' style='display:none'>{b64}</div>
  <script>
    (async()=>{{
      try {{
        const loadScript = (url) => new Promise((resolve, reject) => {{
          const s = document.createElement('script');
          s.src = url;
          s.onload = resolve;
          s.onerror = reject;
          document.head.appendChild(s);
        }});
        if (typeof JSZip === 'undefined') {{
          await loadScript('https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js')
            .catch(() => loadScript('https://unpkg.com/jszip@3.10.1/dist/jszip.min.js'));
        }}
        if (typeof docx === 'undefined') {{
          await loadScript('https://cdn.jsdelivr.net/npm/docx-preview@0.3.3/dist/docx-preview.min.js')
            .catch(() => loadScript('https://unpkg.com/docx-preview@0.3.3/dist/docx-preview.min.js'));
        }}
        if (typeof docx === 'undefined') throw new Error('docx-preview не загрузилась');
        const b64el = document.getElementById('docx-b64');
        const b64 = b64el ? b64el.textContent.trim() : '';
        if (!b64) throw new Error('No b64 data');
        const bin = atob(b64);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        const blob = new Blob([arr], {{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}});
        await docx.renderAsync(blob, document.getElementById('docx-container'), null, {{
          className: 'docx-wrapper',
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: true,
          ignoreFonts: false,
          breakPages: true,
          useBase64URL: true,
        }});
      }} catch(e) {{
        console.error('docx-preview error:', e);
        document.getElementById('docx-container').style.display = 'none';
        document.getElementById('fallback').style.display = 'block';
      }}
    }})();
  </script>''')
        except Exception as e:
            logger.error(f"Ошибка Word: {e}")
            html = _page(filename, f'''
  <div style="padding:60px;text-align:center">
    <p style="margin-bottom:24px;color:var(--muted)">Не удалось открыть документ</p>
    <a href="{file_url}" download style="background:var(--accent);color:#000;padding:12px 28px;border-radius:6px;font-weight:600">↓ Скачать файл</a>
  </div>''')

    # ── EXCEL (SheetJS) ───────────────────────────────────────────────────────
    elif media_type == 'excel':
        try:
            message    = await bot.get_messages(media_info['chat_id'], ids=media_info['message_id'])
            file_bytes = await bot.download_media(message.media, bytes)
            b64        = __import__('base64').b64encode(file_bytes).decode()

            html = _page(filename, f'''
  <style>
    body {{ display:flex; flex-direction:column; height:100vh; overflow:hidden; }}
    .toolbar {{ background:var(--surface); border-bottom:1px solid var(--border);
                padding:10px 16px; display:flex; align-items:center; gap:10px;
                flex-shrink:0; flex-wrap:wrap; }}
    .toolbar span {{ font-family:var(--mono); font-size:13px; color:#aaa; margin-right:auto; }}
    .tab {{ background:transparent; border:1px solid var(--border); color:var(--text);
            padding:5px 14px; border-radius:4px; cursor:pointer; font-size:12px;
            font-family:var(--sans); transition:.15s; }}
    .tab.active,.tab:hover {{ background:var(--accent); color:#000; border-color:var(--accent); }}
    a.dl {{ background:var(--accent); color:#000; padding:5px 14px; border-radius:4px;
            font-size:12px; font-weight:600; }}
    #content {{ flex:1; overflow:auto; }}
    .sheet {{ display:none; }}
    .sheet.active {{ display:block; }}
    table {{ border-collapse:collapse; font-size:13px; font-family:var(--mono); }}
    th,td {{ border:1px solid var(--border); padding:7px 12px; white-space:nowrap; }}
    th {{ background:#1f1f1f; font-weight:600; position:sticky; top:0; color:var(--accent); }}
    tr:nth-child(even) td {{ background:rgba(255,255,255,.02); }}
    td {{ color:var(--text); }}
    #loading {{ padding:60px; text-align:center; font-family:var(--mono); font-size:13px; color:var(--muted); }}
  </style>
  <div class="toolbar" id="toolbar">
    <span>📊 {safe_name}</span>
    <a class="dl" href="{file_url}" download>↓ скачать</a>
  </div>
  <div id="content"><div id="loading">загрузка таблицы…</div></div>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
  <script>
    (()=>{{
      const b64="{b64}";
      try {{
        const wb=XLSX.read(b64,{{type:'base64',cellDates:true}});
        const toolbar=document.getElementById('toolbar');
        const content=document.getElementById('content');
        content.innerHTML='';

        wb.SheetNames.forEach((name,idx)=>{{
          // Таб
          const btn=document.createElement('button');
          btn.className='tab'+(idx===0?' active':'');
          btn.textContent=name;
          btn.onclick=()=>{{
            document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
            document.querySelectorAll('.sheet').forEach(s=>s.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('sheet-'+idx).classList.add('active');
          }};
          // Вставляем перед кнопкой скачать
          toolbar.insertBefore(btn, toolbar.querySelector('.dl'));

          // Лист
          const ws=wb.Sheets[name];
          const html=XLSX.utils.sheet_to_html(ws,{{id:'tbl-'+idx,editable:false}});
          const div=document.createElement('div');
          div.className='sheet'+(idx===0?' active':'');
          div.id='sheet-'+idx;
          div.innerHTML=html;
          // Стилизуем заголовок (первая строка → th)
          const firstRow=div.querySelector('tr');
          if(firstRow)firstRow.querySelectorAll('td').forEach(td=>{{
            const th=document.createElement('th');
            th.innerHTML=td.innerHTML;
            td.replaceWith(th);
          }});
          content.appendChild(div);
        }});
      }} catch(e) {{
        document.getElementById('content').innerHTML=
          `<div style="padding:60px;text-align:center;color:var(--muted)">
            Ошибка чтения файла<br><br>
            <a href="{file_url}" download style="color:var(--accent)">↓ Скачать файл</a>
           </div>`;
      }}
    }})();
  </script>''')
        except Exception as e:
            logger.error(f"Ошибка Excel: {e}")
            html = _page(filename, f'<div style="padding:60px;text-align:center"><a href="{file_url}" download style="color:var(--accent)">↓ Скачать файл</a></div>')

    # ── POWERPOINT (Microsoft Office Online) ─────────────────────────────────
    elif media_type == 'powerpoint':
        # Office Online корректно рендерит .pptx с анимациями и изображениями
        office_url = f'https://view.officeapps.live.com/op/embed.aspx?src={file_url}'
        html = _page(filename, f'''
  <style>
    body {{ overflow:hidden; display:flex; flex-direction:column; height:100vh; }}
    .toolbar {{ background:var(--surface); border-bottom:1px solid var(--border);
                padding:10px 16px; display:flex; align-items:center; gap:16px; flex-shrink:0; }}
    .toolbar span {{ font-family:var(--mono); font-size:13px; color:#aaa; margin-right:auto; }}
    a.dl {{ background:var(--accent); color:#000; padding:6px 16px; border-radius:4px;
            font-size:13px; font-weight:600; }}
    iframe {{ flex:1; border:none; width:100%; }}
    #fallback {{ display:none; flex:1; justify-content:center; align-items:center;
                 flex-direction:column; gap:20px; color:var(--muted); font-size:14px; font-family:var(--mono); }}
  </style>
  <div class="toolbar">
    <span>📑 {safe_name}</span>
    <a class="dl" href="{file_url}" download>↓ скачать</a>
  </div>
  <iframe id="viewer" src="{office_url}" allowfullscreen></iframe>
  <div id="fallback">
    <span>Microsoft Office Online недоступен</span>
    <a class="dl" href="{file_url}" download>↓ Скачать файл</a>
  </div>
  <script>
    // Если Office Online не загрузился за 12с — fallback
    setTimeout(()=>{{
      try {{
        const f=document.getElementById('viewer');
        if(!f.contentDocument?.body?.children?.length)throw 0;
      }} catch {{
        document.getElementById('viewer').style.display='none';
        document.getElementById('fallback').style.display='flex';
      }}
    }}, 12000);
  </script>''')

    # ── ZIP ───────────────────────────────────────────────────────────────────
    elif media_type == 'zip':
        try:
            message    = await bot.get_messages(media_info['chat_id'], ids=media_info['message_id'])
            file_bytes = await bot.download_media(message.media, bytes)

            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                infos = sorted(zf.infolist(), key=lambda x: x.filename)

            rows = ''
            for info in infos:
                name   = escape_html(info.filename)
                is_dir = info.filename.endswith('/')
                icon   = '📁' if is_dir else '📄'
                sz     = '' if is_dir else format_size(info.file_size)
                rows  += f'<tr><td class="ic">{icon}</td><td class="fn">{name}</td><td class="sz">{sz}</td></tr>'

            html = _page(filename, f'''
  <style>
    .toolbar {{ background:var(--surface); border-bottom:1px solid var(--border);
                padding:14px 24px; display:flex; align-items:center; gap:16px; position:sticky; top:0; }}
    .toolbar span {{ font-family:var(--mono); font-size:13px; color:#aaa; margin-right:auto; }}
    a.dl {{ background:var(--accent); color:#000; padding:7px 18px; border-radius:4px;
            font-size:13px; font-weight:600; }}
    .content {{ max-width:900px; margin:32px auto; padding:0 20px; }}
    .meta {{ font-size:12px; color:var(--muted); margin-bottom:16px; font-family:var(--mono); }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    td {{ padding:9px 12px; border-bottom:1px solid #1a1a1a; color:var(--text); }}
    td.ic {{ width:32px; font-size:16px; }}
    td.fn {{ font-family:var(--mono); font-size:13px; word-break:break-all; }}
    td.sz {{ text-align:right; color:var(--muted); font-family:var(--mono); font-size:12px; width:100px; }}
    tr:hover td {{ background:rgba(255,255,255,.03); }}
  </style>
  <div class="toolbar">
    <span>📦 {safe_name}</span>
    <a class="dl" href="{file_url}" download>↓ скачать архив</a>
  </div>
  <div class="content">
    <p class="meta">{len(infos)} объектов · {format_size(media_info["size"])}</p>
    <table><tbody>{rows}</tbody></table>
  </div>''')
        except Exception as e:
            logger.error(f"Ошибка ZIP: {e}")
            html = _page(filename, f'<div style="padding:60px;text-align:center"><a href="{file_url}" download style="color:var(--accent)">↓ Скачать архив</a></div>')

    # ── ВСЁ ОСТАЛЬНОЕ ─────────────────────────────────────────────────────────
    else:
        html = _page(filename, f'''
  <style>
    body {{ display:flex; justify-content:center; align-items:center; min-height:100vh; }}
    .card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
             padding:56px 48px; text-align:center; max-width:440px; width:90%; }}
    .icon {{ font-size:52px; margin-bottom:20px; }}
    h2 {{ font-size:16px; font-weight:500; word-break:break-all; margin-bottom:10px; line-height:1.5; }}
    .meta {{ font-size:12px; color:var(--muted); margin-bottom:36px; font-family:var(--mono); }}
    a {{ display:inline-block; background:var(--accent); color:#000; padding:12px 36px;
         border-radius:6px; font-weight:700; font-size:15px; }}
  </style>
  <div class="card">
    <div class="icon">📦</div>
    <h2>{safe_name}</h2>
    <p class="meta">{format_size(media_info["size"])}</p>
    <a href="{file_url}" download>↓ Скачать файл</a>
  </div>''')

    return web.Response(text=html, content_type='text/html', charset='utf-8')


# ============================================================================
# СПИСОК ФАЙЛОВ + ПОИСК
# ============================================================================

async def handle_list(request: web.Request):
    query  = request.rel_url.query.get('q', '').strip()
    page   = max(1, int(request.rel_url.query.get('p', 1)))
    limit  = 40
    offset = (page - 1) * limit

    if query:
        items = db.search(query, limit=limit)
        total = len(items)
    else:
        items = db.get_recent(limit=limit, offset=offset)
        total = db.total_count()

    total_pages = max(1, (total + limit - 1) // limit)

    ICONS = {'video':'🎬','gif':'🎬','audio':'🎵','image':'🖼','pdf':'📄',
             'word':'📝','excel':'📊','powerpoint':'📑','code':'💻',
             'zip':'📦','archive':'📦','file':'📁'}

    rows = ''
    for item in items:
        fid  = item['file_id']
        fname = escape_html(item['filename'])
        mt   = item['media_type'] or 'file'
        icon = ICONS.get(mt, '📁')
        sz   = format_size(item['size'])
        date = (item['created_at'] or '')[:10]
        rows += f'''
        <tr>
          <td class="ic">{icon}</td>
          <td class="fn"><a href="/play/{fid}">{fname}</a></td>
          <td class="sz">{sz}</td>
          <td class="dt">{date}</td>
          <td class="ac">{item["access_count"]}</td>
        </tr>'''

    stats  = db.get_stats()
    q_val  = escape_html(query)
    prev_d = 'disabled' if page <= 1 else ''
    next_d = 'disabled' if page >= total_pages else ''
    prev_p = page - 1
    next_p = page + 1
    q_param = f'&q={q_val}' if query else ''

    html = _page('Файлы', f'''
  <style>
    .header {{ background:var(--surface); border-bottom:1px solid var(--border);
               padding:16px 24px; display:flex; align-items:center; gap:20px; flex-wrap:wrap; }}
    .logo {{ font-family:var(--mono); font-weight:600; font-size:17px;
             color:var(--accent); letter-spacing:.05em; }}
    .stats {{ font-size:12px; color:var(--muted); font-family:var(--mono); margin-right:auto; }}
    .search {{ display:flex; gap:8px; }}
    .search input {{ background:#1a1a1a; border:1px solid var(--border); color:var(--text);
                     padding:7px 12px; border-radius:4px; font-size:13px; width:240px;
                     font-family:var(--sans); outline:none; }}
    .search input:focus {{ border-color:var(--accent); }}
    .search button {{ background:var(--accent); color:#000; border:none; padding:7px 16px;
                      border-radius:4px; font-weight:700; cursor:pointer; font-size:13px; }}
    .content {{ max-width:1100px; margin:24px auto; padding:0 20px; }}
    .info {{ font-size:12px; color:var(--muted); margin-bottom:12px; font-family:var(--mono); }}
    table {{ width:100%; border-collapse:collapse; }}
    th {{ text-align:left; padding:10px 12px; font-size:11px; text-transform:uppercase;
          letter-spacing:.08em; color:var(--muted); border-bottom:1px solid var(--border);
          font-family:var(--mono); }}
    td {{ padding:10px 12px; border-bottom:1px solid #1a1a1a; vertical-align:middle; }}
    td.ic {{ width:32px; font-size:16px; }}
    td.fn a {{ color:var(--text); font-size:14px; }}
    td.fn a:hover {{ color:var(--accent); }}
    td.sz,td.dt,td.ac {{ font-family:var(--mono); font-size:12px; color:var(--muted); white-space:nowrap; }}
    td.sz {{ width:90px; }} td.dt {{ width:100px; }} td.ac {{ width:60px; text-align:right; }}
    tr:hover td {{ background:rgba(255,255,255,.02); }}
    .pager {{ display:flex; align-items:center; gap:12px; margin-top:24px;
              font-family:var(--mono); font-size:12px; color:var(--muted); }}
    .pager button {{ background:var(--surface); border:1px solid var(--border);
                     color:var(--text); padding:6px 16px; border-radius:4px; cursor:pointer;
                     font-family:var(--mono); font-size:12px; }}
    .pager button:hover:not(:disabled) {{ border-color:var(--accent); color:var(--accent); }}
    .pager button:disabled {{ opacity:.3; cursor:default; }}
    .empty {{ padding:60px; text-align:center; color:var(--muted); font-size:14px; font-family:var(--mono); }}
  </style>
  <div class="header">
    <span class="logo">MEDIA BOT</span>
    <span class="stats">{stats["total_files"]} файлов · {stats["total_size_gb"]} ГБ · {stats["total_access"]} просмотров</span>
    <form class="search" method="get" action="/list">
      <input name="q" value="{q_val}" placeholder="Поиск по имени…" autocomplete="off">
      <button type="submit">Найти</button>
    </form>
  </div>
  <div class="content">
    <p class="info">{"Найдено: " + str(total) if query else f"Стр. {page} из {total_pages} · всего {total}"}</p>
    {"<table><thead><tr><th></th><th>Файл</th><th>Размер</th><th>Дата</th><th>👁</th></tr></thead><tbody>" + rows + "</tbody></table>" if items else '<p class="empty">Файлов не найдено</p>'}
    <div class="pager">
      <button onclick="location.href='/list?p={prev_p}{q_param}'" {prev_d}>← Пред.</button>
      <span>стр. {page} / {total_pages}</span>
      <button onclick="location.href='/list?p={next_p}{q_param}'" {next_d}>След. →</button>
    </div>
  </div>''')

    return web.Response(text=html, content_type='text/html', charset='utf-8')


# ============================================================================
# API
# ============================================================================

async def stats_handler(request: web.Request):
    return web.json_response(db.get_stats())

async def health_handler(request: web.Request):
    return web.json_response({'status': 'ok', 'bot_connected': bot.is_connected()})


# ============================================================================
# WEBHOOK SETUP
# ============================================================================

async def set_webhook():
    webhook_url = f'{DOMAIN}{WEBHOOK_PATH}'
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as session:
        payload = {'url': webhook_url, 'allowed_updates': ['message']}
        if WEBHOOK_SECRET:
            payload['secret_token'] = WEBHOOK_SECRET
        try:
            async with session.post(
                f'https://api.telegram.org/bot{BOT_TOKEN}/setWebhook', json=payload
            ) as resp:
                result = await resp.json()
                if result.get('ok'):
                    logger.info(f"Webhook установлен: {webhook_url}")
                else:
                    logger.error(f"Ошибка webhook: {result}")
        except Exception as e:
            logger.error(f"Webhook недоступен: {e}")


# ============================================================================
# ВЕБ-СЕРВЕР
# ============================================================================

async def start_web_server():
    app = web.Application(client_max_size=10 * 1024 ** 3)
    app.router.add_post(WEBHOOK_PATH,     webhook_handler)
    app.router.add_get('/file/{file_id}', stream_telegram_file)
    app.router.add_get('/play/{file_id}', handle_player)
    app.router.add_get('/list',           handle_list)
    app.router.add_get('/stats',          stats_handler)
    app.router.add_get('/health',         health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logger.info(f"Сервер запущен на порту {PORT}")


# ============================================================================
# MAIN
# ============================================================================

async def main():
    global _message_queue
    logger.info('Telegram Media Bot v3 — запуск')

    _message_queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    await bot.start(bot_token=BOT_TOKEN)
    logger.info('Telegram подключён')

    await start_web_server()
    await set_webhook()
    asyncio.create_task(queue_worker())

    logger.info(f'Готово. Домен: {DOMAIN}  Порт: {PORT}')
    await asyncio.Event().wait()


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    globals()['bot'] = TelegramClient('media_bot', API_ID, API_HASH)

    def _shutdown(sig, frame):
        if bot:
            loop.create_task(bot.disconnect())

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info('Остановлено')