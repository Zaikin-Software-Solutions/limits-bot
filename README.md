# limits-bot

Telegram-бот, который следит за лимитами Claude (Max plan) и шлёт push-уведомления:

- 🔄 **недельный лимит обнулился** — ловится по факту (7d% резко упал или сменилось окно), не по расписанию;
- ⏳ **скоро обнулится** — предупреждение за `WEEKLY_WARN_HOURS` до сброса недельного окна;
- ⚠️ **пороги утилизации** — при пересечении 80% / 95% по 5h и 7d окнам (настраивается);
- 💸 **платный overflow** (`extra_usage`) — при достижении порога месячного лимита;
- ⛔ **полная блокировка** (`quota.exceeded`) и восстановление.

Плюс команды по запросу: `/status` (текущие лимиты по всем аккаунтам), `/next` (ближайшее обнуление).

## Как это работает

Раз в `POLL_INTERVAL_MIN` минут (по умолчанию 30) бот опрашивает limits API
(тот же rotate-proxy, что использует CCR), сравнивает ответ с прошлым снапшотом
на диске (`/data/state.json`) и шлёт события из разницы. Пороги и предупреждения —
edge-triggered: одно окно = одно уведомление, без спама.

На первом запуске снапшот сохраняется молча (baseline), события не шлются.

## Конфигурация

Все настройки — через окружение / `.env` (см. `.env.example`). Секреты в репозиторий
не коммитятся (`.env` в `.gitignore`).

| Переменная | Назначение | Дефолт |
|---|---|---|
| `BOT_TOKEN` | токен бота из BotFather | — |
| `CHAT_ID` | куда слать push | — |
| `ALLOWED_USER_IDS` | кому разрешены команды (опц.) | — |
| `LIMITS_API_URL` | эндпоинт лимитов | rotate-proxy |
| `LIMITS_TOKEN` | Bearer для API | — |
| `ACCOUNTS_FILTER` | мониторить только эти email (опц.) | все |
| `POLL_INTERVAL_MIN` | интервал опроса, мин | 30 |
| `WEEKLY_WARN_HOURS` | за сколько ч предупреждать | 2 |
| `THRESHOLDS` | пороги утилизации, % | 80,95 |
| `EXTRA_USAGE_WARN_PCT` | порог overflow, % | 80 |
| `STATE_PATH` | путь к снапшоту | /data/state.json |

## Запуск

### Docker (прод, образ из GHCR)

```bash
cp .env.example .env      # заполнить BOT_TOKEN, CHAT_ID, LIMITS_TOKEN
docker compose up -d
```

### Локальная сборка

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

### Без Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # заполнить
python -m app.main
```

## Стек

Python 3.12, aiogram 3, APScheduler, httpx, pydantic-settings. Образ multi-arch
(amd64/arm64) собирается в GHCR через GitHub Actions на push в `main`.
