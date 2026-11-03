import base64
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import backup
from app.excel_io import EXPORT_HEADERS
from app.main import create_app
from tests.sample_registry import make_sample_xlsx, sample_rows


class Clock:
    def __init__(self, s):
        self.t = datetime.fromisoformat(s)

    def __call__(self):
        return self.t

    def set(self, s):
        self.t = datetime.fromisoformat(s)

    def shift(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)
    clock = Clock("2026-11-03 10:00:00")
    app = create_app(tmp_path / "test.db", clock, start_backups=False)
    client = TestClient(app)
    r = client.post("/api/setup", json={"pin": "1234"})
    assert r.status_code == 200, r.text
    admin = r.json()["token"]
    e = {"c": client, "clock": clock, "app": app, "tmp": tmp_path, "tokens": {"admin": admin}}
    users = {u["name"]: u["id"] for u in client.get("/api/info").json()["users"]}
    e["ids"] = users
    for name, key, pin in (("Жексебаев М.", "zh", "1111"), ("Меден Д.", "md", "2222"),
                           ("Тулебаев М. Б.", "head", "3333")):
        assert call(e, "admin", "post", f"/api/users/{users[name]}/pin", json={"pin": pin}).status_code == 200
        e["tokens"][key] = client.post("/api/login", json={"user_id": users[name], "pin": pin}).json()["token"]
    r = call(e, "admin", "post", "/api/users", json={"name": "Финансовый директор", "role": "exec", "pin": "4444"})
    e["ids"]["exec"] = r.json()["id"]
    e["tokens"]["exec"] = client.post("/api/login", json={"user_id": r.json()["id"], "pin": "4444"}).json()["token"]
    return e


PINS = {"zh": ("Жексебаев М.", "1111"), "md": ("Меден Д.", "2222"), "head": ("Тулебаев М. Б.", "3333")}


def relogin(e):
    """Сессия живёт 12 часов: после перевода часов входим заново."""
    for key, (name, pin) in PINS.items():
        e["tokens"][key] = e["c"].post("/api/login", json={"user_id": e["ids"][name], "pin": pin}).json()["token"]


def call(e, who, method, url, **kw):
    return getattr(e["c"], method)(url, headers={"Authorization": f"Bearer {e['tokens'][who]}"}, **kw)


def do_import(e, commit, add=None):
    data = base64.b64encode(make_sample_xlsx()).decode()
    return call(e, "admin", "post", "/api/import", json={"data_b64": data, "commit": commit,
                                                           "add_employees": add or []})


# ---------------------------------------------------------------- вход

def test_setup_only_once(env):
    assert env["c"].post("/api/setup", json={"pin": "9999"}).status_code == 403


def test_lockout_after_5_wrong_pins(env):
    uid = env["ids"]["Меден Д."]
    for i in range(4):
        r = env["c"].post("/api/login", json={"user_id": uid, "pin": "0000"})
        assert r.status_code == 401
    r = env["c"].post("/api/login", json={"user_id": uid, "pin": "0000"})
    assert r.status_code == 423
    # даже верный PIN не пускает 15 минут
    assert env["c"].post("/api/login", json={"user_id": uid, "pin": "2222"}).status_code == 423
    env["clock"].shift(minutes=16)
    assert env["c"].post("/api/login", json={"user_id": uid, "pin": "2222"}).status_code == 200


def test_session_expires_after_12h(env):
    assert call(env, "zh", "get", "/api/me").status_code == 200
    env["clock"].shift(hours=12, minutes=1)
    assert call(env, "zh", "get", "/api/me").status_code == 401


def test_pin_is_hashed(env):
    conn = env["app"].state.svc.db.conn
    r = conn.execute("SELECT pin_hash, pin_salt FROM users WHERE pin_hash IS NOT NULL").fetchall()
    assert r and all("1111" not in x[0] and len(x[0]) == 64 and x[1] for x in r)


# ---------------------------------------------------------------- права

def test_manager_cannot_open_settings_or_edit_foreign_report(env):
    assert call(env, "zh", "get", "/api/settings").status_code == 403
    assert call(env, "zh", "put", "/api/settings", json={"plan_trips": 1}).status_code == 403
    assert call(env, "zh", "get", "/api/users").status_code == 403
    md = env["ids"]["Меден Д."]
    r = call(env, "zh", "put", "/api/reports", json={"user_id": md, "date": "2026-11-03", "calls": 99})
    assert r.status_code == 403
    assert call(env, "zh", "get", f"/api/reports/day?user_id={md}").status_code == 403
    # руководитель может
    assert call(env, "head", "put", "/api/reports", json={"user_id": md, "date": "2026-11-03",
                                                          "calls": 5}).status_code == 200


def test_exec_is_read_only(env):
    assert call(env, "exec", "post", "/api/trips", json={"origin": "A"}).status_code == 403
    assert call(env, "exec", "get", "/api/summary?month=2026-10").status_code == 200
    assert call(env, "exec", "get", "/api/export/1c?month=2026-10").status_code == 200


def test_report_rules(env):
    ok = call(env, "zh", "put", "/api/reports", json={"date": "2026-11-03", "calls": 10, "proposals": 2,
                                                      "notes": "Звонил на биржу"})
    assert ok.status_code == 200
    again = call(env, "zh", "put", "/api/reports", json={"date": "2026-11-03", "calls": 12})
    assert again.json()["report"]["calls"] == 12  # повторное сохранение обновляет
    assert call(env, "zh", "put", "/api/reports", json={"date": "2026-10-31", "calls": 1}).status_code == 200
    assert call(env, "zh", "put", "/api/reports", json={"date": "2026-10-30", "calls": 1}).status_code == 403
    assert call(env, "zh", "put", "/api/reports", json={"date": "2026-11-04", "calls": 1}).status_code == 403
    assert call(env, "zh", "put", "/api/reports", json={"notes": "x" * 501}).status_code == 400


def test_report_reminder(env):
    assert call(env, "zh", "get", "/api/me").json()["report_reminder"] is False
    env["clock"].set("2026-11-03 17:31:00")
    assert call(env, "zh", "get", "/api/me").json()["report_reminder"] is True
    call(env, "zh", "put", "/api/reports", json={"calls": 3})
    assert call(env, "zh", "get", "/api/me").json()["report_reminder"] is False


# ---------------------------------------------------------------- импорт и выгрузка

def test_import_preview_and_commit(env):
    p = do_import(env, False).json()
    assert p["new"] == 26 and p["updated"] == 0
    assert p["unknown_employees"] == []
    assert [s["reason"] for s in p["skipped"]] == ["нет клиента"]
    c = do_import(env, True).json()
    assert c["new"] == 26
    again = do_import(env, True).json()
    assert again["new"] == 0 and again["unchanged"] == 26
    clients = call(env, "admin", "get", "/api/clients").json()
    assert sum(1 for c in clients if c["name"].startswith("Coca Cola")) == 1  # дубль с пробелом склеен


def test_import_unknown_employee(env):
    call(env, "admin", "put", f"/api/users/{env['ids']['Меден Д.']}", json={"name": "Меден Дана", "role": "manager"})
    p = do_import(env, False).json()
    assert p["unknown_employees"] == ["Меден Д."]
    assert sum(1 for s in p["skipped"] if "неизвестный" in s["reason"]) == 6
    p = do_import(env, True, add=["Меден Д."]).json()
    assert p["new"] == 26


def test_october_summary_matches_spec(env):
    do_import(env, True)
    s = call(env, "head", "get", "/api/summary?month=2026-10").json()
    t = s["tiles"]
    assert t["trips"] == 26
    assert t["revenue"] == 12_344_000
    assert t["margin"] == 2_493_000
    assert round(t["margin_pct"], 1) == 20.2
    emp = {r["name"]: r for r in s["employees"]}
    zh, md = emp["Жексебаев М."], emp["Меден Д."]
    assert (zh["trips"], zh["clients"], zh["revenue"], zh["margin"]) == (20, 4, 5_372_000, 1_546_000)
    assert (md["trips"], md["clients"], md["revenue"], md["margin"]) == (6, 6, 6_972_000, 947_000)
    coca = s["clients"][0]
    assert coca["name"] == "Coca Cola" and round(coca["pct"]) == 44 and coca["high"]
    assert s["carriers"][0]["name"] == "Солнышка ИП" and s["carriers"][0]["value"] == 13
    assert t["requests"]["received"] == 0  # импортированные не входят в воронку заявок
    # менеджер видит сводку только по себе
    own = call(env, "zh", "get", f"/api/summary?month=2026-10&employee_id={env['ids']['Меден Д.']}").json()
    assert own["tiles"]["trips"] == 20


def test_export_matches_source(env):
    do_import(env, True)
    r = call(env, "zh", "get", "/api/export/1c?month=2026-10")
    assert r.status_code == 200
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    assert ws.title == "Октябрь 2026"
    header = [c.value if c.value is not None else "" for c in ws[1]]
    assert header == EXPORT_HEADERS
    assert ws.auto_filter.ref.startswith("A1:V")
    assert ws["A1"].font.name == "Open Sans" and ws["A1"].font.b and ws["A2"].font.sz == 8
    src = load_workbook(io.BytesIO(make_sample_xlsx())).active
    src_rows = [r for r in src.iter_rows(min_row=2, values_only=True) if r[11]]
    out_rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(out_rows) == 26
    norm = lambda v: " ".join(v.split()) if isinstance(v, str) else v  # noqa: E731
    for a, b in zip(src_rows, out_rows):
        for col in range(22):
            if col == 15:  # «Разница» — формула
                assert b[col] == f"=SUM(H{out_rows.index(b) + 2}-O{out_rows.index(b) + 2})"
                continue
            assert norm(a[col]) == norm(b[col]), (col, a[col], b[col])
    # вычисленное значение формулы сохранено в файле
    cached = load_workbook(io.BytesIO(r.content), data_only=True).active
    assert cached["P2"].value == src_rows[0][7] - src_rows[0][14]
    assert isinstance(cached["B2"].value, datetime)


# ---------------------------------------------------------------- рейсы

def new_trip(e, who="zh", **kw):
    data = {"origin": "Алматы", "destination": "Ташкент", "body_type": "Тент", "client_name": "Новый Клиент",
            "client_type": "Разовый"}
    data.update(kw)
    return call(e, who, "post", "/api/trips", json=data)


def test_trip_lifecycle(env):
    r = new_trip(env)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["stage"] == 1 and t["employee_id"] == env["ids"]["Жексебаев М."]
    adv = call(env, "zh", "post", f"/api/trips/{t['id']}/advance", json={"version": t["version"]})
    assert adv.status_code == 400 and "Стоимость перевозки" in adv.json()["error"]
    t = call(env, "zh", "put", f"/api/trips/{t['id']}", json={"version": t["version"], "price": "500 000"}).json()
    assert t["price"] == 500_000
    t = call(env, "zh", "post", f"/api/trips/{t['id']}/advance", json={"version": t["version"]}).json()
    assert t["stage"] == 2 and 2 in {h["stage"] for h in t["history"]}
    # конфликт версий
    stale = t["version"]
    t = call(env, "md", "put", f"/api/trips/{t['id']}", json={"version": stale, "comment": "замещение"}).json()
    r = call(env, "zh", "put", f"/api/trips/{t['id']}", json={"version": stale, "comment": "моё"})
    assert r.status_code == 409 and r.json()["error"] == "Рейс изменён другим сотрудником, обновите"
    # вернуть этап
    t = call(env, "zh", "post", f"/api/trips/{t['id']}/rollback", json={"version": t["version"]}).json()
    assert t["stage"] == 1 and 2 not in {h["stage"] for h in t["history"]}
    # отказ
    r = call(env, "zh", "post", f"/api/trips/{t['id']}/refuse", json={"version": t["version"]})
    assert r.status_code == 400
    t = call(env, "zh", "post", f"/api/trips/{t['id']}/refuse",
             json={"version": t["version"], "reason": "Дорогая ставка"}).json()
    assert t["stage"] == 0
    # журнал
    log = call(env, "zh", "get", f"/api/audit?entity=trip&entity_id={t['id']}").json()
    assert {"create", "update", "stage", "rollback", "refuse"} <= {x["action"] for x in log}
    # удалять может только РО
    assert call(env, "zh", "delete", f"/api/trips/{t['id']}").status_code == 403
    assert call(env, "head", "delete", f"/api/trips/{t['id']}").status_code == 200


def test_manager_cannot_reassign(env):
    t = new_trip(env).json()
    md = env["ids"]["Меден Д."]
    r = call(env, "zh", "put", f"/api/trips/{t['id']}", json={"version": t["version"], "employee_id": md})
    assert r.status_code == 403
    r = call(env, "head", "put", f"/api/trips/{t['id']}", json={"version": t["version"], "employee_id": md})
    assert r.status_code == 200 and r.json()["employee_id"] == md


def test_rate_late_flag_and_1730(env):
    env["clock"].set("2026-11-03 17:30:00")
    t = new_trip(env).json()
    env["clock"].set("2026-11-04 10:30:00")
    relogin(env)
    assert not call(env, "zh", "get", f"/api/trips/{t['id']}").json()["overdue"]
    env["clock"].set("2026-11-04 10:31:00")
    t = call(env, "zh", "get", f"/api/trips/{t['id']}").json()
    assert [f["code"] for f in t["flags"]] == ["RATE_LATE"]


def _to_stage(e, t, stage):
    t = call(e, "zh", "put", f"/api/trips/{t['id']}", json={
        "version": t["version"], "price": 500000, "carrier_name": "Солнышка ИП", "carrier_rate": 400000,
        "ship_date": "2026-11-03", "ttn": "123"}).json()
    while t["stage"] < stage:
        r = call(e, "zh", "post", f"/api/trips/{t['id']}/advance", json={"version": t["version"]})
        assert r.status_code == 200, r.text
        t = r.json()
    return t


def test_no_contact_and_close_late(env):
    t = _to_stage(env, new_trip(env).json(), 7)
    env["clock"].shift(hours=13)
    relogin(env)
    t = call(env, "zh", "get", f"/api/trips/{t['id']}").json()
    assert "NO_CONTACT" in {f["code"] for f in t["flags"]}
    t = call(env, "zh", "post", f"/api/trips/{t['id']}/contact", json={"location": "Шымкент"}).json()
    assert "NO_CONTACT" not in {f["code"] for f in t["flags"]} and t["driver_location"] == "Шымкент"
    env["clock"].set("2026-11-03 12:00:00")
    relogin(env)
    t = _to_stage(env, t, 8)
    env["clock"].set("2026-11-11 12:00:00")  # 6 рабочих дней спустя
    relogin(env)
    t = call(env, "zh", "get", f"/api/trips/{t['id']}").json()
    assert "CLOSE_LATE" in {f["code"] for f in t["flags"]}
    s = call(env, "head", "get", "/api/summary?month=2026-11").json()
    assert s["tiles"]["overdue"] == 1


# ---------------------------------------------------------------- воронка

def test_lead_first_trip_and_duplicates(env):
    r = call(env, "zh", "post", "/api/leads", json={"company": "Новый Завод", "source": "Холодный звонок"})
    assert r.status_code == 200
    lead = r.json()
    r = call(env, "md", "post", "/api/leads", json={"company": "новый  завод"})
    assert r.status_code == 409 and "Жексебаев" in r.json()["error"]
    lead = call(env, "zh", "post", f"/api/leads/{lead['id']}/advance", json={"version": lead["version"]}).json()
    assert lead["stage"] == 2
    t = new_trip(env, client_name="Новый Завод")
    _to_stage(env, t.json(), 6)
    lead = call(env, "zh", "get", f"/api/leads/{lead['id']}").json()
    assert lead["stage"] == 4
    # менеджер не видит чужие лиды
    assert call(env, "md", "get", f"/api/leads/{lead['id']}").status_code == 403
    assert call(env, "md", "get", "/api/leads").json()["leads"] == []
    hints = call(env, "zh", "get", "/api/reports/day").json()["hints"]
    assert hints["leads_created"] == 1 and hints["leads_kp"] == 1


# ---------------------------------------------------------------- резервная копия и выгрузка

def test_backup_and_full_export(env):
    r = call(env, "admin", "post", "/api/backups")
    assert r.status_code == 200
    assert (env["tmp"] / "backup" / "avto_2026-11-03.db").exists()
    assert backup.ensure_today(env["app"].state.svc) is None  # за сегодня уже есть
    r = call(env, "admin", "get", "/api/export/all")
    wb = load_workbook(io.BytesIO(r.content))
    assert "trips" in wb.sheetnames and "audit_log" in wb.sheetnames
    assert "pin_hash" not in [c.value for c in wb["users"][1]]
    assert call(env, "zh", "get", "/api/export/all").status_code == 403


def test_backup_keeps_30(tmp_path):
    from app.db import Database
    db = Database(tmp_path / "x.db")
    for d in range(1, 36):
        backup.make_backup(db, tmp_path / "b", f"2026-01-{d:02d}" if d <= 31 else f"2026-02-{d - 31:02d}")
    files = backup.list_backups(tmp_path / "b")
    assert len(files) == 30 and files[-1]["name"] == "avto_2026-01-06.db"


def test_sample_rows_have_expected_totals():
    rows = sample_rows()
    assert len(rows) == 26
    assert sum(r["price"] for r in rows) == 12_344_000
    assert sum(r["price"] - r["rate"] for r in rows) == 2_493_000
