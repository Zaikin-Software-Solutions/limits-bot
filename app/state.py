"""Снапшот прошлого опроса на диске (простой JSON, без БД).

Хранит per-email последние проценты/время сброса и флаги уже отправленных
edge-уведомлений — чтобы пороги и предупреждения не повторялись на каждом тике.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: str):
        self.path = Path(path)
        self._data: dict[str, Any] = {"accounts": {}}

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {"accounts": {}}
        self._data.setdefault("accounts", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # атомарная запись: temp в той же папке + os.replace
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @property
    def is_fresh(self) -> bool:
        """True, пока не зафиксирован baseline с РЕАЛЬНЫМИ данными.

        Пустые ответы (upstream 429 → seven_day/five_hour = null) сохраняются, но
        не считаются baseline: первое настоящее сравнение — с первым непустым
        снапшотом, иначе на переходе null→числа прилетит ложное событие.
        """
        for acc in self._data["accounts"].values():
            if acc.get("seven_day_pct") is not None or acc.get("five_hour_pct") is not None:
                return False
        return True

    def account(self, email: str) -> dict[str, Any]:
        return self._data["accounts"].get(email, {})

    def set_account(self, email: str, data: dict[str, Any]) -> None:
        self._data["accounts"][email] = data

    def health(self) -> dict[str, Any]:
        return self._data.setdefault("health", {})
