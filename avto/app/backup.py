"""Резервные копии базы: раз в сутки в папку backup/ (или указанную в настройках), хранятся 30 последних."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path

from .config import BASE_DIR

KEEP = 30
log = logging.getLogger("avto.backup")
NAME_RE = re.compile(r"^avto_(\d{4}-\d{2}-\d{2})\.db$")


def backup_dir(setting: str) -> Path:
    p = Path(setting)
    return p if p.is_absolute() else BASE_DIR / p


def make_backup(db, target_dir: Path, day: str, keep: int = KEEP) -> Path:
    """Копия базы средствами SQLite (безопасно во время работы). Имя: avto_ГГГГ-ММ-ДД.db."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"avto_{day}.db"
    tmp = target.with_suffix(".tmp")
    with db.lock:
        dst = sqlite3.connect(str(tmp))
        try:
            db.conn.backup(dst)
        finally:
            dst.close()
    tmp.replace(target)
    files = sorted((p for p in target_dir.iterdir() if NAME_RE.match(p.name)), key=lambda p: p.name)
    for old in files[:-keep] if keep else []:
        old.unlink(missing_ok=True)
    return target


def list_backups(target_dir: Path) -> list[dict]:
    if not target_dir.exists():
        return []
    res = []
    for p in sorted(target_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if NAME_RE.match(p.name):
            res.append({"name": p.name, "size": p.stat().st_size})
    return res


def ensure_today(svc) -> Path | None:
    s = svc.settings()
    d = backup_dir(s["backup_dir"])
    day = svc.now().date().isoformat()
    if (d / f"avto_{day}.db").exists():
        return None
    return make_backup(svc.db, d, day)


class BackupScheduler(threading.Thread):
    """Раз в 30 минут проверяет, есть ли копия за сегодня, и делает её при необходимости."""

    def __init__(self, svc, interval=1800):
        super().__init__(daemon=True, name="backup")
        self.svc = svc
        self.interval = interval
        self.stop_event = threading.Event()
        self.last_error = None

    def run(self):
        while not self.stop_event.is_set():
            try:
                p = ensure_today(self.svc)
                if p:
                    log.info("Резервная копия: %s", p)
                self.last_error = None
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                log.exception("Не удалось сделать резервную копию")
            self.stop_event.wait(self.interval)
