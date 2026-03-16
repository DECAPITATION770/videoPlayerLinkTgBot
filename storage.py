import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session_maker
from models import Media

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


def _mime_to_ext(mime: str) -> str:
    return MIME_TO_EXT.get(mime.split(";")[0].strip().lower(), ".bin")


def _ensure_media_dir() -> Path:
    p = Path(settings.media_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def register_media_from_file(
    file_bytes: bytes, mime_type: str, media_type: str
) -> str:
    long_id = secrets.token_urlsafe(48)
    ext = _mime_to_ext(mime_type)
    media_dir = _ensure_media_dir()
    local_path = media_dir / f"{long_id}{ext}"
    local_path.write_bytes(file_bytes)

    async with async_session_maker() as session:
        media = Media(
            long_id=long_id,
            local_path=str(local_path),
            telegram_file_id="",
            mime_type=mime_type,
            media_type=media_type,
            created_at=datetime.now(timezone.utc),
        )
        session.add(media)
        await session.commit()

    return long_id


async def get_media(long_id: str) -> tuple[str | None, str | None, str, str] | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(
                Media.local_path,
                Media.telegram_file_id,
                Media.mime_type,
                Media.media_type,
            ).where(Media.long_id == long_id)
        )
        row = result.one_or_none()
    if row is None:
        return None
    return (row.local_path, row.telegram_file_id, row.mime_type, row.media_type)
