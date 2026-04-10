"""TikTok-сервис — скачивание видео и слайдшоу.

Движки (в порядке приоритета):
    1. Cobalt API — быстрый, без watermark, не блокируется
    2. yt-dlp — fallback, если Cobalt недоступен/не сработал

Cobalt docs: https://github.com/imputnet/cobalt/blob/main/docs/api.md
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

# лимит файла (Local Bot API — 2 ГБ)
MAX_FILE_SIZE = settings.max_file_size

# расширения, которые мы считаем за фото (TikTok слайды)
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# расширения, которые мы считаем за аудио-дорожку слайдшоу
_AUDIO_EXTS = {".m4a", ".mp3", ".aac", ".opus", ".webm"}
# расширения итогового видео
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}

# максимум параллельных скачиваний
_download_semaphore = asyncio.Semaphore(40)


@dataclass
class TikTokResult:
    """Результат скачивания из TikTok."""
    kind: Literal["video", "slideshow"]
    title: str
    duration: int | None = None
    uploader: str | None = None
    width: int | None = None
    height: int | None = None
    # для kind="video"
    video_path: Path | None = None
    # для kind="slideshow"
    photo_paths: list[Path] | None = None
    audio_path: Path | None = None
    # рабочий каталог, который чистится в cleanup()
    _workdir: Path | None = None


class FileTooLargeError(Exception):
    """Файл превышает лимит Telegram (2 ГБ)."""
    pass


class TikTokUnavailableError(Exception):
    """Видео приватное / удалено / недоступно по региону."""
    pass


class TikTokDownloadError(Exception):
    """Общая ошибка скачивания (сеть, движок упал и т.п.)."""
    pass


def classify_error(error_msg: str) -> str:
    """Упрощённая классификация ошибок для TikTok."""
    msg = (error_msg or "").lower()
    if "private" in msg or "login required" in msg:
        return "private"
    if "not found" in msg or "404" in msg or "no video" in msg:
        return "not_found"
    if "unavailable" in msg or "not available" in msg or "removed" in msg:
        return "unavailable"
    if any(k in msg for k in ("timeout", "connection", "unreachable", "incompleteread", "ssl", "eof")):
        return "timeout"
    return "unknown"


class TikTokDownloader:
    """Загрузчик TikTok: Cobalt API (primary) → yt-dlp (fallback)."""

    def __init__(self, base_dir: str = "/tmp/tiktok_bot") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cobalt_url = settings.cobalt_api_url.rstrip("/")
        self.cobalt_key = settings.cobalt_api_key
        self._proxy = settings.proxy_url or None
        # Cobalt не умеет проксировать исходящие запросы к TikTok,
        # поэтому если IP заблокирован (proxy_url задан) — пропускаем Cobalt
        self._skip_cobalt = bool(self._proxy)
        logger.info(
            "TikTok downloader готов, base_dir=%s, cobalt=%s, proxy=%s, skip_cobalt=%s",
            self.base_dir, self.cobalt_url, self._proxy or "нет", self._skip_cobalt,
        )

    async def download(self, url: str) -> TikTokResult:
        """Скачивает TikTok-ссылку. Определяет сам, видео это или слайдшоу."""
        async with _download_semaphore:
            return await self._do_download(url)

    async def _do_download(self, url: str) -> TikTokResult:
        t_start = time.monotonic()
        workdir = Path(tempfile.mkdtemp(prefix="tt_", dir=self.base_dir))

        # --- 1. Cobalt API (primary) ---
        # Cobalt пропускается если задан прокси — он не умеет проксировать
        # исходящие запросы, а IP сервера заблокирован TikTok
        cobalt_error: str | None = None
        if self._skip_cobalt:
            cobalt_error = "пропущен: задан PROXY_URL, Cobalt не поддерживает прокси"
            logger.info("Cobalt пропущен — используется SOCKS5 прокси для yt-dlp")
        else:
            try:
                result = await self._cobalt_download(url, workdir)
                result._workdir = workdir
                self._check_file_size(result, workdir)
                elapsed = time.monotonic() - t_start
                logger.info(
                    "[METRIC] tiktok_download %.2fs kind=%s engine=cobalt url=%s",
                    elapsed, result.kind, url,
                )
                return result
            except FileTooLargeError:
                # размер — не проблема движка, пробрасываем сразу
                shutil.rmtree(workdir, ignore_errors=True)
                raise
            except Exception as e:
                cobalt_error = str(e)
                logger.warning("Cobalt не сработал для %s: %s", url, e)
                # чистим частичные файлы перед fallback
                for f in workdir.iterdir():
                    f.unlink(missing_ok=True)

        # --- 2. yt-dlp (fallback) ---
        try:
            result = await self._ytdlp_download(url, workdir)
            result._workdir = workdir
            self._check_file_size(result, workdir)
            elapsed = time.monotonic() - t_start
            logger.info(
                "[METRIC] tiktok_download %.2fs kind=%s engine=yt-dlp url=%s",
                elapsed, result.kind, url,
            )
            return result
        except FileTooLargeError:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        except Exception as e:
            shutil.rmtree(workdir, ignore_errors=True)
            category = classify_error(str(e))
            logger.error(
                "Оба движка упали для %s. cobalt: %s, yt-dlp [%s]: %s",
                url, cobalt_error, category, e,
            )
            if category in ("private", "not_found", "unavailable"):
                raise TikTokUnavailableError(str(e)) from e
            raise TikTokDownloadError(str(e)) from e

    # ─────────────────────────────────────────────
    #  Cobalt API
    # ─────────────────────────────────────────────

    async def _cobalt_download(self, url: str, workdir: Path) -> TikTokResult:
        """Скачивает через Cobalt API. Кидает исключение при любой проблеме."""
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.cobalt_key:
            headers["Authorization"] = f"Api-Key {self.cobalt_key}"

        body = {"url": url}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.cobalt_url,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Cobalt HTTP {resp.status}: {text}")
                data = await resp.json()

            status = data.get("status")
            logger.info("Cobalt ответ: status=%s url=%s", status, url)

            if status == "error":
                error = data.get("error", {})
                code = error.get("code", "unknown") if isinstance(error, dict) else str(error)
                raise RuntimeError(f"Cobalt ошибка: {code}")

            # redirect / tunnel — одиночное видео
            if status in ("redirect", "tunnel"):
                media_url = data["url"]
                filename = data.get("filename", "tiktok_video.mp4")
                file_path = workdir / filename
                await self._download_file(session, media_url, file_path)
                return TikTokResult(
                    kind="video",
                    title=data.get("filename", "TikTok"),
                    video_path=file_path,
                )

            # picker — слайдшоу (массив фото + опционально аудио)
            if status == "picker":
                return await self._handle_picker(session, data, workdir)

            raise RuntimeError(f"Неизвестный Cobalt status: {status}")

    async def _handle_picker(
        self,
        session: aiohttp.ClientSession,
        data: dict,
        workdir: Path,
    ) -> TikTokResult:
        """Обрабатывает Cobalt picker — слайдшоу TikTok."""
        picker = data.get("picker", [])
        if not picker:
            raise RuntimeError("Cobalt вернул пустой picker")

        # скачиваем фото (до 10 штук — лимит Telegram media group)
        photo_paths: list[Path] = []
        for idx, item in enumerate(picker[:10]):
            item_url = item.get("url", "")
            if not item_url:
                continue
            media_type = item.get("type", "photo")
            ext = ".jpg" if media_type == "photo" else ".mp4"
            file_path = workdir / f"slide_{idx:02d}{ext}"
            await self._download_file(session, item_url, file_path)
            photo_paths.append(file_path)

        # аудио-дорожка слайдшоу (Cobalt отдаёт в поле audio)
        audio_path: Path | None = None
        audio_url = data.get("audio")
        if audio_url:
            audio_path = workdir / "audio.m4a"
            await self._download_file(session, audio_url, audio_path)

        if not photo_paths:
            raise RuntimeError("Cobalt picker: не удалось скачать ни одного фото")

        return TikTokResult(
            kind="slideshow",
            title="TikTok",
            photo_paths=photo_paths,
            audio_path=audio_path,
        )

    async def _download_file(
        self,
        session: aiohttp.ClientSession,
        url: str,
        dest: Path,
    ) -> None:
        """Скачивает файл по URL в dest. Стримит чтобы не жрать память."""
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=120)
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Не удалось скачать файл: HTTP {resp.status}")
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    f.write(chunk)

    # ─────────────────────────────────────────────
    #  yt-dlp (fallback)
    # ─────────────────────────────────────────────

    async def _ytdlp_download(self, url: str, workdir: Path) -> TikTokResult:
        """Скачивает через yt-dlp с retry-обёрткой."""
        loop = asyncio.get_event_loop()
        max_attempts = 2
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                info = await loop.run_in_executor(
                    None, self._yt_dlp_extract, url, workdir
                )
                return self._build_result(info, workdir)
            except Exception as e:
                last_exc = e
                category = classify_error(str(e))
                logger.warning(
                    "yt-dlp attempt %d/%d failed [%s]: %s",
                    attempt, max_attempts, category, e,
                )
                # контент-ошибки — ретраить бессмысленно
                if category in ("private", "not_found", "unavailable"):
                    break
                if attempt < max_attempts:
                    for f in workdir.iterdir():
                        f.unlink(missing_ok=True)
                    await asyncio.sleep(2)

        raise last_exc  # type: ignore[misc]

    def _yt_dlp_extract(self, url: str, workdir: Path) -> dict:
        """Синхронный вызов yt-dlp. Возвращает info_dict."""
        import yt_dlp

        output_template = str(workdir / "%(id)s_%(autonumber)s.%(ext)s")
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "best",
            "outtmpl": output_template,
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            "extractor_args": {"tiktok": {"api_hostname": ["api22-normal-c-alisg.tiktokv.com"]}},
            # TikTok CDN иногда отдаёт битый SSL
            "nocheckcertificate": True,
            # явно IPv4
            "source_address": "0.0.0.0",
            # слайдшоу — разрешаем playlist
            "noplaylist": False,
            "ignore_no_formats_error": True,
            "writeinfojson": False,
        }

        # SOCKS5 прокси — обход блокировки TikTok по IP сервера
        if self._proxy:
            ydl_opts["proxy"] = self._proxy

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    def _build_result(self, info: dict, workdir: Path) -> TikTokResult:
        """Собирает TikTokResult из info_dict + реальных файлов на диске."""
        files = sorted(workdir.iterdir())
        photos: list[Path] = []
        videos: list[Path] = []
        audios: list[Path] = []
        for f in files:
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext in _PHOTO_EXTS:
                photos.append(f)
            elif ext in _VIDEO_EXTS:
                videos.append(f)
            elif ext in _AUDIO_EXTS:
                audios.append(f)

        title = info.get("title") or "TikTok"
        uploader = info.get("uploader") or info.get("uploader_id")
        duration = info.get("duration")

        # обычное видео
        if videos and not photos:
            video = videos[0]
            width = info.get("width")
            height = info.get("height")
            if not width and info.get("entries"):
                first = info["entries"][0] if info["entries"] else {}
                width = first.get("width")
                height = first.get("height")
            return TikTokResult(
                kind="video",
                title=title,
                duration=int(duration) if duration else None,
                uploader=uploader,
                width=width,
                height=height,
                video_path=video,
            )

        # слайдшоу — только фото (+опционально аудио)
        if photos and not videos:
            audio = audios[0] if audios else None
            return TikTokResult(
                kind="slideshow",
                title=title,
                duration=int(duration) if duration else None,
                uploader=uploader,
                photo_paths=photos[:10],
                audio_path=audio,
            )

        # смешанный — берём видео если есть
        if videos:
            return TikTokResult(
                kind="video",
                title=title,
                duration=int(duration) if duration else None,
                uploader=uploader,
                video_path=videos[0],
            )

        raise TikTokDownloadError(
            f"yt-dlp не создал ни одного медиафайла (содержимое каталога: {files})"
        )

    # ─────────────────────────────────────────────
    #  Общие утилиты
    # ─────────────────────────────────────────────

    def _check_file_size(self, result: TikTokResult, workdir: Path) -> None:
        """Проверка размера для видео. Для слайдшоу не актуально."""
        if result.kind == "video" and result.video_path:
            size = result.video_path.stat().st_size
            if size > MAX_FILE_SIZE:
                shutil.rmtree(workdir, ignore_errors=True)
                raise FileTooLargeError(
                    f"Файл слишком большой ({size / 1024 / 1024:.0f} МБ)"
                )

    def cleanup(self, result: TikTokResult) -> None:
        """Удаляет рабочий каталог результата."""
        if result._workdir and result._workdir.exists():
            try:
                shutil.rmtree(result._workdir, ignore_errors=True)
                logger.info("Удалён рабочий каталог: %s", result._workdir)
            except OSError as e:
                logger.warning("Не удалось удалить %s: %s", result._workdir, e)


# глобальный экземпляр
downloader = TikTokDownloader()
