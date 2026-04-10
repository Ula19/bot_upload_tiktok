"""Конфигурация бота — все настройки из .env"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # токен бота
    bot_token: str

    # PostgreSQL
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "bot_4_tiktok"
    db_user: str = "postgres"
    db_password: str = ""

    # юзернейм бота (для рекламной подписи)
    bot_username: str = ""

    # админы бота (через запятую в .env)
    admin_ids: str = ""
    admin_username: str = "admin"

    # URL Bot API (Local Bot API на VPS = файлы до 2 ГБ)
    bot_api_url: str = "https://api.telegram.org"

    # Cobalt API — основной движок скачивания (без watermark, быстрый)
    cobalt_api_url: str = "http://localhost:9000"
    cobalt_api_key: str = ""

    # SOCKS5 прокси для обхода блокировки TikTok по IP
    # формат: socks5://user:pass@host:port
    proxy_url: str = ""

    # кэш скачиваний (дни) — 1 день достаточно для TikTok
    cache_ttl_days: int = 1

    # лимит файла (Local Bot API — 2 ГБ, обычный — 50 МБ)
    max_file_size: int = 2 * 1024 * 1024 * 1024  # 2 ГБ

    @property
    def admin_id_list(self) -> list[int]:
        """Парсит admin_ids из строки в список int"""
        if not self.admin_ids:
            return []
        return [int(x.strip()) for x in self.admin_ids.split(",") if x.strip()]

    @property
    def db_url(self) -> str:
        """URL для подключения к PostgreSQL через asyncpg"""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # игнорируем лишние переменные в .env
    }


# глобальный экземпляр настроек
settings = Settings()
