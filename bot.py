import logging
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError

from config import settings
from storage import register_media_from_file

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def _get_media_from_message(update: Update) -> tuple[str, str, str] | None:
    msg = update.message
    if not msg:
        return None
    # Photo: list of PhotoSize, take largest
    if msg.photo:
        return (msg.photo[-1].file_id, "image/jpeg", "photo")
    if msg.video:
        return (msg.video.file_id, msg.video.mime_type or "video/mp4", "video")
    if msg.animation:
        return (msg.animation.file_id, msg.animation.mime_type or "video/mp4", "gif")
    if msg.document:
        doc = msg.document
        mime = doc.mime_type or "application/octet-stream"
        name = (doc.file_name or "").lower()
        if mime == "image/gif" or name.endswith(".gif"):
            return (doc.file_id, mime, "gif")
        if mime.startswith("image/"):
            return (doc.file_id, mime, "photo")
        if mime.startswith("video/"):
            return (doc.file_id, mime, "video")
        # other document: still try to show as video if browser can
        return (doc.file_id, mime, "video")
    return None


def _chat_action_for(media_type: str):
    if media_type == "photo":
        return ChatAction.UPLOAD_PHOTO
    if media_type in ("video", "gif"):
        return ChatAction.UPLOAD_VIDEO
    return ChatAction.UPLOAD_DOCUMENT


def _success_message(media_type: str, url: str) -> str:
    titles = {"photo": "Фото готово", "video": "Видео готово", "gif": "GIF готов"}
    title = titles.get(media_type, "Готово")
    url_escaped = url.replace("&", "&amp;")
    return f"<b>{title}</b>\n\n<a href=\"{url_escaped}\">Открыть</a>"


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media = _get_media_from_message(update)
    if not media:
        await update.message.reply_text("Отправьте фото, видео или GIF.")
        return
    file_id, mime_type, media_type = media
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id:
        await context.bot.send_chat_action(chat_id=chat_id, action=_chat_action_for(media_type))
    try:
        tg_file = await context.bot.get_file(file_id)
        file_bytes = bytes(
            await tg_file.download_as_bytearray(
                read_timeout=120.0,
                write_timeout=60.0,
            )
        )
    except TelegramError as e:
        log.warning("TelegramError при скачивании: %s", e)
        msg = str(e).lower()
        if "too big" in msg or "too large" in msg:
            await update.message.reply_text(
                "Файл слишком большой. Попробуйте до 20 МБ или настройте локальный API."
            )
        elif "timed out" in msg or "timeout" in msg:
            await update.message.reply_text(
                "Не успели обработать. Попробуйте ещё раз или отправьте файл поменьше."
            )
        elif any(x in msg for x in ("not found", "404", "invalid file_id", "wrong file")):
            await update.message.reply_text(
                "Не получилось взять файл. Отправьте, пожалуйста, заново (не пересылайте)."
            )
        else:
            await update.message.reply_text("Что-то пошло не так. Попробуйте отправить ещё раз.")
        return
    except Exception as e:
        log.exception("Ошибка при скачивании файла")
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            await update.message.reply_text(
                "Не успели обработать. Попробуйте ещё раз или отправьте файл поменьше."
            )
        elif any(x in msg for x in ("not found", "404", "invalid file_id", "wrong file")):
            await update.message.reply_text(
                "Не получилось взять файл. Отправьте, пожалуйста, заново (не пересылайте)."
            )
        else:
            await update.message.reply_text("Что-то пошло не так. Попробуйте отправить ещё раз.")
        return
    try:
        long_id = await register_media_from_file(file_bytes, mime_type, media_type)
        url = f"{settings.public_url_rstrip}/p/{long_id}"
    except Exception:
        log.exception("Ошибка при регистрации")
        await update.message.reply_text("Не удалось создать ссылку. Попробуйте позже.")
        return
    if url:
        await update.message.reply_text(
            _success_message(media_type, url),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("Не удалось получить ссылку.")


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN в окружении")
    builder = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .read_timeout(120.0)
        .write_timeout(60.0)
        .connect_timeout(30.0)
    )
    if settings.telegram_bot_api_url:
        url = settings.telegram_bot_api_url.rstrip("/")
        builder = builder.base_url(f"{url}/bot").base_file_url(f"{url}/file/bot")
    app = builder.build()
    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION,
            handle_media,
        )
    )
    return app


def main() -> None:
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
