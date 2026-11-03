"""База SQLite одним файлом. Все обращения идут через одну блокировку:
при 10 пользователях это проще и надёжнее, чем параллельная запись."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
  pin_hash TEXT, pin_salt TEXT, active INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
  deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT, expires_at TEXT);
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'Разовый', comment TEXT,
  deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE INDEX IF NOT EXISTS clients_key ON clients(name_key);
CREATE TABLE IF NOT EXISTS carriers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
  phone TEXT, directions TEXT, vehicle_types TEXT,
  deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE INDEX IF NOT EXISTS carriers_key ON carriers(name_key);
CREATE TABLE IF NOT EXISTS trips(
  id INTEGER PRIMARY KEY,
  ship_date TEXT, truck_brand TEXT, plate TEXT, driver TEXT,
  origin TEXT, destination TEXT, price INTEGER, body_type TEXT, payment_doc TEXT,
  driver_location TEXT, client_id INTEGER, employee_id INTEGER, carrier_id INTEGER,
  carrier_rate INTEGER, ttn TEXT, pay_client TEXT, pay_agent TEXT, postpay TEXT,
  client_type TEXT, stage INTEGER NOT NULL DEFAULT 1, last_contact_at TEXT,
  refusal_reason TEXT, comment TEXT, received_at TEXT,
  imported INTEGER NOT NULL DEFAULT 0, import_key TEXT,
  version INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT, created_by INTEGER, updated_at TEXT, updated_by INTEGER);
CREATE INDEX IF NOT EXISTS trips_import_key ON trips(import_key);
CREATE TABLE IF NOT EXISTS trip_stage_history(
  id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL, stage INTEGER NOT NULL,
  at TEXT NOT NULL, user_id INTEGER, imported INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS tsh_trip ON trip_stage_history(trip_id);
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY, company TEXT NOT NULL, company_key TEXT NOT NULL,
  source TEXT, owner_id INTEGER, contact_name TEXT, contact_phone TEXT,
  cargo TEXT, volume TEXT, stage INTEGER NOT NULL DEFAULT 1,
  next_step TEXT, next_step_date TEXT, refusal_reason TEXT, comment TEXT,
  client_id INTEGER, version INTEGER NOT NULL DEFAULT 1,
  deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT, created_by INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS lead_stage_history(
  id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL, stage INTEGER NOT NULL,
  at TEXT NOT NULL, user_id INTEGER);
CREATE INDEX IF NOT EXISTS lsh_lead ON lead_stage_history(lead_id);
CREATE TABLE IF NOT EXISTS daily_reports(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, date TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0, proposals INTEGER NOT NULL DEFAULT 0,
  contacts INTEGER NOT NULL DEFAULT 0, meetings INTEGER NOT NULL DEFAULT 0,
  notes TEXT, deleted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT, updated_at TEXT, updated_by INTEGER,
  UNIQUE(user_id, date));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS holidays(date TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, user_name TEXT,
  entity TEXT NOT NULL, entity_id INTEGER, action TEXT NOT NULL, changes TEXT);
CREATE INDEX IF NOT EXISTS audit_entity ON audit_log(entity, entity_id);
"""

TABLES = ["users", "trips", "trip_stage_history", "leads", "lead_stage_history", "daily_reports",
          "clients", "carriers", "settings", "holidays", "audit_log"]


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._depth = 0

    @contextmanager
    def tx(self):
        """Транзакция записи. Вложенные вызовы работают внутри внешней."""
        with self.lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self.conn
                finally:
                    self._depth -= 1
                return
            self._depth = 1
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    def close(self):
        with self.lock:
            self.conn.close()


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)
