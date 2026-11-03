"""Работа с данными: рейсы, лиды, отчёты, справочники, сотрудники, журнал.
Каждая операция проверяет права пользователя и пишет журнал изменений."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import auth
from . import logic as L
from .db import Database, dumps, one, rows


class AppError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.extra = extra or {}


def forbid(msg="Недостаточно прав для этого действия"):
    raise AppError(403, msg, "forbidden")


def need(user: dict, perm: str):
    if not auth.can(user, perm):
        forbid()


TRIP_TEXT = ["truck_brand", "plate", "driver", "origin", "destination", "body_type", "payment_doc",
             "driver_location", "ttn", "pay_client", "pay_agent", "postpay", "client_type", "comment"]
TRIP_MONEY = ["price", "carrier_rate"]
TRIP_COLUMNS = TRIP_TEXT + TRIP_MONEY + ["ship_date", "client_id", "employee_id", "carrier_id"]
LEAD_TEXT = ["source", "contact_name", "contact_phone", "cargo", "volume", "next_step", "comment"]


def _text(v, limit=500):
    if v is None:
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def _money(v, label):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        n = round(float(str(v).replace(" ", "").replace(" ", "").replace(",", ".")))
    except ValueError:
        raise AppError(400, f"«{label}»: нужно число")
    if n < 0:
        raise AppError(400, f"«{label}»: число не может быть отрицательным")
    return n


def _date_str(v, label):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise AppError(400, f"«{label}»: неверная дата")


def _int(v, label, minimum=0):
    if v is None or v == "":
        return 0
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise AppError(400, f"«{label}»: нужно целое число")
    if n < minimum:
        raise AppError(400, f"«{label}»: не меньше {minimum}")
    return n


class Service:
    def __init__(self, db: Database, now_fn=L.now_local):
        self.db = db
        self.now_fn = now_fn

    def now(self) -> datetime:
        return self.now_fn()

    def ts(self) -> str:
        return L.fmt_dt(self.now())

    # ------------------------------------------------------------ настройки

    def settings(self, conn=None) -> dict:
        conn = conn or self.db.conn
        s = dict(L.DEFAULT_SETTINGS)
        for r in conn.execute("SELECT key, value FROM settings"):
            if r["key"] in s:
                s[r["key"]] = json.loads(r["value"])
        return s

    def holidays(self, conn=None) -> list[dict]:
        conn = conn or self.db.conn
        return rows(conn.execute("SELECT date, name FROM holidays ORDER BY date"))

    def calendar(self, conn=None, s=None) -> L.WorkCalendar:
        s = s or self.settings(conn)
        return L.WorkCalendar.from_settings(s, [h["date"] for h in self.holidays(conn)])

    def update_settings(self, user, data: dict) -> dict:
        need(user, "admin")
        with self.db.tx() as conn:
            old = self.settings(conn)
            changes = {}
            for key, value in data.items():
                if key not in L.DEFAULT_SETTINGS:
                    continue
                value = self._check_setting(key, value)
                if value != old[key]:
                    changes[key] = [old[key], value]
                    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, dumps(value)))
            new = self.settings(conn)
            L.WorkCalendar.from_settings(new)  # проверка рабочего времени
            if changes:
                self.audit(conn, user, "settings", None, "update", changes)
            return new

    @staticmethod
    def _check_setting(key, value):
        if key == "work_days":
            days = sorted({int(d) for d in value if 0 <= int(d) <= 6})
            if not days:
                raise AppError(400, "Нужен хотя бы один рабочий день")
            return days
        if key in ("work_start", "work_end", "report_reminder"):
            try:
                L._hm(value)
            except Exception:
                raise AppError(400, f"Время в формате ЧЧ:ММ, получено «{value}»")
            return str(value)
        if key == "backup_dir":
            v = str(value or "").strip()
            if not v:
                raise AppError(400, "Укажите папку для резервных копий")
            return v
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise AppError(400, f"Настройка {key}: нужно число")
        if n < 0:
            raise AppError(400, f"Настройка {key}: не может быть отрицательной")
        return int(n) if n == int(n) else n

    def set_holidays(self, user, items: list[dict]) -> list[dict]:
        need(user, "admin")
        with self.db.tx() as conn:
            old = [h["date"] for h in self.holidays(conn)]
            conn.execute("DELETE FROM holidays")
            for h in items:
                d = _date_str(h.get("date"), "Дата праздника")
                if d:
                    conn.execute("INSERT OR REPLACE INTO holidays(date, name) VALUES(?, ?)", (d, _text(h.get("name"))))
            new = self.holidays(conn)
            self.audit(conn, user, "holidays", None, "update", {"dates": [old, [h["date"] for h in new]]})
            return new

    # ------------------------------------------------------------ журнал

    def audit(self, conn, user, entity, entity_id, action, changes=None):
        conn.execute(
            "INSERT INTO audit_log(at, user_id, user_name, entity, entity_id, action, changes) VALUES(?,?,?,?,?,?,?)",
            (self.ts(), user["id"] if user else None, user["name"] if user else "система",
             entity, entity_id, action, dumps(changes) if changes is not None else None))

    def audit_list(self, user, entity=None, entity_id=None, limit=300) -> list[dict]:
        if entity not in ("trip", "lead") or entity_id is None:
            need(user, "admin")
        q, p = "SELECT * FROM audit_log WHERE 1=1", []
        if entity:
            q += " AND entity=?"
            p.append(entity)
        if entity_id is not None:
            q += " AND entity_id=?"
            p.append(entity_id)
        q += " ORDER BY id DESC LIMIT ?"
        p.append(min(int(limit), 2000))
        with self.db.lock:
            res = rows(self.db.conn.execute(q, p))
        for r in res:
            r["changes"] = json.loads(r["changes"]) if r["changes"] else None
        return res

    # ------------------------------------------------------------ сотрудники и вход

    def init_defaults(self):
        with self.db.tx() as conn:
            if one(conn.execute("SELECT COUNT(*) n FROM users"))["n"]:
                return
            now = self.ts()
            for name, role in (("Жексебаев М.", "manager"), ("Меден Д.", "manager"),
                               ("Тулебаев М. Б.", "head"), ("Администратор", "admin")):
                conn.execute("INSERT INTO users(name, role, created_at) VALUES(?,?,?)", (name, role, now))

    def needs_setup(self) -> bool:
        with self.db.lock:
            r = one(self.db.conn.execute(
                "SELECT COUNT(*) n FROM users WHERE role='admin' AND pin_hash IS NOT NULL AND deleted=0 AND active=1"))
        return r["n"] == 0

    def setup_admin(self, pin: str) -> dict:
        """Первый запуск: задать PIN администратора (пока ни у одного админа нет PIN)."""
        if not auth.valid_pin(pin):
            raise AppError(400, "PIN — от 4 до 6 цифр")
        with self.db.tx() as conn:
            if not self.needs_setup():
                forbid("Администратор уже настроен")
            adm = one(conn.execute("SELECT * FROM users WHERE role='admin' AND deleted=0 ORDER BY id LIMIT 1"))
            if not adm:
                cur = conn.execute("INSERT INTO users(name, role, created_at) VALUES('Администратор','admin',?)",
                                   (self.ts(),))
                adm = one(conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)))
            h, salt = auth.hash_pin(pin)
            conn.execute("UPDATE users SET pin_hash=?, pin_salt=?, active=1 WHERE id=?", (h, salt, adm["id"]))
            self.audit(conn, adm, "user", adm["id"], "setup_pin")
            return adm

    def login_users(self) -> list[dict]:
        with self.db.lock:
            res = rows(self.db.conn.execute(
                "SELECT id, name, role, pin_hash IS NOT NULL AS has_pin FROM users "
                "WHERE active=1 AND deleted=0 ORDER BY CASE role WHEN 'manager' THEN 0 WHEN 'head' THEN 1 "
                "WHEN 'exec' THEN 2 ELSE 3 END, name"))
        for r in res:
            r["has_pin"] = bool(r["has_pin"])
        return res

    def login(self, user_id, pin) -> dict:
        now = self.now()
        error = None
        with self.db.tx() as conn:
            u = one(conn.execute("SELECT * FROM users WHERE id=? AND active=1 AND deleted=0", (user_id,)))
            if not u:
                raise AppError(401, "Сотрудник не найден")
            if u["locked_until"] and L.parse_dt(u["locked_until"]) > now:
                mins = int((L.parse_dt(u["locked_until"]) - now).total_seconds() // 60) + 1
                raise AppError(423, f"Вход заблокирован после {auth.MAX_FAILS} неверных PIN. "
                                    f"Попробуйте через {mins} мин.", "locked")
            if not u["pin_hash"]:
                raise AppError(401, "PIN не задан. Обратитесь к администратору")
            if not auth.check_pin(str(pin or ""), u["pin_hash"], u["pin_salt"]):
                # счётчик ошибок сохраняем, поэтому ошибку поднимаем после фиксации транзакции
                fails = u["failed_attempts"] + 1
                if fails >= auth.MAX_FAILS:
                    conn.execute("UPDATE users SET failed_attempts=0, locked_until=? WHERE id=?",
                                 (L.fmt_dt(auth.lock_until(now)), u["id"]))
                    self.audit(conn, u, "user", u["id"], "locked")
                    error = AppError(423, f"Неверный PIN. Вход заблокирован на {auth.LOCK_MINUTES} минут", "locked")
                else:
                    conn.execute("UPDATE users SET failed_attempts=? WHERE id=?", (fails, u["id"]))
                    error = AppError(401, f"Неверный PIN. Осталось попыток: {auth.MAX_FAILS - fails}", "bad_pin")
            else:
                token = auth.new_token()
                exp = auth.session_expiry(now)
                conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (u["id"],))
                conn.execute("DELETE FROM sessions WHERE expires_at < ?", (L.fmt_dt(now),))
                conn.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES(?,?,?,?)",
                             (auth.token_hash(token), u["id"], L.fmt_dt(now), L.fmt_dt(exp)))
                self.audit(conn, u, "user", u["id"], "login")
        if error:
            raise error
        return {"token": token, "expires_at": L.fmt_dt(exp), "user": self.public_user(u)}

    def logout(self, token: str):
        with self.db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (auth.token_hash(token),))

    def user_by_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self.db.lock:
            r = one(self.db.conn.execute(
                "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND u.active=1 AND u.deleted=0", (auth.token_hash(token),)))
        if not r or L.parse_dt(r["expires_at"]) < self.now():
            return None
        return r

    @staticmethod
    def public_user(u: dict) -> dict:
        return {"id": u["id"], "name": u["name"], "role": u["role"], "role_name": L.ROLES.get(u["role"], u["role"]),
                "permissions": sorted(auth.PERMISSIONS.get(u["role"], set()))}

    def me(self, user) -> dict:
        res = self.public_user(user)
        res["report_reminder"] = self.report_reminder(user)
        res["expires_at"] = user.get("expires_at")
        return res

    def list_users(self, user) -> list[dict]:
        need(user, "admin")
        with self.db.lock:
            res = rows(self.db.conn.execute(
                "SELECT id, name, role, active, deleted, locked_until, pin_hash IS NOT NULL AS has_pin, created_at "
                "FROM users ORDER BY deleted, name"))
        for r in res:
            r["has_pin"] = bool(r["has_pin"])
            r["role_name"] = L.ROLES.get(r["role"], r["role"])
        return res

    def save_user(self, user, data: dict, user_id: int | None = None) -> dict:
        need(user, "admin")
        name = L.clean_name(data.get("name"))
        role = data.get("role")
        if not name:
            raise AppError(400, "Укажите имя сотрудника")
        if role not in L.ROLES:
            raise AppError(400, "Неизвестная роль")
        pin = data.get("pin")
        if pin and not auth.valid_pin(str(pin)):
            raise AppError(400, "PIN — от 4 до 6 цифр")
        active = 1 if data.get("active", True) else 0
        with self.db.tx() as conn:
            dup = one(conn.execute("SELECT id FROM users WHERE deleted=0 AND lower(name)=lower(?) AND id<>?",
                                   (name, user_id or 0)))
            if dup:
                raise AppError(409, f"Сотрудник «{name}» уже есть")
            if user_id is None:
                cur = conn.execute("INSERT INTO users(name, role, active, created_at) VALUES(?,?,?,?)",
                                   (name, role, active, self.ts()))
                user_id = cur.lastrowid
                self.audit(conn, user, "user", user_id, "create", {"name": [None, name], "role": [None, role]})
            else:
                old = one(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)))
                if not old:
                    raise AppError(404, "Сотрудник не найден")
                if old["role"] == "admin" and (role != "admin" or not active):
                    self._ensure_other_admin(conn, user_id)
                changes = {k: [old[k], v] for k, v in (("name", name), ("role", role), ("active", active))
                           if old[k] != v}
                conn.execute("UPDATE users SET name=?, role=?, active=? WHERE id=?", (name, role, active, user_id))
                if not active:
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                if changes:
                    self.audit(conn, user, "user", user_id, "update", changes)
            if pin:
                self._set_pin(conn, user, user_id, str(pin))
        return {"id": user_id}

    def _ensure_other_admin(self, conn, user_id):
        n = one(conn.execute("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND deleted=0 AND id<>?",
                             (user_id,)))["n"]
        if not n:
            raise AppError(400, "Нельзя убрать последнего администратора")

    def _set_pin(self, conn, user, user_id, pin):
        h, salt = auth.hash_pin(pin)
        conn.execute("UPDATE users SET pin_hash=?, pin_salt=?, failed_attempts=0, locked_until=NULL WHERE id=?",
                     (h, salt, user_id))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.audit(conn, user, "user", user_id, "set_pin")

    def set_pin(self, user, user_id: int, pin: str):
        need(user, "admin")
        if not auth.valid_pin(str(pin or "")):
            raise AppError(400, "PIN — от 4 до 6 цифр")
        with self.db.tx() as conn:
            self._set_pin(conn, user, user_id, str(pin))

    def unlock_user(self, user, user_id: int):
        need(user, "admin")
        with self.db.tx() as conn:
            conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (user_id,))
            self.audit(conn, user, "user", user_id, "unlock")

    def delete_user(self, user, user_id: int):
        need(user, "admin")
        with self.db.tx() as conn:
            u = one(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)))
            if not u:
                raise AppError(404, "Сотрудник не найден")
            if u["role"] == "admin":
                self._ensure_other_admin(conn, user_id)
            conn.execute("UPDATE users SET deleted=1, active=0 WHERE id=?", (user_id,))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self.audit(conn, user, "user", user_id, "delete")

    def all_users(self, conn) -> list[dict]:
        return rows(conn.execute("SELECT id, name, role, active, deleted FROM users ORDER BY name"))

    # ------------------------------------------------------------ справочники

    def _get_or_create(self, conn, table, name, user, extra=None):
        name = L.clean_name(name)
        if not name:
            return None
        key = L.norm_key(name)
        r = one(conn.execute(f"SELECT id FROM {table} WHERE name_key=? AND deleted=0 ORDER BY id LIMIT 1", (key,)))
        if r:
            return r["id"]
        cols = {"name": name, "name_key": key, "created_at": self.ts(), **(extra or {})}
        cur = conn.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                           list(cols.values()))
        self.audit(conn, user, table[:-1], cur.lastrowid, "create", {"name": [None, name]})
        return cur.lastrowid

    def client_id(self, conn, name, user, ctype="Разовый"):
        return self._get_or_create(conn, "clients", name, user, {"type": ctype})

    def carrier_id(self, conn, name, user):
        return self._get_or_create(conn, "carriers", name, user)

    def list_clients(self, user, include_deleted=False) -> list[dict]:
        with self.db.lock:
            conn = self.db.conn
            res = rows(conn.execute("SELECT * FROM clients" + ("" if include_deleted else " WHERE deleted=0")
                                    + " ORDER BY name"))
            stats = {r["client_id"]: r for r in rows(conn.execute(
                "SELECT client_id, COUNT(*) trips, SUM(stage>=8) delivered, MAX(COALESCE(ship_date, received_at)) last "
                "FROM trips WHERE deleted=0 AND stage<>0 GROUP BY client_id"))}
        for c in res:
            st = stats.get(c["id"], {})
            c["trips"] = st.get("trips") or 0
            c["delivered"] = st.get("delivered") or 0
            c["last_trip"] = (st.get("last") or "")[:10] or None
            c["propose_permanent"] = c["type"] == "Разовый" and c["delivered"] >= 3
        return res

    def list_carriers(self, user, include_deleted=False) -> list[dict]:
        with self.db.lock:
            conn = self.db.conn
            res = rows(conn.execute("SELECT * FROM carriers" + ("" if include_deleted else " WHERE deleted=0")
                                    + " ORDER BY name"))
            stats = {r["carrier_id"]: r for r in rows(conn.execute(
                "SELECT carrier_id, COUNT(*) trips FROM trips WHERE deleted=0 AND stage<>0 GROUP BY carrier_id"))}
        for c in res:
            c["trips"] = (stats.get(c["id"]) or {}).get("trips") or 0
        return res

    def save_directory(self, user, table, data, item_id=None):
        need(user, "admin")
        name = L.clean_name(data.get("name"))
        if not name:
            raise AppError(400, "Укажите название")
        fields = {"name": name, "name_key": L.norm_key(name)}
        if table == "clients":
            ctype = data.get("type") or "Разовый"
            if ctype not in L.CLIENT_TYPES:
                raise AppError(400, "Тип клиента: Постоянный или Разовый")
            fields["type"] = ctype
            fields["comment"] = _text(data.get("comment"))
        else:
            for k in ("phone", "directions", "vehicle_types"):
                fields[k] = _text(data.get(k))
        entity = table[:-1]
        with self.db.tx() as conn:
            dup = one(conn.execute(f"SELECT id, name FROM {table} WHERE name_key=? AND deleted=0 AND id<>?",
                                   (fields["name_key"], item_id or 0)))
            if dup:
                raise AppError(409, f"«{dup['name']}» уже есть в справочнике. Используйте объединение")
            if item_id is None:
                fields["created_at"] = self.ts()
                cur = conn.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' * len(fields))})",
                                   list(fields.values()))
                item_id = cur.lastrowid
                self.audit(conn, user, entity, item_id, "create", {"name": [None, name]})
            else:
                old = one(conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)))
                if not old:
                    raise AppError(404, "Запись не найдена")
                changes = {k: [old[k], v] for k, v in fields.items() if old[k] != v and k != "name_key"}
                conn.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in fields)} WHERE id=?",
                             [*fields.values(), item_id])
                if changes:
                    self.audit(conn, user, entity, item_id, "update", changes)
        return {"id": item_id}

    def merge_directory(self, user, table, target_id: int, source_ids: list[int]):
        """Объединение дублей («Coca Cola» и «Coca Cola »): ссылки переносятся на target."""
        need(user, "admin")
        col = "client_id" if table == "clients" else "carrier_id"
        source_ids = [int(i) for i in source_ids if int(i) != int(target_id)]
        if not source_ids:
            raise AppError(400, "Выберите записи для объединения")
        with self.db.tx() as conn:
            if not one(conn.execute(f"SELECT id FROM {table} WHERE id=? AND deleted=0", (target_id,))):
                raise AppError(404, "Основная запись не найдена")
            for sid in source_ids:
                conn.execute(f"UPDATE trips SET {col}=? WHERE {col}=?", (target_id, sid))
                if table == "clients":
                    conn.execute("UPDATE leads SET client_id=? WHERE client_id=?", (target_id, sid))
                conn.execute(f"UPDATE {table} SET deleted=1 WHERE id=?", (sid,))
            self.audit(conn, user, table[:-1], target_id, "merge", {"merged_ids": [source_ids, target_id]})

    def refs(self, user) -> dict:
        """Всё для выпадающих списков и автоподсказок."""
        with self.db.lock:
            conn = self.db.conn
            places = sorted({r["p"] for r in conn.execute(
                "SELECT origin p FROM trips WHERE deleted=0 AND origin IS NOT NULL UNION "
                "SELECT destination FROM trips WHERE deleted=0 AND destination IS NOT NULL")}, key=str.casefold)
            clients = rows(conn.execute("SELECT id, name, type FROM clients WHERE deleted=0 ORDER BY name"))
            carriers = rows(conn.execute("SELECT id, name FROM carriers WHERE deleted=0 ORDER BY name"))
            users = rows(conn.execute("SELECT id, name, role, active FROM users WHERE deleted=0 ORDER BY name"))
            s = self.settings(conn)
            body_types = sorted({r["b"] for r in conn.execute(
                "SELECT DISTINCT body_type b FROM trips WHERE body_type IS NOT NULL")} | set(L.BODY_TYPES),
                key=lambda b: (b not in L.BODY_TYPES, L.BODY_TYPES.index(b) if b in L.BODY_TYPES else 0, b))
        return {
            "places": places, "clients": clients, "carriers": carriers, "users": users,
            "trip_stages": L.TRIP_STAGES, "lead_stages": L.LEAD_STAGES,
            "board_columns": [{"key": k, "title": t, "stages": list(s_)} for k, t, s_ in L.BOARD_COLUMNS],
            "trip_refusal_reasons": L.TRIP_REFUSAL_REASONS, "lead_refusal_reasons": L.LEAD_REFUSAL_REASONS,
            "lead_sources": L.LEAD_SOURCES, "body_types": body_types, "client_types": L.CLIENT_TYPES,
            "roles": L.ROLES, "settings": s, "today": self.now().date().isoformat(),
            "required": {str(k): [L.FIELD_LABELS[f] for f in v] for k, v in L.STAGE_REQUIRED.items()},
        }

    # ------------------------------------------------------------ рейсы: чтение

    def load_trips(self, conn, include_deleted=False) -> list[dict]:
        trips = rows(conn.execute(
            "SELECT t.*, c.name client_name, u.name employee_name, k.name carrier_name FROM trips t "
            "LEFT JOIN clients c ON c.id=t.client_id LEFT JOIN users u ON u.id=t.employee_id "
            "LEFT JOIN carriers k ON k.id=t.carrier_id"
            + ("" if include_deleted else " WHERE t.deleted=0")))
        times: dict[int, dict] = {}
        for h in conn.execute("SELECT trip_id, stage, at FROM trip_stage_history ORDER BY id"):
            times.setdefault(h["trip_id"], {})[h["stage"]] = h["at"]
        for t in trips:
            t["times"] = times.get(t["id"], {})
        return trips

    def _enriched_trips(self, conn, include_deleted=False):
        s = self.settings(conn)
        cal = self.calendar(conn, s)
        now = self.now()
        trips = self.load_trips(conn, include_deleted)
        nums = L.assign_reg_numbers(trips)
        for t in trips:
            L.enrich_trip(t, s, cal, now)
            t["reg_no"] = nums.get(t["id"])
        return trips, s, cal, now

    def list_trips(self, user, scope="registry", month=None, employee_id=None, client_id=None) -> dict:
        month = month or self.now().strftime("%Y-%m")
        with self.db.lock:
            trips, _s, _cal, _now = self._enriched_trips(self.db.conn)
        if employee_id:
            trips = [t for t in trips if t["employee_id"] == int(employee_id)]
        if client_id:
            trips = [t for t in trips if t["client_id"] == int(client_id)]
        if scope == "board":
            sel = [t for t in trips if L.is_active(t) or (t["stage"] == L.CLOSED and t["month"] == month)]
            sel.sort(key=lambda t: (not t["overdue"], str(t.get("received_at") or "")), reverse=False)
        else:
            sel = [t for t in trips if t["month"] == month]
            sel.sort(key=lambda t: (t["reg_no"] is None, t["reg_no"] or 0, t["id"]))
        totals = {"price": sum(t["price"] or 0 for t in sel if t["stage"] != 0),
                  "carrier_rate": sum(t["carrier_rate"] or 0 for t in sel if t["stage"] != 0),
                  "margin": sum(t["margin"] or 0 for t in sel if t["stage"] != 0)}
        return {"month": month, "month_title": L.month_title(month), "trips": sel, "totals": totals}

    def get_trip(self, user, trip_id: int) -> dict:
        with self.db.lock:
            conn = self.db.conn
            trips, *_ = self._enriched_trips(conn, include_deleted=auth.can(user, "admin"))
            t = next((x for x in trips if x["id"] == trip_id), None)
            if not t:
                raise AppError(404, "Рейс не найден")
            t["history"] = rows(conn.execute(
                "SELECT h.stage, h.at, h.imported, u.name user_name FROM trip_stage_history h "
                "LEFT JOIN users u ON u.id=h.user_id WHERE h.trip_id=? ORDER BY h.id", (trip_id,)))
            for h in t["history"]:
                h["stage_name"] = L.TRIP_STAGES.get(h["stage"], "")
        t["log"] = self.audit_list(user, "trip", trip_id, 100)
        return t

    # ------------------------------------------------------------ рейсы: изменения

    def _trip_values(self, conn, user, data: dict, current: dict | None) -> dict:
        """Разбор полей формы. Возвращает только переданные поля."""
        v = {}
        for f in TRIP_TEXT:
            if f in data:
                v[f] = _text(data[f], 2000 if f == "comment" else 500)
        for f in TRIP_MONEY:
            if f in data:
                v[f] = _money(data[f], L.FIELD_LABELS[f])
        if "ship_date" in data:
            v["ship_date"] = _date_str(data["ship_date"], "Дата отправки")
        if "client_type" in v and v["client_type"] and v["client_type"] not in L.CLIENT_TYPES:
            raise AppError(400, "Тип клиента: Постоянный или Разовый")
        if "client_name" in data:
            cid = self.client_id(conn, data["client_name"], user, v.get("client_type") or "Разовый")
            v["client_id"] = cid
            if cid and not v.get("client_type"):
                v["client_type"] = one(conn.execute("SELECT type FROM clients WHERE id=?", (cid,)))["type"]
        if "carrier_name" in data:
            v["carrier_id"] = self.carrier_id(conn, data["carrier_name"], user)
        if "employee_id" in data and data["employee_id"] not in (None, ""):
            eid = int(data["employee_id"])
            if not one(conn.execute("SELECT id FROM users WHERE id=? AND deleted=0", (eid,))):
                raise AppError(400, "Сотрудник не найден")
            current_emp = current["employee_id"] if current else user["id"]
            if eid != current_emp and not auth.can(user, "reassign"):
                forbid("Переназначать рейсы между менеджерами может только руководитель отдела")
            v["employee_id"] = eid
        return v

    def _names_for_diff(self, conn, field, value):
        table = {"client_id": "clients", "carrier_id": "carriers", "employee_id": "users"}.get(field)
        if table and value:
            r = one(conn.execute(f"SELECT name FROM {table} WHERE id=?", (value,)))
            return r["name"] if r else value
        return value

    def _load_trip_row(self, conn, trip_id, version=None):
        t = one(conn.execute("SELECT * FROM trips WHERE id=? AND deleted=0", (trip_id,)))
        if not t:
            raise AppError(404, "Рейс не найден")
        if version is not None and int(version) != t["version"]:
            raise AppError(409, "Рейс изменён другим сотрудником, обновите", "conflict")
        return t

    def _add_trip_history(self, conn, trip_id, stage, user, at=None, imported=0):
        conn.execute("INSERT INTO trip_stage_history(trip_id, stage, at, user_id, imported) VALUES(?,?,?,?,?)",
                     (trip_id, stage, at or self.ts(), user["id"] if user else None, imported))

    def create_trip(self, user, data: dict) -> dict:
        need(user, "trips_edit")
        with self.db.tx() as conn:
            if not auth.can(user, "reassign") or data.get("employee_id") in (None, ""):
                data = {**data, "employee_id": user["id"]}
            v = self._trip_values(conn, user, data, None)
            missing = L.missing_fields(v, 1)
            if missing:
                raise AppError(400, "Заполните: " + ", ".join(missing), "missing", {"missing": missing})
            now = self.ts()
            v.update({"stage": 1, "received_at": now, "created_at": now, "created_by": user["id"],
                      "updated_at": now, "updated_by": user["id"], "version": 1})
            cur = conn.execute(f"INSERT INTO trips({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
            tid = cur.lastrowid
            self._add_trip_history(conn, tid, 1, user)
            self.audit(conn, user, "trip", tid, "create",
                       {k: [None, self._names_for_diff(conn, k, val)] for k, val in v.items()
                        if k in TRIP_COLUMNS and val is not None})
        return self.get_trip(user, tid)

    def update_trip(self, user, trip_id: int, data: dict) -> dict:
        need(user, "trips_edit")
        if "version" not in data:
            raise AppError(400, "Не передана версия рейса")
        with self.db.tx() as conn:
            cur = self._load_trip_row(conn, trip_id, data["version"])
            v = self._trip_values(conn, user, data, cur)
            if cur["stage"] == L.REFUSED and "refusal_reason" in data:
                v["refusal_reason"] = _text(data["refusal_reason"])
            merged = {**cur, **v}
            required = set(L.required_fields(cur["stage"]))
            cleared = [L.FIELD_LABELS[f] for f in required if f in v and L.is_empty(v[f]) and not L.is_empty(cur[f])]
            if cleared:
                raise AppError(400, "Нельзя очистить обязательные для этапа поля: " + ", ".join(cleared), "missing")
            changes = {k: [self._names_for_diff(conn, k, cur[k]), self._names_for_diff(conn, k, val)]
                       for k, val in v.items() if cur.get(k) != val}
            if changes:
                v.update({"version": cur["version"] + 1, "updated_at": self.ts(), "updated_by": user["id"]})
                conn.execute(f"UPDATE trips SET {','.join(k + '=?' for k in v)} WHERE id=?", [*v.values(), trip_id])
                self.audit(conn, user, "trip", trip_id, "update", changes)
                if merged["stage"] >= L.LOADED:
                    self.sync_leads(conn)
        return self.get_trip(user, trip_id)

    def advance_trip(self, user, trip_id: int, version) -> dict:
        need(user, "trips_edit")
        with self.db.tx() as conn:
            t = self._load_trip_row(conn, trip_id, version)
            st = t["stage"]
            if st == L.REFUSED or st >= L.CLOSED:
                raise AppError(400, "Рейс уже закрыт" if st == L.CLOSED else "Рейс в отказе: сначала верните этап")
            nxt = st + 1
            missing = L.missing_fields(t, nxt)
            if missing:
                raise AppError(400, f"Для этапа «{L.TRIP_STAGES[nxt]}» заполните: " + ", ".join(missing),
                               "missing", {"missing": missing})
            self._bump(conn, user, trip_id, {"stage": nxt}, t["version"])
            self._add_trip_history(conn, trip_id, nxt, user)
            self.audit(conn, user, "trip", trip_id, "stage", {"stage": [L.TRIP_STAGES[st], L.TRIP_STAGES[nxt]]})
            if nxt >= L.LOADED:
                self.sync_leads(conn)
        return self.get_trip(user, trip_id)

    def _bump(self, conn, user, trip_id, values: dict, version: int):
        values = {**values, "version": version + 1, "updated_at": self.ts(), "updated_by": user["id"]}
        conn.execute(f"UPDATE trips SET {','.join(k + '=?' for k in values)} WHERE id=?", [*values.values(), trip_id])

    def rollback_trip(self, user, trip_id: int, version) -> dict:
        need(user, "trips_edit")
        with self.db.tx() as conn:
            t = self._load_trip_row(conn, trip_id, version)
            st = t["stage"]
            if st == 1:
                raise AppError(400, "Это первый этап, возвращать некуда")
            last = one(conn.execute("SELECT id, at FROM trip_stage_history WHERE trip_id=? AND stage=? "
                                    "ORDER BY id DESC LIMIT 1", (trip_id, st)))
            if last:
                conn.execute("DELETE FROM trip_stage_history WHERE id=?", (last["id"],))
            prev = one(conn.execute("SELECT MAX(stage) s FROM trip_stage_history WHERE trip_id=? AND stage<>0",
                                    (trip_id,)))["s"] if st == L.REFUSED else st - 1
            prev = prev or 1
            values = {"stage": prev}
            if st == L.REFUSED:
                values["refusal_reason"] = None
            self._bump(conn, user, trip_id, values, t["version"])
            self.audit(conn, user, "trip", trip_id, "rollback",
                       {"stage": [L.TRIP_STAGES[st], L.TRIP_STAGES[prev]],
                        "stage_time_removed": [last["at"] if last else None, None]})
        return self.get_trip(user, trip_id)

    def refuse_trip(self, user, trip_id: int, version, reason, comment=None) -> dict:
        need(user, "trips_edit")
        if reason not in L.TRIP_REFUSAL_REASONS:
            raise AppError(400, "Выберите причину отказа")
        with self.db.tx() as conn:
            t = self._load_trip_row(conn, trip_id, version)
            if t["stage"] not in (1, 2, 3):
                raise AppError(400, "Отказ клиента возможен только на этапах 1–3")
            values = {"stage": L.REFUSED, "refusal_reason": reason}
            if _text(comment):
                values["comment"] = ((t["comment"] + "\n") if t["comment"] else "") + _text(comment)
            self._bump(conn, user, trip_id, values, t["version"])
            self._add_trip_history(conn, trip_id, L.REFUSED, user)
            self.audit(conn, user, "trip", trip_id, "refuse",
                       {"stage": [L.TRIP_STAGES[t["stage"]], L.TRIP_STAGES[0]], "refusal_reason": [None, reason]})
        return self.get_trip(user, trip_id)

    def contact_trip(self, user, trip_id: int, location=None) -> dict:
        """Кнопка «На связи»: время связи ставит сервер, местонахождение по желанию."""
        need(user, "trips_edit")
        with self.db.tx() as conn:
            t = self._load_trip_row(conn, trip_id)
            values = {"last_contact_at": self.ts()}
            changes = {"last_contact_at": [t["last_contact_at"], values["last_contact_at"]]}
            loc = _text(location)
            if loc:
                values["driver_location"] = loc
                changes["driver_location"] = [t["driver_location"], loc]
            self._bump(conn, user, trip_id, values, t["version"])
            self.audit(conn, user, "trip", trip_id, "contact", changes)
        return self.get_trip(user, trip_id)

    def delete_trip(self, user, trip_id: int):
        need(user, "delete")
        with self.db.tx() as conn:
            t = self._load_trip_row(conn, trip_id)
            self._bump(conn, user, trip_id, {"deleted": 1}, t["version"])
            self.audit(conn, user, "trip", trip_id, "delete")

    # ------------------------------------------------------------ лиды

    def load_leads(self, conn, include_deleted=False) -> list[dict]:
        leads = rows(conn.execute(
            "SELECT l.*, u.name owner_name FROM leads l LEFT JOIN users u ON u.id=l.owner_id"
            + ("" if include_deleted else " WHERE l.deleted=0")))
        times: dict[int, dict] = {}
        for h in conn.execute("SELECT lead_id, stage, at FROM lead_stage_history ORDER BY id"):
            times.setdefault(h["lead_id"], {})[h["stage"]] = h["at"]
        for l in leads:
            l["times"] = times.get(l["id"], {})
        return leads

    def _enrich_lead(self, l, s, now):
        l["stage_name"] = L.LEAD_STAGES.get(l["stage"], "")
        l["flags"] = L.lead_flags(l, s, now)
        l["days_on_stage"] = L.lead_days_on_stage(l, now)
        return l

    def list_leads(self, user, month=None, owner_id=None) -> dict:
        with self.db.lock:
            conn = self.db.conn
            s = self.settings(conn)
            leads = self.load_leads(conn)
        now = self.now()
        if not auth.can(user, "view_all"):
            owner_id = user["id"]
        if owner_id:
            leads = [l for l in leads if l["owner_id"] == int(owner_id)]
        for l in leads:
            self._enrich_lead(l, s, now)
        # на доске: все открытые лиды + закрытые (первый рейс / отказ) за выбранный месяц
        board = [l for l in leads if l["stage"] in (1, 2, 3) or month is None
                 or str(l["times"].get(l["stage"]) or l["updated_at"] or "")[:7] == month]
        board.sort(key=lambda l: (not any(f["level"] == "red" for f in l["flags"]),
                                  not l["flags"], -(l["days_on_stage"] or 0)))
        return {"leads": board, "funnel": L.lead_funnel(leads, month), "month": month}

    def get_lead(self, user, lead_id: int) -> dict:
        with self.db.lock:
            conn = self.db.conn
            s = self.settings(conn)
            lead = next((l for l in self.load_leads(conn, auth.can(user, "admin")) if l["id"] == lead_id), None)
            if not lead:
                raise AppError(404, "Лид не найден")
            if not auth.can(user, "view_all") and lead["owner_id"] != user["id"]:
                forbid("Это лид другого менеджера")
            lead["history"] = rows(conn.execute(
                "SELECT h.stage, h.at, u.name user_name FROM lead_stage_history h LEFT JOIN users u ON u.id=h.user_id "
                "WHERE h.lead_id=? ORDER BY h.id", (lead_id,)))
            for h in lead["history"]:
                h["stage_name"] = L.LEAD_STAGES.get(h["stage"], "")
        lead["log"] = self.audit_list(user, "lead", lead_id, 100)
        return self._enrich_lead(lead, s, self.now())

    def lead_duplicates(self, user, company, exclude_id=None) -> list[str]:
        key = L.norm_key(company)
        if not key:
            return []
        warnings = []
        with self.db.lock:
            conn = self.db.conn
            c = one(conn.execute("SELECT name, type FROM clients WHERE name_key=? AND deleted=0", (key,)))
            if c:
                owners = rows(conn.execute(
                    "SELECT DISTINCT u.name FROM trips t JOIN users u ON u.id=t.employee_id JOIN clients c "
                    "ON c.id=t.client_id WHERE c.name_key=? AND t.deleted=0", (key,)))
                who = ", ".join(o["name"] for o in owners) or "—"
                warnings.append(f"«{c['name']}» уже есть в справочнике клиентов ({c['type']}), ведёт: {who}")
            for l in rows(conn.execute(
                    "SELECT l.company, u.name owner FROM leads l LEFT JOIN users u ON u.id=l.owner_id "
                    "WHERE l.company_key=? AND l.deleted=0 AND l.stage IN (1,2,3) AND l.owner_id<>? AND l.id<>?",
                    (key, user["id"], exclude_id or 0))):
                warnings.append(f"«{l['company']}» уже в работе у менеджера {l['owner']}")
        return warnings

    def save_lead(self, user, data: dict, lead_id: int | None = None) -> dict:
        need(user, "leads_edit")
        company = L.clean_name(data.get("company"))
        if not company:
            raise AppError(400, "Укажите компанию")
        v = {f: _text(data.get(f), 2000 if f == "comment" else 500) for f in LEAD_TEXT if f in data}
        if v.get("source") and v["source"] not in L.LEAD_SOURCES:
            raise AppError(400, "Неизвестный источник")
        if "next_step_date" in data:
            v["next_step_date"] = _date_str(data.get("next_step_date"), "Дата следующего шага")
        v["company"] = company
        v["company_key"] = L.norm_key(company)
        with self.db.tx() as conn:
            cur = None
            if lead_id is not None:
                if "version" not in data:
                    raise AppError(400, "Не передана версия лида")
                cur = one(conn.execute("SELECT * FROM leads WHERE id=? AND deleted=0", (lead_id,)))
                if not cur:
                    raise AppError(404, "Лид не найден")
                if int(data["version"]) != cur["version"]:
                    raise AppError(409, "Лид изменён другим сотрудником, обновите", "conflict")
                if cur["owner_id"] != user["id"] and not auth.can(user, "reassign"):
                    forbid("Это лид другого менеджера")
            owner = data.get("owner_id")
            if owner not in (None, ""):
                owner = int(owner)
                base = cur["owner_id"] if cur else user["id"]
                if owner != base and not auth.can(user, "reassign"):
                    forbid("Переназначать лиды может только руководитель отдела")
                v["owner_id"] = owner
            elif cur is None:
                v["owner_id"] = user["id"]
            if cur is None or v["company_key"] != cur["company_key"]:
                warn = self.lead_duplicates(user, company, lead_id)
                if warn and not data.get("confirm"):
                    raise AppError(409, "\n".join(warn), "duplicate", {"warnings": warn})
            now = self.ts()
            if cur is None:
                v.update({"stage": 1, "created_at": now, "created_by": user["id"], "updated_at": now, "version": 1})
                c = conn.execute(f"INSERT INTO leads({','.join(v)}) VALUES({','.join('?' * len(v))})",
                                 list(v.values()))
                lead_id = c.lastrowid
                conn.execute("INSERT INTO lead_stage_history(lead_id, stage, at, user_id) VALUES(?,?,?,?)",
                             (lead_id, 1, now, user["id"]))
                self.audit(conn, user, "lead", lead_id, "create",
                           {k: [None, val] for k, val in v.items() if val is not None and k != "company_key"})
            else:
                changes = {k: [cur[k], val] for k, val in v.items() if cur.get(k) != val and k != "company_key"}
                if changes:
                    v.update({"version": cur["version"] + 1, "updated_at": now})
                    conn.execute(f"UPDATE leads SET {','.join(k + '=?' for k in v)} WHERE id=?",
                                 [*v.values(), lead_id])
                    self.audit(conn, user, "lead", lead_id, "update", changes)
            self.sync_leads(conn)
        return self.get_lead(user, lead_id)

    def _lead_row_for_change(self, conn, user, lead_id, version):
        cur = one(conn.execute("SELECT * FROM leads WHERE id=? AND deleted=0", (lead_id,)))
        if not cur:
            raise AppError(404, "Лид не найден")
        if version is not None and int(version) != cur["version"]:
            raise AppError(409, "Лид изменён другим сотрудником, обновите", "conflict")
        if cur["owner_id"] != user["id"] and not auth.can(user, "reassign"):
            forbid("Это лид другого менеджера")
        return cur

    def _set_lead_stage(self, conn, user, lead, stage, extra=None, history=True):
        values = {"stage": stage, "version": lead["version"] + 1, "updated_at": self.ts(), **(extra or {})}
        conn.execute(f"UPDATE leads SET {','.join(k + '=?' for k in values)} WHERE id=?", [*values.values(), lead["id"]])
        if history:
            conn.execute("INSERT INTO lead_stage_history(lead_id, stage, at, user_id) VALUES(?,?,?,?)",
                         (lead["id"], stage, self.ts(), user["id"] if user else None))

    def advance_lead(self, user, lead_id, version) -> dict:
        need(user, "leads_edit")
        with self.db.tx() as conn:
            cur = self._lead_row_for_change(conn, user, lead_id, version)
            if cur["stage"] not in (1, 2):
                raise AppError(400, "«Первый рейс» ставится автоматически, когда по компании появится загруженный рейс"
                               if cur["stage"] == 3 else "Лид уже закрыт")
            nxt = cur["stage"] + 1
            self._set_lead_stage(conn, user, cur, nxt)
            self.audit(conn, user, "lead", lead_id, "stage", {"stage": [L.LEAD_STAGES[cur["stage"]], L.LEAD_STAGES[nxt]]})
        return self.get_lead(user, lead_id)

    def refuse_lead(self, user, lead_id, version, reason, comment=None) -> dict:
        need(user, "leads_edit")
        if reason not in L.LEAD_REFUSAL_REASONS:
            raise AppError(400, "Выберите причину отказа")
        with self.db.tx() as conn:
            cur = self._lead_row_for_change(conn, user, lead_id, version)
            if cur["stage"] not in (1, 2, 3):
                raise AppError(400, "Отказ возможен только на этапах 1–3")
            extra = {"refusal_reason": reason}
            if _text(comment):
                extra["comment"] = ((cur["comment"] + "\n") if cur["comment"] else "") + _text(comment)
            self._set_lead_stage(conn, user, cur, 0, extra)
            self.audit(conn, user, "lead", lead_id, "refuse", {"stage": [L.LEAD_STAGES[cur["stage"]], "Отказ"],
                                                               "refusal_reason": [None, reason]})
        return self.get_lead(user, lead_id)

    def rollback_lead(self, user, lead_id, version) -> dict:
        need(user, "leads_edit")
        with self.db.tx() as conn:
            cur = self._lead_row_for_change(conn, user, lead_id, version)
            st = cur["stage"]
            if st in (1, L.LEAD_FIRST_TRIP):
                raise AppError(400, "Этот этап нельзя вернуть")
            last = one(conn.execute("SELECT id FROM lead_stage_history WHERE lead_id=? AND stage=? ORDER BY id DESC "
                                    "LIMIT 1", (lead_id, st)))
            if last:
                conn.execute("DELETE FROM lead_stage_history WHERE id=?", (last["id"],))
            prev = (one(conn.execute("SELECT MAX(stage) s FROM lead_stage_history WHERE lead_id=? AND stage<>0",
                                     (lead_id,)))["s"] or 1) if st == 0 else st - 1
            self._set_lead_stage(conn, user, cur, prev, {"refusal_reason": None} if st == 0 else None, history=False)
            self.audit(conn, user, "lead", lead_id, "rollback",
                       {"stage": [L.LEAD_STAGES[st], L.LEAD_STAGES[prev]]})
        return self.get_lead(user, lead_id)

    def delete_lead(self, user, lead_id):
        need(user, "delete")
        with self.db.tx() as conn:
            conn.execute("UPDATE leads SET deleted=1, version=version+1 WHERE id=?", (lead_id,))
            self.audit(conn, user, "lead", lead_id, "delete")

    def sync_leads(self, conn):
        """Лид → «Первый рейс», когда по компании появился рейс на этапе «Загружен» и дальше
        (после создания лида). Компания добавляется в справочник клиентов как «Разовый»."""
        open_leads = rows(conn.execute("SELECT * FROM leads WHERE deleted=0 AND stage IN (1,2,3)"))
        if not open_leads:
            return
        loaded = {}
        for r in conn.execute(
                "SELECT c.name_key key, MIN(h.at) at FROM trips t JOIN clients c ON c.id=t.client_id "
                "JOIN trip_stage_history h ON h.trip_id=t.id AND h.stage=6 AND h.imported=0 "
                "WHERE t.deleted=0 AND t.stage>=6 GROUP BY c.name_key, t.id"):
            loaded.setdefault(r["key"], []).append(r["at"])
        for lead in open_leads:
            ats = [a for a in loaded.get(lead["company_key"], []) if a >= (lead["created_at"] or "")]
            if not ats:
                continue
            cid = self.client_id(conn, lead["company"], None, "Разовый")
            self._set_lead_stage(conn, None, lead, L.LEAD_FIRST_TRIP, {"client_id": cid})
            self.audit(conn, None, "lead", lead["id"], "stage",
                       {"stage": [L.LEAD_STAGES[lead["stage"]], L.LEAD_STAGES[L.LEAD_FIRST_TRIP]]})

    # ------------------------------------------------------------ дневные отчёты

    def report_reminder(self, user) -> bool:
        if user["role"] != "manager":
            return False
        now = self.now()
        with self.db.lock:
            conn = self.db.conn
            s = self.settings(conn)
            cal = self.calendar(conn, s)
            if not cal.is_workday(now.date()) or now.time() < L._hm(s["report_reminder"]):
                return False
            r = one(conn.execute("SELECT id FROM daily_reports WHERE user_id=? AND date=? AND deleted=0",
                                 (user["id"], now.date().isoformat())))
        return r is None

    def report_hints(self, conn, user_id, d: str) -> dict:
        like = d + "%"
        return {
            "leads_created": one(conn.execute(
                "SELECT COUNT(*) n FROM leads WHERE created_by=? AND created_at LIKE ? AND deleted=0",
                (user_id, like)))["n"],
            "leads_kp": one(conn.execute(
                "SELECT COUNT(*) n FROM lead_stage_history WHERE user_id=? AND stage=2 AND at LIKE ?",
                (user_id, like)))["n"],
            "trip_stages": one(conn.execute(
                "SELECT COUNT(*) n FROM trip_stage_history WHERE user_id=? AND at LIKE ? AND imported=0",
                (user_id, like)))["n"],
            "trips_created": one(conn.execute(
                "SELECT COUNT(*) n FROM trips WHERE created_by=? AND created_at LIKE ? AND deleted=0 AND imported=0",
                (user_id, like)))["n"],
        }

    def get_report(self, user, d=None, user_id=None) -> dict:
        d = _date_str(d, "Дата") or self.now().date().isoformat()
        uid = int(user_id) if user_id else user["id"]
        if uid != user["id"] and not auth.can(user, "reports_all"):
            forbid("Чужие отчёты видит только руководитель")
        with self.db.lock:
            conn = self.db.conn
            r = one(conn.execute("SELECT * FROM daily_reports WHERE user_id=? AND date=? AND deleted=0", (uid, d)))
            hints = self.report_hints(conn, uid, d)
        return {"date": d, "user_id": uid, "report": r, "hints": hints,
                "can_edit": self._can_edit_report(user, uid, d, raise_=False)}

    def _can_edit_report(self, user, uid, d, raise_=True) -> bool:
        today = self.now().date()
        dd = date.fromisoformat(d)
        msg = None
        if dd > today:
            msg = "Нельзя сдать отчёт за будущую дату"
        elif uid != user["id"]:
            if not auth.can(user, "reports_edit_all"):
                msg = "Изменять чужой дневной отчёт может только руководитель отдела"
        elif not auth.can(user, "reports_own"):
            msg = "Дневной отчёт сдают менеджеры"
        elif not auth.can(user, "reports_edit_all") and (today - dd).days > 3:
            msg = "Исправить можно отчёт не старше 3 дней. Обратитесь к руководителю"
        if msg and raise_:
            forbid(msg)
        return msg is None

    def save_report(self, user, data: dict) -> dict:
        d = _date_str(data.get("date"), "Дата") or self.now().date().isoformat()
        uid = int(data.get("user_id") or user["id"])
        self._can_edit_report(user, uid, d)
        notes = _text(data.get("notes"), 10_000)
        if notes and len(notes) > 500:
            raise AppError(400, "«Чем ещё занимался» — не больше 500 символов")
        vals = {"calls": _int(data.get("calls"), "Звонки"), "proposals": _int(data.get("proposals"), "КП"),
                "contacts": _int(data.get("contacts"), "Новые контакты"),
                "meetings": _int(data.get("meetings"), "Встречи"), "notes": notes}
        now = self.ts()
        with self.db.tx() as conn:
            if not one(conn.execute("SELECT id FROM users WHERE id=? AND deleted=0", (uid,))):
                raise AppError(404, "Сотрудник не найден")
            old = one(conn.execute("SELECT * FROM daily_reports WHERE user_id=? AND date=?", (uid, d)))
            if old:
                changes = {k: [old[k], v] for k, v in vals.items() if old[k] != v}
                conn.execute("UPDATE daily_reports SET calls=?, proposals=?, contacts=?, meetings=?, notes=?, "
                             "deleted=0, updated_at=?, updated_by=? WHERE id=?",
                             (*vals.values(), now, user["id"], old["id"]))
                rid = old["id"]
                if changes or old["deleted"]:
                    self.audit(conn, user, "daily_report", rid, "update", changes)
            else:
                c = conn.execute("INSERT INTO daily_reports(user_id, date, calls, proposals, contacts, meetings, notes, "
                                 "created_at, updated_at, updated_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                 (uid, d, *vals.values(), now, now, user["id"]))
                rid = c.lastrowid
                self.audit(conn, user, "daily_report", rid, "create", {k: [None, v] for k, v in vals.items()})
        return self.get_report(user, d, uid)

    def report_grid(self, user) -> dict:
        now = self.now()
        with self.db.lock:
            conn = self.db.conn
            s = self.settings(conn)
            cal = self.calendar(conn, s)
            days = cal.last_workdays(now.date(), 10, include_today=True)
            users = rows(conn.execute("SELECT id, name FROM users WHERE role='manager' AND active=1 AND deleted=0 "
                                      "ORDER BY name"))
            if not auth.can(user, "reports_all"):
                users = [{"id": user["id"], "name": user["name"]}]
            ids = [u["id"] for u in users] or [0]
            reps = rows(conn.execute(
                f"SELECT r.*, u.name user_name FROM daily_reports r JOIN users u ON u.id=r.user_id "
                f"WHERE r.deleted=0 AND r.user_id IN ({','.join('?' * len(ids))}) AND r.date>=? ORDER BY r.date DESC",
                (*ids, (now.date() - timedelta(days=60)).isoformat())))
        by = {(r["user_id"], r["date"]): r for r in reps}
        grid = []
        for u in users:
            cells = []
            for d in days:
                r = by.get((u["id"], d.isoformat()))
                cells.append({"date": d.isoformat(), "calls": r["calls"] if r else None,
                              "kp": r["proposals"] if r else None, "color": L.report_color(r, s)})
            grid.append({"user_id": u["id"], "name": u["name"], "cells": cells})
        feed = [{"date": r["date"], "user_name": r["user_name"], "notes": r["notes"], "calls": r["calls"],
                 "kp": r["proposals"]} for r in reps if r["notes"]][:60]
        return {"days": [d.isoformat() for d in days], "grid": grid, "feed": feed,
                "norm_calls": s["norm_calls"], "norm_kp": s["norm_kp"]}

    # ------------------------------------------------------------ сводка

    def summary(self, user, month=None, employee_id=None) -> dict:
        month = month or self.now().strftime("%Y-%m")
        if not auth.can(user, "view_all"):
            employee_id = user["id"]
        employee_id = int(employee_id) if employee_id else None
        with self.db.lock:
            conn = self.db.conn
            trips, s, cal, now = self._enriched_trips(conn)
            leads = self.load_leads(conn)
            reports = rows(conn.execute("SELECT * FROM daily_reports WHERE deleted=0"))
            users = rows(conn.execute("SELECT * FROM users"))
        res = L.compute_summary(trips, leads, reports, users, s, cal, month, now, employee_id)
        res["min_margin_pct"] = s["min_margin_pct"]
        res["dependency_pct"] = s["client_dependency_pct"]
        if auth.can(user, "reassign") or auth.can(user, "view_all"):
            res["propose_permanent"] = [c for c in self.list_clients(user) if c["propose_permanent"]]
        return res

    # ------------------------------------------------------------ удалённые записи

    def deleted_items(self, user) -> dict:
        need(user, "admin")
        with self.db.lock:
            conn = self.db.conn
            return {
                "trips": rows(conn.execute(
                    "SELECT t.id, t.ship_date, t.origin, t.destination, c.name client, t.updated_at FROM trips t "
                    "LEFT JOIN clients c ON c.id=t.client_id WHERE t.deleted=1 ORDER BY t.updated_at DESC")),
                "leads": rows(conn.execute("SELECT id, company, updated_at FROM leads WHERE deleted=1")),
                "clients": rows(conn.execute("SELECT id, name FROM clients WHERE deleted=1")),
                "carriers": rows(conn.execute("SELECT id, name FROM carriers WHERE deleted=1")),
                "users": rows(conn.execute("SELECT id, name, role FROM users WHERE deleted=1")),
                "daily_reports": rows(conn.execute(
                    "SELECT r.id, r.date, u.name user_name FROM daily_reports r JOIN users u ON u.id=r.user_id "
                    "WHERE r.deleted=1")),
            }

    def delete_item(self, user, entity: str, item_id: int):
        table = {"client": "clients", "carrier": "carriers", "daily_report": "daily_reports"}.get(entity)
        if not table:
            raise AppError(400, "Неизвестный тип записи")
        need(user, "admin" if entity != "daily_report" else "delete")
        with self.db.tx() as conn:
            conn.execute(f"UPDATE {table} SET deleted=1 WHERE id=?", (item_id,))
            self.audit(conn, user, entity, item_id, "delete")

    def restore(self, user, entity: str, item_id: int):
        need(user, "admin")
        table = {"trip": "trips", "lead": "leads", "client": "clients", "carrier": "carriers",
                 "user": "users", "daily_report": "daily_reports"}.get(entity)
        if not table:
            raise AppError(400, "Неизвестный тип записи")
        with self.db.tx() as conn:
            extra = ", active=1" if table == "users" else ""
            extra += ", version=version+1" if table in ("trips", "leads") else ""
            conn.execute(f"UPDATE {table} SET deleted=0{extra} WHERE id=?", (item_id,))
            self.audit(conn, user, entity, item_id, "restore")
