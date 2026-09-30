# limits-bot

Telegram-бот, который следит за лимитами **Claude** (Max plan) и **Codex**
(ChatGPT-подписка через CLIProxyAPI) и шлёт push-уведомления:

- 🔄 **недельный лимит обнулился** — ловится по факту (7d% резко упал или сменилось окно), не по расписанию;
- ⏳ **скоро обнулится** — предупреждение за `WEEKLY_WARN_HOURS` до сброса недельного окна;
- ⚠️ **пороги утилизации** — при пересечении 80% / 95% по 5h и 7d окнам (настраивается);
- 💸 **платный overflow** (`extra_usage`) — при достижении порога месячного лимита
  (значения приходят в **центах**: `monthly_limit=16000` = $160.00, бот делит на 100);
- ⛔ **полная блокировка** (`quota.exceeded`) и восстановление.

Мониторит Claude и Codex одновременно (🟣 Claude / 🟠 Codex), каждый со своим
источником. Постоянная клавиатура внизу + inline-меню `/menu`. Команды: `/status`
(лимиты по всем аккаунтам), `/next` (все окна обнуления), `/models` (модели прокси).

Команда `/check` проверяет настоящий короткий ответ модели Claude через AI Proxy,
официальный [Claude Status](https://status.claude.com/) и, если настроен Codex,
короткий ответ его модели. Она показывает время ответа и число успешных
автоматических проверок из последних шести. Список `/models` не считается
проверкой работоспособности авторизации.

Фоновая проверка идёт раз в `HEALTH_POLL_INTERVAL_MIN` минут: при проблеме и
после восстановления бот отправляет уведомление. Проблемы авторизации и открытые
инциденты Claude Status сообщаются сразу, прочие сбои — после двух опросов,
восстановление — после двух удачных опросов. История и флаги уведомлений лежат
в том же `state.json` и переживают перезапуск.

Для Codex контейнеру нужен доступ только на чтение к каталогу auth-файлов
CLIProxyAPI. Укажите `CODEX_AUTH_HOST_DIR=/opt/cliproxyapi/auths` в `.env` на ns1.
Бот предупреждает за `CODEX_REAUTH_WARN_HOURS` часов до поля `expired` в auth-файле
и проверяет работу Codex реальным запросом. `expired` — срок **текущего OAuth
access token**, который может автоматически обновиться; эта дата сама по себе
не доказывает, что OpenAI требует повторного входа. Ошибку авторизации в живом
запросе бот сообщает отдельно. Токены и содержимое auth-файлов не выводятся.

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
| `CLAUDE_PROBE_MODEL` | модель для короткой проверки Claude | claude-sonnet-5-5 |
| `CODEX_PROBE_MODEL` | модель для короткой проверки Codex | gpt-6-luna |
| `HEALTH_POLL_INTERVAL_MIN` | период проверок, мин | 10 |
| `CODEX_AUTH_HOST_DIR` | каталог auth-файлов на хосте Docker | ./codex-auth |
| `CODEX_AUTH_DIR` | каталог auth-файлов при запуске Python без Docker | пусто |
| `CODEX_REAUTH_WARN_HOURS` | предупреждение до срока access token, ч | 48 |

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
