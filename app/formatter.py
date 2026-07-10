"""Форматирование сообщений для команд /status и /next."""

from __future__ import annotations

from datetime import datetime, timezone

from .limits_api import Credential


def _fmt_delta(target: datetime | None) -> str:
    if not target:
        return "?"
    delta = int((target - datetime.now(timezone.utc)).total_seconds())
    if delta <= 0:
        return "сейчас"
    d, rem = divmod(delta, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"~{d}д {h}ч"
    if h:
        return f"~{h}ч {m}м"
    return f"~{m}м"


def _pct(v: float | None) -> str:
    return f"{v:.0f}%" if v is not None else "?"


def format_status(creds: list[Credential]) -> str:
    if not creds:
        return "Нет аккаунтов в ответе API."
    lines: list[str] = []
    for c in creds:
        fh = c.limits.five_hour
        sd = c.limits.seven_day
        lines.append(f"<b>{c.name}</b> · {c.plan or '?'}")
        if fh:
            lines.append(f"  5h:  {_pct(fh.utilization_pct)}  (сброс {_fmt_delta(fh.resets_at)})")
        if sd:
            lines.append(f"  7d:  {_pct(sd.utilization_pct)}  (сброс {_fmt_delta(sd.resets_at)})")
        eu = c.extra_usage
        if eu.is_enabled and eu.utilization_pct is not None:
            lines.append(
                f"  💸 overflow: {_pct(eu.utilization_pct)} "
                f"({eu.used_credits:.0f}/{eu.monthly_limit:.0f} {eu.currency or ''})"
            )
        if c.quota.exceeded:
            lines.append(f"  ⛔ квота исчерпана (восст. {_fmt_delta(c.quota.next_recover_at)})")
        lines.append("")
    return "\n".join(lines).strip()


def format_next(creds: list[Credential]) -> str:
    """Ближайшее по времени обнуление среди всех окон/аккаунтов."""
    candidates: list[tuple[datetime, str]] = []
    for c in creds:
        if c.limits.five_hour and c.limits.five_hour.resets_at:
            candidates.append((c.limits.five_hour.resets_at, f"5h · {c.name}"))
        if c.limits.seven_day and c.limits.seven_day.resets_at:
            candidates.append((c.limits.seven_day.resets_at, f"7d · {c.name}"))
    if not candidates:
        return "Нет данных о времени обнуления."
    candidates.sort(key=lambda x: x[0])
    when, label = candidates[0]
    return f"Ближайшее обнуление: <b>{label}</b> через {_fmt_delta(when)}."
