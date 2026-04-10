"""Утилиты и вспомогательные функции"""
import re


# паттерны TikTok-ссылок (полные, короткие, мобильные, vm.tiktok.com)
_TIKTOK_PATTERNS = [
    r"https?://(www\.)?tiktok\.com/@[\w\.\-]+/video/\d+",
    r"https?://(www\.)?tiktok\.com/t/[\w]+",
    r"https?://(vm|vt)\.tiktok\.com/[\w]+",
    r"https?://m\.tiktok\.com/v/\d+",
    r"https?://(www\.)?tiktok\.com/v/\d+",
]


def is_tiktok_url(text: str) -> bool:
    """Проверяет, является ли текст ссылкой на TikTok"""
    text = text.strip()
    return any(re.match(pattern, text) for pattern in _TIKTOK_PATTERNS)


def clean_tiktok_url(url: str) -> str:
    """Очищает URL — убирает query-параметры трекинга"""
    url = url.strip()
    # убираем всё после ? — трекинг и так не нужен
    return url.split("?")[0].rstrip("/")
