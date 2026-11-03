"""Логика расчётов (разделы 4–8 ТЗ): этапы, сроки регламента, метки, сводка.

Модуль не обращается к базе данных: на вход получает списки словарей,
поэтому его легко проверять автотестами (см. tests/test_logic.py).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

ALMATY = timezone(timedelta(hours=5))  # Алматы, UTC+5
DT_FMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------- справочные данные

TRIP_STAGES = {
    1: "Заявка получена",
    2: "Ставка отправлена клиенту",
    3: "Подтверждена клиентом",
    4: "Перевозчик назначен",
    5: "Договор-заявка подписан",
    6: "Загружен",
    7: "В пути",
    8: "Доставлен",
    9: "Документы собраны",
    10: "Закрыт",
    0: "Отказ клиента",
}
REFUSED = 0
LOADED, IN_TRANSIT, DELIVERED, DOCS, CLOSED = 6, 7, 8, 9, 10

TRIP_REFUSAL_REASONS = ["Дорогая ставка", "Нет машины", "Клиент передумал", "Другое"]
BODY_TYPES = ["Тент", "Реф", "Борт", "Другое"]
CLIENT_TYPES = ["Постоянный", "Разовый"]

BOARD_COLUMNS = [
    ("request", "Заявка", (1, 2, 3)),
    ("prep", "Подготовка", (4, 5)),
    ("transit", "Погрузка и путь", (6, 7)),
    ("delivered", "Доставлен, документы", (8, 9)),
    ("closed", "Закрыто в месяце", (10,)),
]

LEAD_STAGES = {1: "Первый контакт", 2: "КП отправлено", 3: "Ставка согласована", 4: "Первый рейс", 0: "Отказ"}
LEAD_FIRST_TRIP = 4
LEAD_SOURCES = ["Холодный звонок", "Биржа грузов", "Клиент ЖД-отдела", "Рекомендация",
                "Сайт / входящий", "Тендер", "Другое"]
LEAD_REFUSAL_REASONS = ["Дорогая ставка", "Есть свой перевозчик", "Нет объёмов", "Не дозвонились", "Другое"]

ROLES = {"manager": "Менеджер", "head": "Руководитель отдела", "exec": "Руководство", "admin": "Администратор"}

DEFAULT_SETTINGS = {
    "plan_trips": 40,             # план рейсов отдела на месяц
    "min_margin_pct": 10,         # минимальная маржа, %
    "overload_threshold": 6,      # активных рейсов на сотрудника
    "norm_calls": 15,             # норма звонков в день
    "norm_kp": 3,                 # норма КП в день
    "rate_hours": 2,              # срок ставки, рабочих часов
    "contact_hours": 12,          # интервал связи с водителем, часов
    "close_days": 5,              # срок закрытия после выгрузки, рабочих дней
    "lead_stuck_days": 14,        # лид «завис», дней на этапе
    "client_dependency_pct": 40,  # порог зависимости от клиента, % маржи
    "work_days": [0, 1, 2, 3, 4], # 0 = понедельник
    "work_start": "09:00",
    "work_end": "18:00",
    "report_reminder": "17:30",   # с этого времени напоминаем сдать отчёт
    "backup_dir": "backup",
}

FIELD_LABELS = {
    "ship_date": "Дата отправки",
    "truck_brand": "Марка машины",
    "plate": "Гос. номер",
    "driver": "Данные водителя",
    "origin": "Пункт отправления",
    "destination": "Пункт назначения",
    "price": "Стоимость перевозки",
    "body_type": "Тент/Реф",
    "payment_doc": "Платёжка",
    "driver_location": "Местонахождение водителя",
    "client_id": "Клиент",
    "employee_id": "Сотрудник",
    "carrier_id": "Перевозчик",
    "carrier_rate": "Ставка перевозчика",
    "ttn": "Номер ТТН",
    "pay_client": "Оплата от клиента",
    "pay_agent": "Оплата агенту",
    "postpay": "Постоплата",
    "client_type": "Тип клиента",
    "refusal_reason": "Причина отказа",
    "comment": "Комментарий",
}

BASE_REQUIRED = ["origin", "destination", "body_type", "client_id", "employee_id", "client_type"]
STAGE_REQUIRED = {2: ["price"], 4: ["carrier_id", "carrier_rate"], 6: ["ship_date"], 8: ["ttn"]}

OVERDUE_CODES = ("RATE_LATE", "NO_CONTACT", "CLOSE_LATE")


# ---------------------------------------------------------------- даты и строки

def now_local() -> datetime:
    """Текущее время Алматы без указания пояса (так хранится в базе)."""
    return datetime.now(ALMATY).replace(tzinfo=None, microsecond=0)


def fmt_dt(d: datetime) -> str:
    return d.strftime(DT_FMT)


def parse_dt(s) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    if isinstance(s, date):
        return datetime.combine(s, time(0))
    return datetime.fromisoformat(str(s))


def parse_date(s) -> date | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return date.fromisoformat(str(s)[:10])


def clean_name(name) -> str:
    """Убирает лишние пробелы: «  Coca   Cola » → «Coca Cola»."""
    return " ".join(str(name or "").split())


def norm_key(name) -> str:
    """Ключ для поиска дублей: без лишних пробелов и без учёта регистра."""
    return clean_name(name).casefold()


def person_key(name) -> str:
    """Ключ для имени сотрудника: «Жексебаев М.» = «жексебаев м»."""
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


def month_shift(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    idx = y * 12 + (m - 1) + k
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
               "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def month_title(month: str) -> str:
    return f"{MONTH_NAMES[int(month[5:7]) - 1]} {month[:4]}"


def is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# ---------------------------------------------------------------- рабочее время

def _hm(s: str) -> time:
    h, m = str(s).split(":")
    return time(int(h), int(m))


class WorkCalendar:
    """Рабочие дни и часы отдела (по умолчанию Пн–Пт 09:00–18:00) плюс праздники."""

    def __init__(self, work_days=(0, 1, 2, 3, 4), start="09:00", end="18:00", holidays=()):
        self.work_days = set(int(d) for d in work_days)
        self.start = _hm(start)
        self.end = _hm(end)
        self.holidays = {parse_date(h) for h in holidays}
        if self.end <= self.start:
            raise ValueError("Конец рабочего дня должен быть позже начала")

    @classmethod
    def from_settings(cls, settings: dict, holidays=()):
        return cls(settings["work_days"], settings["work_start"], settings["work_end"], holidays)

    def is_workday(self, d: date) -> bool:
        return d.weekday() in self.work_days and d not in self.holidays

    def add_work_hours(self, start: datetime, hours: float) -> datetime:
        """Момент, когда от start пройдёт hours рабочих часов.
        Пример: заявка в 17:30 + 2 ч → 10:30 следующего рабочего дня."""
        remaining = timedelta(hours=hours)
        cur = start
        for _ in range(3700):
            d = cur.date()
            day_start = datetime.combine(d, self.start)
            day_end = datetime.combine(d, self.end)
            if not self.is_workday(d) or cur >= day_end:
                cur = datetime.combine(d + timedelta(days=1), self.start)
                continue
            if cur < day_start:
                cur = day_start
            avail = day_end - cur
            if remaining <= avail:
                return cur + remaining
            remaining -= avail
            cur = datetime.combine(d + timedelta(days=1), self.start)
        raise ValueError("Не найдено рабочее время: проверьте настройки рабочих дней")

    def work_hours_between(self, a: datetime, b: datetime) -> float:
        """Сколько рабочих часов прошло между a и b."""
        if b <= a:
            return 0.0
        total = timedelta()
        d = a.date()
        while d <= b.date():
            if self.is_workday(d):
                s = max(a, datetime.combine(d, self.start))
                e = min(b, datetime.combine(d, self.end))
                if e > s:
                    total += e - s
            d += timedelta(days=1)
        return total.total_seconds() / 3600

    def add_work_days(self, start: datetime, n: int) -> datetime:
        """То же время суток через n рабочих дней (дни после даты start)."""
        d = start.date()
        count = 0
        for _ in range(3700):
            if count >= n:
                break
            d += timedelta(days=1)
            if self.is_workday(d):
                count += 1
        return datetime.combine(d, start.time())

    def last_workdays(self, today: date, n: int, include_today: bool) -> list[date]:
        """n последних рабочих дней (по возрастанию)."""
        res = []
        d = today if include_today else today - timedelta(days=1)
        for _ in range(3700):
            if len(res) >= n:
                break
            if self.is_workday(d):
                res.append(d)
            d -= timedelta(days=1)
        return sorted(res)


# ---------------------------------------------------------------- рейсы

def required_fields(stage: int) -> list[str]:
    fields = list(BASE_REQUIRED)
    for s, extra in STAGE_REQUIRED.items():
        if stage >= s:
            fields += extra
    if stage == REFUSED:
        fields.append("refusal_reason")
    return fields


def missing_fields(trip: dict, stage: int) -> list[str]:
    """Названия незаполненных полей, обязательных для этапа stage."""
    return [FIELD_LABELS[f] for f in required_fields(stage) if is_empty(trip.get(f))]


def status_1c(stage: int) -> str:
    if stage in (LOADED, IN_TRANSIT):
        return "В пути"
    if stage in (DELIVERED, DOCS, CLOSED):
        return "Доставлен"
    return ""


def trip_margin(t: dict):
    if t.get("price") is None or t.get("carrier_rate") is None:
        return None
    return t["price"] - t["carrier_rate"]


def trip_margin_pct(t: dict):
    m = trip_margin(t)
    if m is None or not t.get("price"):
        return None
    return m / t["price"] * 100


def trip_month(t: dict) -> str:
    """Месяц рейса: по дате отправки, а если её нет — по дате заявки."""
    src = t.get("ship_date") or t.get("received_at") or t.get("created_at") or ""
    return str(src)[:7]


def board_column(stage: int) -> str | None:
    for key, _title, stages in BOARD_COLUMNS:
        if stage in stages:
            return key
    return None


def _times(t: dict) -> dict:
    return {int(k): v for k, v in (t.get("times") or {}).items()}


def trip_flags(t: dict, s: dict, cal: WorkCalendar, now: datetime) -> list[dict]:
    """Метки контроля сроков регламента (п. 4.3)."""
    flags = []
    if t.get("deleted"):
        return flags
    st = t["stage"]
    times = _times(t)
    if st == 1:
        rec = parse_dt(times.get(1) or t.get("received_at"))
        if rec and now > cal.add_work_hours(rec, s["rate_hours"]):
            flags.append({"code": "RATE_LATE", "level": "red",
                          "text": f"Ставка не отправлена > {s['rate_hours']} ч"})
    if st in (LOADED, IN_TRANSIT):
        cands = [parse_dt(x) for x in (t.get("last_contact_at"), times.get(LOADED), times.get(IN_TRANSIT)) if x]
        base = max(cands) if cands else None
        if base and now - base > timedelta(hours=s["contact_hours"]):
            flags.append({"code": "NO_CONTACT", "level": "red",
                          "text": f"Нет связи с водителем > {s['contact_hours']} ч"})
    if st in (DELIVERED, DOCS):
        d = parse_dt(times.get(DELIVERED))
        if d and now > cal.add_work_days(d, s["close_days"]):
            flags.append({"code": "CLOSE_LATE", "level": "red",
                          "text": f"Не закрыт > {s['close_days']} раб. дней"})
    mp = trip_margin_pct(t)
    if st != REFUSED and mp is not None and mp < s["min_margin_pct"]:
        flags.append({"code": "LOW_MARGIN", "level": "orange",
                      "text": f"Маржа {mp:.1f}% < мин. {s['min_margin_pct']}%".replace(".", ",")})
    if st >= 4 and not t.get("carrier_id"):
        flags.append({"code": "NO_CARRIER", "level": "orange", "text": "Не указан перевозчик"})
    return flags


def is_overdue(flags: list[dict]) -> bool:
    return any(f["code"] in OVERDUE_CODES for f in flags)


def assign_reg_numbers(trips: list[dict]) -> dict:
    """№ в реестре месяца: сквозная нумерация по дате отправки (отказы не нумеруются)."""
    by_month = defaultdict(list)
    for t in trips:
        if t.get("deleted") or t["stage"] == REFUSED:
            continue
        by_month[trip_month(t)].append(t)
    res = {}
    for items in by_month.values():
        items.sort(key=lambda t: (str(t.get("ship_date") or t.get("received_at") or "")[:10], t["id"]))
        for i, t in enumerate(items, 1):
            res[t["id"]] = i
    return res


def enrich_trip(t: dict, s: dict, cal: WorkCalendar, now: datetime) -> dict:
    t["month"] = trip_month(t)
    t["margin"] = trip_margin(t)
    t["margin_pct"] = trip_margin_pct(t)
    t["status_1c"] = status_1c(t["stage"])
    t["stage_name"] = TRIP_STAGES.get(t["stage"], "")
    t["column"] = board_column(t["stage"])
    t["flags"] = trip_flags(t, s, cal, now)
    t["overdue"] = is_overdue(t["flags"])
    return t


def is_month_trip(t: dict, month: str) -> bool:
    return not t.get("deleted") and t["stage"] != REFUSED and trip_month(t) == month


def is_active(t: dict) -> bool:
    return not t.get("deleted") and 1 <= t["stage"] <= DOCS


# ---------------------------------------------------------------- лиды

def lead_flags(lead: dict, s: dict, now: datetime) -> list[dict]:
    flags = []
    st = lead["stage"]
    if lead.get("deleted") or st not in (1, 2, 3):
        return flags
    since = parse_dt(_times(lead).get(st) or lead.get("created_at"))
    if since and (now - since).days > s["lead_stuck_days"]:
        flags.append({"code": "STUCK", "level": "orange", "text": "Завис"})
    nd = parse_date(lead.get("next_step_date"))
    if nd and nd < now.date():
        flags.append({"code": "MISSED_STEP", "level": "red", "text": "Пропущен шаг"})
    return flags


def lead_days_on_stage(lead: dict, now: datetime) -> int | None:
    since = parse_dt(_times(lead).get(lead["stage"]) or lead.get("created_at"))
    return (now - since).days if since else None


def lead_reached(lead: dict, stage: int) -> bool:
    return stage in _times(lead)


def lead_funnel(leads: list[dict], month: str | None) -> dict:
    """Конверсия воронки для лидов, созданных в месяце (или за всё время)."""
    sel = [l for l in leads if not l.get("deleted") and (month is None or str(l.get("created_at", ""))[:7] == month)]
    counts = {k: sum(1 for l in sel if lead_reached(l, k)) for k in (1, 2, 3, 4)}
    refused = Counter(l.get("refusal_reason") or "Другое" for l in sel if l["stage"] == 0)
    conv = []
    for a, b in ((1, 2), (2, 3), (3, 4)):
        conv.append({"from": LEAD_STAGES[a], "to": LEAD_STAGES[b],
                     "pct": (counts[b] / counts[a] * 100) if counts[a] else None})
    by_source = []
    for src in sorted({l.get("source") or "Другое" for l in sel}):
        items = [l for l in sel if (l.get("source") or "Другое") == src]
        by_source.append({"source": src, "leads": len(items),
                          "kp": sum(1 for l in items if lead_reached(l, 2)),
                          "first_trip": sum(1 for l in items if lead_reached(l, 4)),
                          "refused": sum(1 for l in items if l["stage"] == 0)})
    by_source.sort(key=lambda r: -r["leads"])
    return {"total": len(sel), "counts": counts, "conversion": conv,
            "refused": sum(refused.values()), "refused_reasons": dict(refused), "by_source": by_source}


# ---------------------------------------------------------------- дневные отчёты

def report_color(report: dict | None, s: dict) -> str:
    if not report:
        return "none"
    calls, kp = report.get("calls") or 0, report.get("proposals") or 0
    if calls >= s["norm_calls"] and kp >= s["norm_kp"]:
        return "green"
    if calls >= s["norm_calls"] / 2:
        return "yellow"
    return "red"


# ---------------------------------------------------------------- сводка

def _pct(a, b):
    return (a / b * 100) if b else None


def rate_on_time(trips, month, s, cal, now):
    """«Ставка за 2 ч»: (в срок, всего с известным результатом) по заявкам месяца."""
    ok = total = 0
    for t in trips:
        if t.get("deleted") or t.get("imported"):
            continue
        times = _times(t)
        rec = parse_dt(times.get(1) or t.get("received_at"))
        if not rec or str(rec)[:7] != month:
            continue
        sent = parse_dt(times.get(2))
        if sent:
            total += 1
            ok += cal.work_hours_between(rec, sent) <= s["rate_hours"] + 1e-9
        elif t["stage"] == 1 and now > cal.add_work_hours(rec, s["rate_hours"]):
            total += 1
    return ok, total


def close_on_time(trips, month, s, cal, now):
    """«Закрытие за 5 дней»: (в срок, всего с известным результатом) по рейсам месяца."""
    ok = total = 0
    for t in trips:
        if t.get("imported") or not is_month_trip(t, month):
            continue
        times = _times(t)
        dlv = parse_dt(times.get(DELIVERED))
        if not dlv:
            continue
        deadline = cal.add_work_days(dlv, s["close_days"])
        closed = parse_dt(times.get(CLOSED))
        if closed:
            total += 1
            ok += closed <= deadline
        elif now > deadline:
            total += 1
    return ok, total


def requests_funnel(trips, month):
    """Заявки месяца: получено → ставка отправлена → подтверждено → отказ (без импортированных)."""
    sel = []
    for t in trips:
        if t.get("deleted") or t.get("imported"):
            continue
        rec = _times(t).get(1) or t.get("received_at")
        if rec and str(rec)[:7] == month:
            sel.append(t)
    refused = Counter(t.get("refusal_reason") or "Другое" for t in sel if t["stage"] == REFUSED)
    return {"received": len(sel),
            "rate_sent": sum(1 for t in sel if 2 in _times(t)),
            "confirmed": sum(1 for t in sel if 3 in _times(t)),
            "refused": sum(refused.values()),
            "refused_reasons": dict(refused)}


def _share_bars(counter: Counter, top: int = 6):
    total = sum(v for v in counter.values() if v > 0)
    items = sorted(((k, v) for k, v in counter.items()), key=lambda kv: -kv[1])
    bars = [{"name": k, "value": v, "pct": _pct(v, total)} for k, v in items[:top]]
    rest = sum(v for _k, v in items[top:])
    if rest:
        bars.append({"name": "Остальные", "value": rest, "pct": _pct(rest, total), "rest": True})
    return bars


def compute_summary(trips, leads, reports, users, s, cal, month, now, employee_id=None):
    """Сводка (раздел 8). trips/leads — обогащённые словари со списком times."""
    managers = [u for u in users if u["role"] == "manager" and u.get("active") and not u.get("deleted")]
    n_mgr = max(1, len(managers))
    sel = [t for t in trips if not t.get("deleted")]
    if employee_id:
        sel = [t for t in sel if t.get("employee_id") == employee_id]
    month_trips = [t for t in sel if is_month_trip(t, month)]

    def money(items):
        revenue = sum(t.get("price") or 0 for t in items)
        margin = sum(trip_margin(t) or 0 for t in items)
        return revenue, margin

    revenue, margin = money(month_trips)
    plan = s["plan_trips"] if not employee_id else s["plan_trips"] / n_mgr
    plan_pct = _pct(len(month_trips), plan)
    active = [t for t in sel if is_active(t)]
    overdue = [t for t in active if is_overdue(trip_flags(t, s, cal, now))]
    margin_pct = _pct(margin, revenue)

    tiles = {
        "trips": len(month_trips), "plan": plan, "plan_pct": plan_pct,
        "plan_color": None if plan_pct is None else ("red" if plan_pct < 60 else "yellow" if plan_pct < 90 else "green"),
        "revenue": revenue, "margin": margin, "margin_pct": margin_pct,
        "margin_color": "yellow" if margin_pct is not None and margin_pct < s["min_margin_pct"] else None,
        "avg_trip": (revenue / len(month_trips)) if month_trips else None,
        "active": len(active), "overdue": len(overdue),
        "requests": requests_funnel(sel, month),
    }

    # ---- таблица по сотрудникам
    today = now.date()
    window = cal.last_workdays(today, 5, include_today=False)
    win_set = {d.isoformat() for d in window}
    emp_ids = [u["id"] for u in managers]
    for t in month_trips:
        if t.get("employee_id") and t["employee_id"] not in emp_ids:
            emp_ids.append(t["employee_id"])
    if employee_id:
        emp_ids = [employee_id]
    names = {u["id"]: u["name"] for u in users}
    rows = []
    for eid in emp_ids:
        et = [t for t in trips if t.get("employee_id") == eid and not t.get("deleted")]
        mt = [t for t in et if is_month_trip(t, month)]
        rev, mar = money(mt)
        act = [t for t in et if is_active(t)]
        load_pct = _pct(len(act), s["overload_threshold"])
        r_ok, r_total = rate_on_time(et, month, s, cal, now)
        c_ok, c_total = close_on_time(et, month, s, cal, now)
        reps = [r for r in reports if r["user_id"] == eid and r["date"] in win_set and not r.get("deleted")]
        calls = sum(r.get("calls") or 0 for r in reps)
        kp = sum(r.get("proposals") or 0 for r in reps)
        calls_pct = _pct(calls, s["norm_calls"] * len(window))
        kp_pct = _pct(kp, s["norm_kp"] * len(window))
        el = [l for l in leads if l.get("owner_id") == eid and not l.get("deleted")]
        in_work = [l for l in el if l["stage"] in (1, 2, 3)]
        stuck = [l for l in in_work if any(f["code"] == "STUCK" for f in lead_flags(l, s, now))]
        new_clients = [l for l in el if str(_times(l).get(LEAD_FIRST_TRIP) or "")[:7] == month]
        mpct = _pct(mar, rev)
        rate_pct = _pct(r_ok, r_total)
        close_pct = _pct(c_ok, c_total)

        def ok_color(p):
            return None if p is None else ("red" if p < 70 else "yellow" if p < 90 else None)

        rows.append({
            "employee_id": eid, "name": names.get(eid, "?"),
            "trips": len(mt), "clients": len({t.get("client_id") for t in mt if t.get("client_id")}),
            "revenue": rev, "margin": mar, "margin_pct": mpct,
            "margin_color": "red" if mpct is not None and mpct < s["min_margin_pct"] else None,
            "avg_trip": (rev / len(mt)) if mt else None,
            "active": len(act), "load_pct": load_pct,
            "load_label": None if load_pct is None else ("перегрузка" if load_pct > 100 else "недогружен" if load_pct < 35 else ""),
            "rate_ok": r_ok, "rate_total": r_total, "rate_pct": rate_pct, "rate_color": ok_color(rate_pct),
            "close_ok": c_ok, "close_total": c_total, "close_pct": close_pct, "close_color": ok_color(close_pct),
            "overdue": sum(1 for t in act if is_overdue(trip_flags(t, s, cal, now))),
            "calls": calls, "kp": kp, "calls_pct": calls_pct, "kp_pct": kp_pct,
            "calls_color": "red" if (calls_pct or 0) < 50 or (kp_pct or 0) < 50 else None,
            "reports": len(reps), "reports_of": len(window),
            "leads_in_work": len(in_work), "leads_stuck": len(stuck), "new_clients": len(new_clients),
        })

    # ---- зависимость от клиентов и перевозчиков
    client_margin = Counter()
    carrier_count = Counter()
    for t in month_trips:
        client_margin[t.get("client_name") or "—"] += trip_margin(t) or 0
        if t.get("carrier_name"):
            carrier_count[t["carrier_name"]] += 1
    clients_bars = _share_bars(client_margin)
    for b in clients_bars:
        b["high"] = not b.get("rest") and (b["pct"] or 0) > s["client_dependency_pct"]
    carriers_bars = _share_bars(carrier_count)

    # ---- динамика за 12 месяцев
    dynamics = []
    for k in range(-11, 1):
        m = month_shift(month, k)
        mt = [t for t in sel if is_month_trip(t, m)]
        rev, mar = money(mt)
        dynamics.append({"month": m, "title": month_title(m), "trips": len(mt), "revenue": rev, "margin": mar})

    return {"month": month, "month_title": month_title(month), "employee_id": employee_id,
            "tiles": tiles, "employees": rows, "clients": clients_bars, "carriers": carriers_bars,
            "dynamics": dynamics, "plan": plan, "report_window": [d.isoformat() for d in window]}
