from pathlib import Path

from contextlib import asynccontextmanager
import json

import httpx
from httpx import HTTPStatusError
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from telegram import Update
from telegram.ext import Application

from config import settings
from database import init_db
from storage import get_media, register_media_from_file, MIME_TO_EXT
from bot import build_application

# long_id: token_urlsafe(48) → буквы, цифры, -, _; длина ~64
def _is_valid_long_id(long_id: str) -> bool:
    if not long_id or len(long_id) < 40 or len(long_id) > 80:
        return False
    return all(c.isalnum() or c in "-_" for c in long_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    bot_app = build_application()
    await bot_app.initialize()
    await bot_app.start()
    webhook_url = f"{settings.public_url_rstrip}/telegram-webhook"
    await bot_app.bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
    )
    app.state.bot_app = bot_app
    try:
        yield
    finally:
        await bot_app.bot.delete_webhook(drop_pending_updates=True)
        await bot_app.stop()
        await bot_app.shutdown()


app = FastAPI(title="TgPlayer", lifespan=lifespan)


@app.get("/")
async def root():
    """Проверка доступности сервиса."""
    return {"status": "ok", "message": "TgPlayer работает"}


@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    bot_app: Application = request.app.state.bot_app
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}


@app.post("/api/register")
async def api_register(
    file: UploadFile = File(...),
    mime_type: str = Form(...),
    media_type: str = Form(...),
):
    if not mime_type or not media_type:
        raise HTTPException(400, "mime_type, media_type required")
    mime_base = mime_type.split(";")[0].strip().lower()
    if mime_base not in MIME_TO_EXT:
        raise HTTPException(400, "Unsupported media type")
    chunks = []
    total = 0
    limit = settings.max_upload_bytes
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "File too large")
        chunks.append(chunk)
    file_bytes = b"".join(chunks)
    if not file_bytes:
        raise HTTPException(400, "Empty file")
    long_id = await register_media_from_file(file_bytes, mime_type, media_type)
    return {"url": f"{settings.public_url_rstrip}/p/{long_id}", "success": True}


@app.get("/p/{long_id}")
async def player_page(long_id: str):
    if not _is_valid_long_id(long_id):
        raise HTTPException(404, "Not found")
    row = await get_media(long_id)
    if not row:
        raise HTTPException(404, "Not found")
    _, _, mime_type, media_type = row
    media_url = f"/p/{long_id}/media"

    is_video = media_type == "video" or mime_type.startswith("video/")
    is_image = media_type in {"photo", "gif"} or mime_type.startswith("image/")

    # Для картинок — сразу отдаём чистый файл/картинку
    if is_image and not is_video:
        # frontend указывает напрямую на сырой файл
        from fastapi.responses import RedirectResponse as _Redirect

        return _Redirect(url=media_url, status_code=302)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TgPlayer</title>
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      background: black;
      overflow: hidden;
    }}
    body {{
      margin: 0;
      padding: 0;
    }}
    .root {{
      position: relative;
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      background: black;
      cursor: pointer;
    }}
    .preview-media {{
      max-width: 100%;
      max-height: 100%;
      object-fit: cover;
      background: black;
      display: block;
    }}
    .play-overlay {{
      position: absolute;
      width: 96px;
      height: 96px;
      border-radius: 50%;
      background: rgba(0, 0, 0, 0.6);
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .play-overlay::before {{
      content: "";
      display: block;
      width: 0;
      height: 0;
      border-top: 18px solid transparent;
      border-bottom: 18px solid transparent;
      border-left: 28px solid white;
      margin-left: 6px;
    }}
  </style>
</head>
<body>
  <div class="root" id="root">
    <video
      id="preview"
      class="preview-media"
      src="{media_url}"
      playsinline
      preload="metadata"
    ></video>
    <div class="play-overlay" id="play"></div>
  </div>
  <script>
    (function() {{
      const root = document.getElementById('root');
      const play = document.getElementById('play');
      const src = {json.dumps(media_url)};

      if (!root || !play) return;

      function createPlayer() {{
        const el = document.createElement("video");
        el.src = src;
        el.controls = true;
        el.autoplay = true;
        el.playsInline = true;
        el.id = "player";
        el.className = "preview-media";
        return el;
      }}

      function goFullscreen(target) {{
        const docEl = document.documentElement;
        if (docEl.requestFullscreen) {{
          docEl.requestFullscreen().catch(() => {{}});
        }}
        if (screen.orientation && screen.orientation.lock) {{
          // Пытаемся заблокировать альбомную ориентацию там, где это разрешено
          screen.orientation.lock('landscape').catch(() => {{}});
        }}
        if (target && target.play) {{
          target.play().catch(() => {{}});
        }}
      }}

      function startPlayback() {{
        const current = document.getElementById("player");
        if (!current) {{
          const player = createPlayer();
          root.innerHTML = "";
          root.appendChild(player);
          goFullscreen(player);
        }} else {{
          goFullscreen(current);
        }}
      }}

      root.addEventListener("click", startPlayback, {{ once: true }});
      play.addEventListener("click", function (ev) {{
        ev.stopPropagation();
        startPlayback();
      }}, {{ once: true }});

      // На всякий случай попытаться сразу — могут заблокировать
      window.addEventListener("load", function () {{
        setTimeout(() => {{
          // только визуальный превью, без звука
        }}, 0);
      }});
    }})();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.get("/p/{long_id}/raw")
async def player_raw(long_id: str):
    """Чистый доступ к файлу (без обёртки). Удобно для «обычного» плеера."""
    if not _is_valid_long_id(long_id):
        raise HTTPException(404, "Not found")
    # просто переиспользуем существующий эндпоинт, который отдаёт файл/стрим
    return RedirectResponse(url=f"/p/{long_id}/media", status_code=302)


@app.get("/p/{long_id}/media")
async def serve_media(long_id: str, request: Request):
    if not _is_valid_long_id(long_id):
        raise HTTPException(404, "Not found")
    row = await get_media(long_id)
    if not row:
        raise HTTPException(404, "Not found")
    local_path, telegram_file_id, mime_type, _ = row

    if local_path and Path(local_path).exists():
        return FileResponse(
            local_path,
            media_type=mime_type,
            headers={"Accept-Ranges": "bytes"},
        )

    if not telegram_file_id or not settings.telegram_bot_token:
        raise HTTPException(404, "Файл недоступен")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getFile",
                params={"file_id": telegram_file_id},
            )
            r.raise_for_status()
        except HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(404, "Файл не найден в Telegram")
            raise HTTPException(502, f"Ошибка Telegram API: {e.response.status_code}")
        data = r.json()
        if not data.get("ok"):
            raise HTTPException(502, "Telegram getFile failed")
        file_path = data["result"]["file_path"]
        file_size = data["result"].get("file_size")
        tg_url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
        range_header = request.headers.get("range")
        start, end = 0, file_size - 1 if file_size else None
        if range_header and file_size is not None:
            try:
                unit, spec = range_header.strip().split(" ", 1)
                if unit.lower() != "bytes":
                    raise ValueError()
                start_s, _, end_s = spec.partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else file_size - 1
                end = min(end, file_size - 1)
            except (ValueError, TypeError):
                range_header = None
        if range_header and file_size is not None:
            headers = {"Range": f"bytes={start}-{end}"}
            async with client.stream("GET", tg_url, headers=headers) as resp:
                resp.raise_for_status()
                content_length = end - start + 1

                async def stream():
                    async for chunk in resp.aiter_bytes():
                        yield chunk

                return StreamingResponse(
                    stream(),
                    status_code=206,
                    media_type=mime_type,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(content_length),
                    },
                )
        else:
            async with client.stream("GET", tg_url) as resp:
                resp.raise_for_status()
                headers = {"Accept-Ranges": "bytes"}
                if file_size is not None:
                    headers["Content-Length"] = str(file_size)

                async def stream():
                    async for chunk in resp.aiter_bytes():
                        yield chunk

                return StreamingResponse(
                    stream(),
                    media_type=mime_type,
                    headers=headers,
                )
