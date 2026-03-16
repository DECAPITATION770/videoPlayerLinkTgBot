from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    public_url: str = "http://localhost:8000"  # Публичный адрес для ссылок (localhost или ngrok)
    app_url: str = ""  # URL API для бота (в Docker: http://app:8000)
    telegram_bot_token: str = ""
    telegram_bot_api_url: str = ""  # Локальный Bot API для файлов >20 МБ
    db_path: str = "sqlite+aiosqlite:///tgplayer.db"
    media_dir: str = "media"
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 МБ, задаётся через MAX_UPLOAD_BYTES

    @field_validator("db_path", mode="before")
    @classmethod
    def normalize_db_path(cls, v: str) -> str:
        if v and not v.startswith("sqlite+"):
            return f"sqlite+aiosqlite:///{v}"
        return v

    @property
    def public_url_rstrip(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def app_url_rstrip(self) -> str:
        return (self.app_url or self.public_url).rstrip("/")


settings = Settings()
