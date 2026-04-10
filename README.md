# bot_4_tiktok

Telegram-бот для скачивания видео из TikTok без водяного знака.

## Стек

- **Python 3.12** + **aiogram 3**
- **SQLAlchemy 2.x** (async) + **PostgreSQL 16**
- **yt-dlp** — движок скачивания
- **Local Bot API** — для файлов > 50 МБ (до 2 ГБ)
- **Docker Compose** — деплой

## Возможности

- Скачивание TikTok-видео без watermark
- Поддержка коротких ссылок (`vm.tiktok.com`, `vt.tiktok.com`)
- Кэш по `file_id` (повторные запросы — мгновенно, TTL 1 день)
- Обязательная подписка на каналы (настраивается через админ-панель)
- Rate limit (5 запросов в минуту на юзера)
- Мультиязычность: русский, узбекский, английский
- Админ-панель: статистика, управление каналами, рассылка

## Структура

```
bot_4_tiktok/
├── bot/
│   ├── main.py              # точка входа
│   ├── config.py            # настройки из .env
│   ├── i18n.py              # переводы ru/uz/en
│   ├── emojis.py            # премиум-эмодзи
│   ├── database/            # SQLAlchemy модели + CRUD
│   ├── handlers/            # start, admin, download
│   ├── middlewares/         # subscription, rate_limit
│   ├── keyboards/           # inline / admin
│   ├── services/            # tiktok.py — логика yt-dlp
│   └── utils/               # commands, helpers
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── CLAUDE.md
```

## Деплой

```bash
cp .env.example .env
# Заполнить BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS, DB_PASSWORD
docker compose up -d --build
```

Проверка логов:

```bash
docker compose logs -f bot
```

## Конфигурация

См. `.env.example` — минимальный набор переменных. `BOT_API_URL` задаётся в `docker-compose.yml` напрямую.

## Разработка

См. `CLAUDE.md` — архитектурные правила, паттерны, соглашения. Общий каркас описан в `../../COMMON_SPEC.md`.
