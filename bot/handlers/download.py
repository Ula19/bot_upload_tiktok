"""Хэндлер скачивания TikTok — тонкий.

Флоу: ссылка → (опционально кэш file_id) → скачивание → отправка.
Без FSM, без выбора качества, без fallback-цепочки.
"""
import html
import logging
import os
import time

from aiogram import F, Router
from aiogram.types import (
    FSInputFile,
    InputMediaPhoto,
    Message,
)

from bot.config import settings
from bot.database import async_session
from bot.database.crud import (
    get_cached_download,
    get_or_create_user,
    get_user_language,
    increment_download_count,
    save_download,
)
from bot.emojis import E
from bot.i18n import t
from bot.services.tiktok import (
    FileTooLargeError,
    TikTokDownloadError,
    TikTokResult,
    TikTokUnavailableError,
    classify_error,
    downloader,
)
from bot.utils.helpers import clean_tiktok_url, is_tiktok_url

logger = logging.getLogger(__name__)
router = Router()

# ключ кэша для обычного видео
_VIDEO_FORMAT_KEY = "video"


@router.message(F.text)
async def handle_tiktok_url(message: Message) -> None:
    """Обработчик любых текстовых сообщений — ищем ссылку TikTok."""
    text = (message.text or "").strip()

    async with async_session() as session:
        lang = await get_user_language(session, message.from_user.id)

    if not is_tiktok_url(text):
        await message.answer(
            t("download.not_tiktok", lang),
            parse_mode="HTML",
        )
        return

    await _process_download(message, text, message.from_user, lang)


async def _process_download(
    message: Message,
    url: str,
    user,
    lang: str = "ru",
) -> None:
    """Скачивает TikTok-ссылку и отправляет юзеру.

    Вызывается:
      - напрямую из handle_tiktok_url;
      - из start.py::check_subscription когда у юзера есть pending_url.
    """
    clean_url = clean_tiktok_url(url)

    # регистрируем юзера и пробуем кэш — только для обычного видео
    async with async_session() as session:
        await get_or_create_user(
            session=session,
            telegram_id=user.id,
            username=user.username,
            full_name=user.full_name,
        )
        cached = await get_cached_download(session, clean_url, _VIDEO_FORMAT_KEY)

    if cached:
        logger.info("Кэш найден для %s", clean_url)
        try:
            promo = t("download.promo", lang, bot_username=settings.bot_username)
            title_part = f" {html.escape(cached.title)}" if cached.title else ""
            await message.answer_video(
                video=cached.file_id,
                caption=f"{E['video']}{title_part}{promo}",
                parse_mode="HTML",
            )
            async with async_session() as session:
                await increment_download_count(session, user.id)
            return
        except Exception as e:
            logger.warning("Кэш устарел / file_id невалиден: %s", e)
            # падаем дальше — скачиваем заново

    # статусное сообщение
    status_msg = await message.answer(t("download.processing", lang))

    result: TikTokResult | None = None
    try:
        result = await downloader.download(clean_url)

        # обновляем статус перед отправкой файла
        try:
            await status_msg.edit_text(t("download.uploading", lang))
        except Exception:
            pass

        t_upload = time.monotonic()
        if result.kind == "video":
            file_id = await _send_video(message, result, lang)
            # кэшируем только обычное видео (для слайдшоу отдельной схемы нет)
            if file_id:
                async with async_session() as session:
                    await save_download(
                        session=session,
                        tiktok_url=clean_url,
                        format_key=_VIDEO_FORMAT_KEY,
                        file_id=file_id,
                        media_type="video",
                        title=result.title,
                    )
        else:
            await _send_slideshow(message, result, lang)

        # метрика отправки
        _log_upload_metric(result, t_upload)

        # инкремент счётчика
        async with async_session() as session:
            await increment_download_count(session, user.id)

        # удаляем статусное сообщение
        try:
            await status_msg.delete()
        except Exception:
            pass

    except FileTooLargeError:
        logger.warning("Файл > лимита для %s", clean_url)
        try:
            await status_msg.edit_text(t("error.too_large", lang), parse_mode="HTML")
        except Exception:
            await message.answer(t("error.too_large", lang), parse_mode="HTML")

    except TikTokUnavailableError as e:
        category = classify_error(str(e))
        text = _error_text_for_category(category, lang)
        try:
            await status_msg.edit_text(text, parse_mode="HTML")
        except Exception:
            await message.answer(text, parse_mode="HTML")

    except TikTokDownloadError as e:
        category = classify_error(str(e))
        text = _error_text_for_category(category, lang)
        logger.error("Ошибка скачивания TikTok %s: %s", clean_url, e)
        try:
            await status_msg.edit_text(text, parse_mode="HTML")
        except Exception:
            await message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.exception("Неожиданная ошибка на %s: %s", clean_url, e)
        try:
            await status_msg.edit_text(t("error.generic", lang), parse_mode="HTML")
        except Exception:
            await message.answer(t("error.generic", lang), parse_mode="HTML")

    finally:
        if result is not None:
            downloader.cleanup(result)


async def _send_video(message: Message, result: TikTokResult, lang: str) -> str | None:
    """Отправляет одиночное видео. Возвращает file_id для кэша."""
    assert result.video_path is not None
    promo = t("download.promo", lang, bot_username=settings.bot_username)
    caption = f"{E['video']} {html.escape(result.title)}{promo}"

    sent = await message.answer_video(
        video=FSInputFile(str(result.video_path)),
        caption=caption,
        duration=int(result.duration) if result.duration else None,
        width=result.width,
        height=result.height,
        parse_mode="HTML",
    )
    return sent.video.file_id if sent.video else None


async def _send_slideshow(message: Message, result: TikTokResult, lang: str) -> None:
    """Отправляет слайдшоу: media group из фото + опционально аудио-трек."""
    assert result.photo_paths
    promo = t("download.promo", lang, bot_username=settings.bot_username)
    caption = f"{E['video']} {html.escape(result.title)}{promo}"

    media: list[InputMediaPhoto] = []
    for idx, photo in enumerate(result.photo_paths):
        media.append(
            InputMediaPhoto(
                media=FSInputFile(str(photo)),
                # подпись ставим только на первое фото
                caption=caption if idx == 0 else None,
                parse_mode="HTML" if idx == 0 else None,
            )
        )

    await message.answer_media_group(media=media)

    # аудио-трек слайдшоу — отдельным сообщением
    if result.audio_path and result.audio_path.exists():
        try:
            await message.answer_audio(
                audio=FSInputFile(str(result.audio_path)),
                title=result.title,
                duration=int(result.duration) if result.duration else None,
            )
        except Exception as e:
            logger.warning("Не удалось отправить аудио слайдшоу: %s", e)


def _log_upload_metric(result: TikTokResult, t_start: float) -> None:
    elapsed = time.monotonic() - t_start
    try:
        if result.kind == "video" and result.video_path:
            size_mb = os.path.getsize(result.video_path) / 1024 / 1024
        else:
            size_mb = sum(
                os.path.getsize(p) for p in (result.photo_paths or []) if p.exists()
            ) / 1024 / 1024
    except OSError:
        size_mb = 0
    speed = size_mb / elapsed if elapsed > 0 else 0
    logger.info(
        "[METRIC] tiktok_upload %.2fs kind=%s size=%.1fMB speed=%.1fMB/s",
        elapsed, result.kind, size_mb, speed,
    )


def _error_text_for_category(category: str, lang: str) -> str:
    """Локализованный текст ошибки по категории из classify_error()."""
    if category == "private":
        return t("error.private", lang)
    if category == "not_found":
        return t("error.not_found", lang)
    if category == "unavailable":
        return t("error.unavailable", lang)
    if category == "timeout":
        return t("error.timeout", lang)
    return t("error.generic", lang)
