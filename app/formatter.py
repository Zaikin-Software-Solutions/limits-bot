"""Форматирование сообщений для команд /status, /next, /models."""

from __future__ import annotations

from datetime import datetime, timezone

from .limits_api import Credential

# Часовой пояс для человекочитаемых дат (МСК = UTC+3).
MSK = timezone(__import__("datetime").timedelta(hours=3))


def fmt_countdown(target: datetime | None) -> str:
    """«2д 4ч 30м» / «5ч 12м» / «43м» / «сейчас» до целевого времени."""
    if not target:
        return "?"
    delta = int((target - datetime.now(timezone.utc)).total_seconds())
    if delta <= 0:
        return "сейчас"
    d, rem = divmod(delta, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts: list[str] = []
    if d:
        parts.append(f"{d}д")
    if h:
        parts.append(f"{h}ч")
    if m and not d:  # минуты показываем только если < суток
        parts.append(f"{m}м")
    return " ".join(parts) or "меньше минуты"


def fmt_when(target: datetime | None) -> str:
    """Абсолютная дата обнуления в МСК: «16 июля 04:00»."""
    if not target:
        return "?"
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    local = target.astimezone(MSK)
    return f"{local.day} {months[local.month - 1]} {local.strftime('%H:%M')} МСК"


def bar(pct: float | None, width: int = 10) -> str:
    """Прогресс-бар [███░░░░░░░] по проценту утилизации."""
    if pct is None:
        return "░" * width
    filled = int(round(pct / 100 * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _pct(v: float | None) -> str:
    return f"{v:.0f}%" if v is not None else "?"


def fmt_money(cents: float | None, currency: str | None) -> str:
    """extra_usage приходит в центах (monthly_limit=16000 → $160.00). Форматируем в $."""
    if cents is None:
        return "?"
    sym = "$" if (currency or "").upper() == "USD" else ""
    val = cents / 100
    suffix = "" if sym else f" {currency or ''}".rstrip()
    return f"{sym}{val:,.2f}{suffix}"


def _window_line(label: str, w) -> str:
    """Строка окна: 5h [███░░░] 7% · осталось 93% · сброс через 5ч (16 июля 04:00)."""
    util = w.utilization_pct
    rem = w.remaining_pct
    cd = fmt_countdown(w.resets_at)
    when = fmt_when(w.resets_at)
    rem_txt = f"осталось {_pct(rem)} · " if rem is not None else ""
    return (
        f"{label} {bar(util)} {_pct(util)}\n"
        f"     {rem_txt}сброс через <b>{cd}</b> ({when})"
    )


def format_status(creds: list[Credential]) -> str:
    if not creds:
        return "Нет аккаунтов (источник недоступен или отфильтрован)."
    lines: list[str] = []
    for c in creds:
        tier = f" ({c.rate_limit_tier})" if c.rate_limit_tier else ""
        lines.append(f"<b>{c.label}</b> · {c.plan or '?'}{tier}")
        if c.limits.five_hour and c.limits.five_hour.utilization_pct is not None:
            lines.append(_window_line("5h ", c.limits.five_hour))
        if c.limits.seven_day and c.limits.seven_day.utilization_pct is not None:
            lines.append(_window_line("7d ", c.limits.seven_day))
        # раздельные недельные лимиты по моделям — если Anthropic их включит
        if c.limits.seven_day_opus and c.limits.seven_day_opus.utilization_pct is not None:
            lines.append(_window_line("7d Opus  ", c.limits.seven_day_opus))
        if c.limits.seven_day_sonnet and c.limits.seven_day_sonnet.utilization_pct is not None:
            lines.append(_window_line("7d Sonnet", c.limits.seven_day_sonnet))
        eu = c.extra_usage
        if eu.is_enabled and eu.utilization_pct is not None:
            used = fmt_money(eu.used_credits, eu.currency)
            limit = fmt_money(eu.monthly_limit, eu.currency)
            lines.append(
                f"💸 overflow {bar(eu.utilization_pct)} {_pct(eu.utilization_pct)}\n"
                f"     потрачено {used} из {limit}"
            )
        if c.quota.exceeded:
            lines.append(f"⛔ квота исчерпана (восст. через {fmt_countdown(c.quota.next_recover_at)})")
        lines.append("")
    if not any("5h" in ln or "7d" in ln for ln in lines):
        lines.append("⏳ API временно отдаёт пустые лимиты (upstream rate-limit), попробуй позже.")
    return "\n".join(lines).strip()


def format_next(creds: list[Credential]) -> str:
    """Все окна обнуления, отсортированы по времени, с днями/часами."""
    rows: list[tuple[datetime, str]] = []
    for c in creds:
        fh = c.limits.five_hour
        sd = c.limits.seven_day
        if fh and fh.resets_at:
            rows.append((fh.resets_at, f"5h · {c.label}"))
        if sd and sd.resets_at:
            rows.append((sd.resets_at, f"7d · {c.label}"))
        if c.limits.seven_day_opus and c.limits.seven_day_opus.resets_at:
            rows.append((c.limits.seven_day_opus.resets_at, f"7d Opus · {c.label}"))
        if c.limits.seven_day_sonnet and c.limits.seven_day_sonnet.resets_at:
            rows.append((c.limits.seven_day_sonnet.resets_at, f"7d Sonnet · {c.label}"))
    if not rows:
        return "⏳ Нет данных о времени обнуления (API отдаёт пустышку). Попробуй позже."
    rows.sort(key=lambda x: x[0])
    out = ["<b>Обнуление окон:</b>"]
    for when, label in rows:
        out.append(f"• {label}: через <b>{fmt_countdown(when)}</b> ({fmt_when(when)})")
    return "\n".join(out)


def _models_block(title: str, model_ids: list[tuple[str, str]], err: str | None) -> list[str]:
    """Блок одного прокси: заголовок + модели по owner, либо ошибка."""
    if err:
        return [f"<b>{title}</b>", f"  ⚠️ {err}"]
    if not model_ids:
        return [f"<b>{title}</b>", "  (пусто)"]
    by_owner: dict[str, list[str]] = {}
    for owner, mid in sorted(model_ids):
        by_owner.setdefault(owner, []).append(mid)
    out = [f"<b>{title}</b> · {len(model_ids)} шт"]
    for owner, ids in by_owner.items():
        out.append(f"  <u>{owner}</u>: " + ", ".join(f"<code>{m}</code>" for m in ids))
    return out


def format_models_split(
    rodionov: tuple[list[tuple[str, str]], str | None],
    zspzvs: tuple[list[tuple[str, str]], str | None],
) -> str:
    """Модели двух прокси раздельно. Каждый аргумент = (список, текст-ошибки|None)."""
    lines: list[str] = ["🤖 <b>Доступные модели</b>", ""]
    lines += _models_block("🟣 rodionov (Claude/Gemini)", rodionov[0], rodionov[1])
    lines.append("")
    lines += _models_block("🟠 zspzvs (Codex/OpenAI)", zspzvs[0], zspzvs[1])
    return "\n".join(lines)
