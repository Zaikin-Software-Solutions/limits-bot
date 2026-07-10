"""Диффер: сравнивает свежий ответ API с прошлым снапшотом → список событий.

Событие weekly_reset ловится по факту (7d% резко упал ИЛИ пройден прошлый
resets_at), а не по «текущему моменту» — это устойчиво к неточному расписанию
поллинга. Пороги и предупреждения — edge-triggered через notified_flags в
снапшоте, чтобы не спамить на каждом тике.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .limits_api import Credential
from .state import State

# Насколько должен упасть 7d%, чтобы счесть это обнулением (а не обычным дрейфом).
RESET_DROP_PCT = 15.0


@dataclass
class Event:
    type: str
    email: str
    text: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pct(v: float | None) -> float | None:
    return round(v, 1) if v is not None else None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_delta(target: datetime | None) -> str:
    """«2д 4ч» / «5ч 12м» / «43м» до цели."""
    if not target:
        return "неизвестно когда"
    delta = int((target - _now()).total_seconds())
    if delta <= 0:
        return "сейчас"
    d, rem = divmod(delta, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}д {h}ч"
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def _fmt_when(target: datetime | None) -> str:
    """Абсолютная дата обнуления в МСК: «16 июля 04:00»."""
    if not target:
        return "?"
    msk = timezone(timedelta(hours=3))
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    local = target.astimezone(msk)
    return f"{local.day} {months[local.month - 1]} {local.strftime('%H:%M')} МСК"


def _crossed(prev: float | None, cur: float | None, threshold: float) -> bool:
    """Порог пересечён вверх между опросами."""
    if cur is None:
        return False
    p = prev if prev is not None else 0.0
    return p < threshold <= cur


def has_limits_data(cred: Credential) -> bool:
    """True, если API вернул реальные окна лимитов (а не null из-за upstream 429).

    rotate-proxy кэширует usage; при протухшем кэше и rate-limit на upstream он
    отдаёт five_hour/seven_day = null. Такой ответ НЕ должен затирать снапшот и
    не должен порождать события.
    """
    sd = cred.limits.seven_day
    fh = cred.limits.five_hour
    return (sd is not None and sd.utilization_pct is not None) or (
        fh is not None and fh.utilization_pct is not None
    )


def snapshot_of(cred: Credential) -> dict[str, Any]:
    """Собрать текущий снапшот аккаунта (без notified_flags — их мержим отдельно)."""
    sd = cred.limits.seven_day
    fh = cred.limits.five_hour
    return {
        "seven_day_pct": _pct(sd.utilization_pct) if sd else None,
        "seven_day_resets_at": _iso(sd.resets_at) if sd else None,
        "five_hour_pct": _pct(fh.utilization_pct) if fh else None,
        "extra_usage_pct": _pct(cred.extra_usage.utilization_pct),
        "quota_exceeded": cred.quota.exceeded,
        "last_seen_at": _iso(_now()),
    }


def detect(
    cred: Credential,
    prev: dict[str, Any],
    *,
    thresholds: list[int],
    weekly_warn_hours: float,
    extra_usage_warn_pct: float,
) -> tuple[list[Event], dict[str, Any]]:
    """Вернуть (события, новый снапшот-аккаунта с обновлёнными notified_flags).

    Если API вернул пустые лимиты (upstream 429) — сохраняем прошлый снапшот как
    есть, событий не шлём: пустышка не должна выглядеть как «обнулилось».
    """
    if not has_limits_data(cred):
        keep = dict(prev)
        keep["last_seen_at"] = _iso(_now())
        return [], keep

    events: list[Event] = []
    cur = snapshot_of(cred)
    name = cred.name
    flags: dict[str, Any] = dict(prev.get("notified_flags", {}))

    prev_7d = prev.get("seven_day_pct")
    cur_7d = cur["seven_day_pct"]
    prev_reset = _parse_iso(prev.get("seven_day_resets_at"))
    cur_reset = _parse_iso(cur["seven_day_resets_at"])

    # ── Недельное обнуление ────────────────────────────────────────────────
    reset_by_drop = (
        prev_7d is not None
        and cur_7d is not None
        and (prev_7d - cur_7d) >= RESET_DROP_PCT
    )
    # окно сменилось: прошлый resets_at уже в прошлом ИЛИ новый resets_at позже прошлого
    reset_by_window = (
        prev_reset is not None
        and cur_reset is not None
        and cur_reset > prev_reset
    )
    if reset_by_drop or reset_by_window:
        was = f"{prev_7d:.0f}%" if prev_7d is not None else "?"
        now_pct = f"{cur_7d:.0f}%" if cur_7d is not None else "?"
        next_reset = (
            f"\nСледующее обнуление через {_fmt_delta(cur_reset)} ({_fmt_when(cur_reset)})."
            if cur_reset
            else ""
        )
        events.append(
            Event(
                "weekly_reset",
                cred.email,
                f"🔄 Недельный лимит обнулился ({name}). Было 7d:{was} → стало {now_pct}. "
                f"Можно грузить по полной.{next_reset}",
            )
        )
        flags = {}  # новое окно — сбрасываем все edge-флаги

    # ── Предупреждение «скоро обнулится» ───────────────────────────────────
    if cur_reset is not None:
        secs_left = (cur_reset - _now()).total_seconds()
        warn_key = f"weekly_warn:{cur['seven_day_resets_at']}"
        if 0 < secs_left <= weekly_warn_hours * 3600 and not flags.get(warn_key):
            pct = f"{cur_7d:.0f}%" if cur_7d is not None else "?"
            events.append(
                Event(
                    "weekly_reset_soon",
                    cred.email,
                    f"⏳ Недельный лимит обнулится через {_fmt_delta(cur_reset)} "
                    f"({_fmt_when(cur_reset)}, {name}). Сейчас 7d:{pct}.",
                )
            )
            flags[warn_key] = True

    # ── Пороги «скоро исчерпаешь» (7d и 5h) ────────────────────────────────
    for th in thresholds:
        key_7d = f"th7d:{th}"
        if _crossed(prev_7d, cur_7d, th) and not flags.get(key_7d):
            events.append(
                Event(
                    "threshold_7d",
                    cred.email,
                    f"⚠️ Недельный лимит достиг {th}% ({name}). "
                    f"Обнуление через {_fmt_delta(cur_reset)} ({_fmt_when(cur_reset)}).",
                )
            )
            flags[key_7d] = True

        key_5h = f"th5h:{th}"
        prev_5h = prev.get("five_hour_pct")
        cur_5h = cur["five_hour_pct"]
        if _crossed(prev_5h, cur_5h, th) and not flags.get(key_5h):
            fh = cred.limits.five_hour
            fh_reset = fh.resets_at if fh else None
            events.append(
                Event(
                    "threshold_5h",
                    cred.email,
                    f"⚠️ 5-часовой лимит достиг {th}% ({name}). "
                    f"Сброс через {_fmt_delta(fh_reset)} ({_fmt_when(fh_reset)}).",
                )
            )
            flags[key_5h] = True

    # если 5h окно сбросилось (упало) — снимаем 5h-флаги, чтобы сработали в след. окне
    prev_5h = prev.get("five_hour_pct")
    cur_5h = cur["five_hour_pct"]
    if prev_5h is not None and cur_5h is not None and (prev_5h - cur_5h) >= RESET_DROP_PCT:
        flags = {k: v for k, v in flags.items() if not k.startswith("th5h:")}

    # ── extra_usage overflow ───────────────────────────────────────────────
    eu = cred.extra_usage
    if eu.is_enabled and eu.utilization_pct is not None:
        key_eu = f"extra:{int(extra_usage_warn_pct)}"
        prev_eu = prev.get("extra_usage_pct")
        if _crossed(prev_eu, eu.utilization_pct, extra_usage_warn_pct) and not flags.get(key_eu):
            used = eu.used_credits or 0
            limit = eu.monthly_limit or 0
            cur_sym = eu.currency or ""
            events.append(
                Event(
                    "extra_usage_high",
                    cred.email,
                    f"💸 Платный overflow достиг {eu.utilization_pct:.0f}% "
                    f"({used:.0f}/{limit:.0f} {cur_sym}, {name}).",
                )
            )
            flags[key_eu] = True

    # ── quota exceeded / recovered ─────────────────────────────────────────
    prev_q = prev.get("quota_exceeded", False)
    cur_q = cred.quota.exceeded
    if cur_q and not prev_q:
        recover = _fmt_delta(cred.quota.next_recover_at) if cred.quota.next_recover_at else "неизвестно"
        events.append(
            Event(
                "quota_exceeded",
                cred.email,
                f"⛔ Лимит полностью исчерпан ({name}). Восстановление: {recover}.",
            )
        )
    elif prev_q and not cur_q:
        events.append(
            Event("quota_recovered", cred.email, f"✅ Квота восстановлена ({name}).")
        )

    cur["notified_flags"] = flags
    return events, cur
