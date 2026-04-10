"""Модели базы данных — User, Channel, Download (кэш file_id)"""
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс для всех моделей"""
    pass


class User(Base):
    """Пользователь бота"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    # когда первый раз зашел в бота
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # сколько раз скачивал
    download_count: Mapped[int] = mapped_column(default=0)
    # язык интерфейса: ru / uz / en
    language: Mapped[str] = mapped_column(String(5), default="ru")

    def __repr__(self) -> str:
        return f"<User {self.telegram_id} ({self.username})>"


class Channel(Base):
    """Канал/группа для обязательной подписки"""
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # id канала в Telegram (например -1001234567890)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    # название для отображения юзеру
    title: Mapped[str] = mapped_column(String(255))
    # ссылка на канал (для кнопки "Подписаться")
    invite_link: Mapped[str] = mapped_column(String(255))
    # когда добавлен
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Channel {self.channel_id} ({self.title})>"


class Download(Base):
    """Кэш скачанных TikTok-видео — хранит file_id для быстрой повторной отправки.

    TTL кэша = 1 день (TikTok-ссылки и контент часто меняются/удаляются).
    """
    __tablename__ = "downloads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # ссылка на TikTok-видео (нормализованная)
    tiktok_url: Mapped[str] = mapped_column(String(500), index=True)
    # формат скачивания: video, audio (у TikTok обычно только video)
    format_key: Mapped[str] = mapped_column(String(50))
    # file_id от Telegram (для повторной отправки без скачивания)
    file_id: Mapped[str] = mapped_column(String(255))
    # тип медиа: video, audio
    media_type: Mapped[str] = mapped_column(String(50))
    # заголовок видео (для восстановления caption из кэша)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # когда скачано
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # сколько раз запрашивали
    download_count: Mapped[int] = mapped_column(default=1)
    # когда кэш протухнет (по умолчанию +1 день)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now() + timedelta(days=1),
    )

    @property
    def is_expired(self) -> bool:
        """Проверяет, протух ли кэш"""
        return datetime.now(self.expires_at.tzinfo) > self.expires_at

    def __repr__(self) -> str:
        return f"<Download {self.tiktok_url[:30]}... ({self.media_type})>"
