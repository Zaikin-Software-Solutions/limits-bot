"""Напоминание о ручном обновлении логина Claude в rotate-proxy.

Прокси не отдаёт срок OAuth-токена, поэтому дату последнего обновления вводит
владелец командой /token (любая дата, в том числе задним числом). Срок = дата +
CLAUDE_LOGIN_PERIOD_DAYS; напоминание приходит за CLAUDE_LOGIN_WARN_DAYS дней.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

MSK = timezone(timedelta(hours=3))
_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y")


@dataclass(frozen=True)
class LoginStatus:
    last_login: date
    due: date
    days_left: int


def today_msk(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(MSK).date()


def parse_date(text: str, today: date) -> date:
    """Дата из ввода пользователя. ValueError, если не разобрать или она в будущем."""
    raw = text.strip().lower()
    if raw in ("сегодня", "today"):
        return today
    if raw in ("вчера", "yesterday"):
        return today - timedelta(days=1)
    parsed: date | None = None
    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        try:  # ДД.ММ без года: ближайшая такая дата не позже сегодня
            day = datetime.strptime(f"{raw}.2000", "%d.%m.%Y")
            parsed = date(today.year, day.month, day.day)
            if parsed > today:
                parsed = date(today.year - 1, day.month, day.day)
        except ValueError:
            raise ValueError("не удалось разобрать дату") from None
    if parsed > today:
        raise ValueError("дата в будущем")
    return parsed


def login_status(login: dict[str, Any], today: date, period_days: int) -> LoginStatus | None:
    raw = login.get("last_login")
    if not isinstance(raw, str):
        return None
    try:
        last = date.fromisoformat(raw)
    except ValueError:
        return None
    due = last + timedelta(days=period_days)
    return LoginStatus(last, due, (due - today).days)


def format_status(status: LoginStatus | None) -> str:
    if status is None:
        return "🔑 Логин Claude: дата не задана. Отправь <code>/token ДД.ММ.ГГГГ</code>"
    fmt = "%d.%m.%Y"
    if status.days_left > 0:
        left = f"через {status.days_left} дн."
    elif status.days_left == 0:
        left = "сегодня"
    else:
        left = f"просрочено на {-status.days_left} дн."
    return (
        f"🔑 Логин Claude: обновлён {status.last_login.strftime(fmt)}, "
        f"следующий до {status.due.strftime(fmt)} ({left})"
    )


def login_reminder(
    login: dict[str, Any], today: date, period_days: int, warn_days: int
) -> str | None:
    """Текст напоминания или None. Каждая стадия (warn / overdue) шлётся один раз на срок."""
    status = login_status(login, today, period_days)
    if status is None or status.days_left > warn_days:
        return None
    stage = "overdue" if status.days_left < 0 else "warn"
    marker = f"{status.due.isoformat()}:{stage}"
    if login.get("reminded") == marker:
        return None
    login["reminded"] = marker
    if stage == "overdue":
        return (
            f"🚨 <b>Логин Claude просрочен</b> на {-status.days_left} дн. Обнови токен в "
            "rotate-proxy и отправь новую дату: <code>/token сегодня</code>"
        )
    when = "сегодня" if status.days_left == 0 else f"через {status.days_left} дн."
    return (
        f"⏳ <b>Пора обновить логин Claude</b>: срок {status.due.strftime('%d.%m.%Y')} ({when}). "
        "После обновления отправь <code>/token сегодня</code>"
    )


def set_last_login(login: dict[str, Any], value: date) -> None:
    login["last_login"] = value.isoformat()
    login.pop("reminded", None)
