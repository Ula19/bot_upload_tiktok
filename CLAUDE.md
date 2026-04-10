# CLAUDE.md — TikTok Telegram Bot

## ОБЯЗАТЕЛЬНО перед любой задачей
Прочитай `/Users/ulugbektoshev/telegram_bots/COMMON_SPEC.md` — там общий каркас всех ботов (UI, i18n, middleware, админка, БД, Docker). Все решения принимать согласно этим правилам. Эталонная реализация — `bot_4_youtube`.

## Суть проекта
Telegram-бот для скачивания видео из TikTok без водяного знака. Aiogram 3 + SQLAlchemy + PostgreSQL + yt-dlp. Мультиязычность (ru/uz/en).

В отличие от ютуб-бота — **никаких** proxy/WARP, cookies, FSM выбора качества: TikTok не блокирует IP, не требует авторизации, отдаёт один формат на выходе.

## Архитектура

```
handlers/ → middlewares/ → services/ → database/crud.py → database/models.py
```

- **handlers/** — `start`, `admin`, `download`
- **services/tiktok.py** — вся логика yt-dlp для TikTok (одна попытка, без fallback)
- **database/crud.py** — CRUD для User, Channel, Download (кэш file_id)
- **middlewares/** — `subscription.py` (обязательная подписка), `rate_limit.py` (5/мин)
- **keyboards/** — `inline.py` (юзерские), `admin.py` (админские)
- **i18n.py** — переводы через `t("section.key", lang, **kwargs)`

## Ключевые особенности TikTok-бота

### Скачивание
- Одна попытка через yt-dlp, без fallback-цепочек.
- У TikTok один формат — видео без watermark (`download_addr`).
- Нет FSM: юзер присылает URL → сразу идёт скачивание.
- Нет proxy/WARP: работаем напрямую с tiktok.com.
- Нет cookies: TikTok не требует авторизации для публичного контента.

### Кэш скачиваний (таблица `downloads`)
`get_cached_download(url, "video")` → если есть валидный `file_id` → отправка без повторного скачивания. TTL = 1 день.

### Поддерживаемые ссылки
- `https://www.tiktok.com/@user/video/<id>`
- `https://vm.tiktok.com/<id>` (короткие)
- `https://vt.tiktok.com/<id>`
- `https://m.tiktok.com/v/<id>`

Проверка — `bot/utils/helpers.py::is_tiktok_url()`.

## Модели БД

| Таблица     | Ключевые поля |
|-------------|--------------|
| `users`     | `telegram_id` (UNIQUE), `language`, `download_count` |
| `channels`  | `channel_id` (UNIQUE), `title`, `invite_link` |
| `downloads` | `tiktok_url`, `format_key`, `file_id`, `expires_at` |

## Конфигурация (.env)

Минимальный набор: `BOT_TOKEN`, `API_ID`/`API_HASH` (для Local Bot API), `ADMIN_IDS`, `BOT_USERNAME`, `DB_*`. Никаких `PROXY_URL` и прочей ютуб-специфики.

`BOT_API_URL` задаётся в `docker-compose.yml` (не в `.env`).

`settings.admin_id_list` → `list[int]` из `ADMIN_IDS`.

## Docker

Сервисы: `bot`, `bot-api` (Local API), `postgres`, `autoheal`. **Без WARP.** Порт Local Bot API — `8091` (чтобы не конфликтовать с ютуб-ботом на `8081`).

Volumes: `/tmp/tiktok_bot` (временные файлы), `bot-api-data` (Local Bot API).

## Что унаследовано от эталона без изменений
- `bot/emojis.py` — премиум-эмодзи
- `bot/middlewares/subscription.py` (только `is_youtube_url` → `is_tiktok_url`)
- `bot/middlewares/rate_limit.py` (аналогично)
- `bot/utils/commands.py` — меню команд Telegram
- `bot/keyboards/admin.py` — без кнопки cookies
- `bot/handlers/admin.py` — без блока cookies и импорта downloader
- `bot/handlers/start.py` — тексты адаптированы

## Что ещё не реализовано (TODO для domain-downloader)
- `bot/services/tiktok.py` — сейчас пустой файл с docstring
- `bot/handlers/download.py` — только заглушка с `router = Router()`

После реализации доменной логики:
- Удалить TODO-комментарии из docstring
- Раскомментировать `_process_download` в `handlers/start.py::check_subscription`
- Заполнить кэш через `save_download` / `get_cached_download`

## Соглашения

- Комментарии и коммиты — **на русском языке**
- Коммит = одна короткая строка от первого лица (`добавил скачивание TikTok`)
- Никаких упоминаний AI/Claude в коммитах
- `.md` файлы (кроме `README.md` и `CLAUDE.md`) — в `.gitignore`
- Все кнопки обязательно: `text` через `t()`, `style`, `icon_custom_emoji_id=E_ID[...]`
- Все сообщения — через `parse_mode="HTML"` (уже глобально в `main.py`)
