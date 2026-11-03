"""Веб-сервер (FastAPI): API и страница сайта."""
from __future__ import annotations

import base64
import socket
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, backup
from . import logic as L
from .config import DB_PATH, PORT, STATIC_DIR, VERSION
from .db import TABLES, Database, rows
from .excel_io import build_1c_export, build_full_export, export_filename
from .importer import import_registry
from .services import AppError, Service, need

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def lan_addresses(port: int) -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    ips = sorted(ip for ip in ips if not ip.startswith("127."))
    return [f"http://{ip}:{port}" for ip in ips] or [f"http://localhost:{port}"]


def xlsx_response(data: bytes, filename: str) -> Response:
    ascii_name = "".join(ch if ord(ch) < 128 else "_" for ch in filename)
    return Response(data, media_type=XLSX, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"})


def create_app(db_path=None, now_fn=None, start_backups=True) -> FastAPI:
    db = Database(db_path or DB_PATH)
    svc = Service(db, now_fn or L.now_local)
    svc.init_defaults()
    app = FastAPI(title="Автоперевозки", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.svc = svc
    app.state.scheduler = None
    if start_backups:
        app.state.scheduler = backup.BackupScheduler(svc)
        app.state.scheduler.start()

    @app.exception_handler(AppError)
    def _app_error(_req, exc: AppError):
        return JSONResponse({"error": exc.message, "code": exc.code, **exc.extra}, status_code=exc.status)

    @app.exception_handler(ValueError)
    def _value_error(_req, exc: ValueError):
        return JSONResponse({"error": str(exc), "code": "bad_value"}, status_code=400)

    def user_dep(request: Request) -> dict:
        h = request.headers.get("authorization", "")
        token = h[7:].strip() if h.lower().startswith("bearer ") else None
        u = svc.user_by_token(token)
        if not u:
            raise AppError(401, "Сессия истекла. Войдите заново", "auth")
        return u

    U = Depends(user_dep)

    # ------------------------------------------------------------ страница
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ------------------------------------------------------------ вход
    @app.get("/api/info")
    def info():
        return {"addresses": lan_addresses(PORT), "version": VERSION, "needs_setup": svc.needs_setup(),
                "users": svc.login_users()}

    @app.post("/api/setup")
    def setup(data: dict = Body(...)):
        adm = svc.setup_admin(str(data.get("pin") or ""))
        return svc.login(adm["id"], str(data.get("pin")))

    @app.post("/api/login")
    def login(data: dict = Body(...)):
        return svc.login(data.get("user_id"), str(data.get("pin") or ""))

    @app.post("/api/logout")
    def logout(request: Request):
        h = request.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            svc.logout(h[7:].strip())
        return {"ok": True}

    @app.get("/api/me")
    def me(user=U):
        return svc.me(user)

    @app.get("/api/refs")
    def refs(user=U):
        return svc.refs(user)

    # ------------------------------------------------------------ рейсы
    @app.get("/api/trips")
    def trips(scope: str = "registry", month: str | None = None, employee_id: int | None = None,
              client_id: int | None = None, user=U):
        return svc.list_trips(user, scope, month, employee_id, client_id)

    @app.get("/api/trips/{trip_id}")
    def trip(trip_id: int, user=U):
        return svc.get_trip(user, trip_id)

    @app.post("/api/trips")
    def trip_create(data: dict = Body(...), user=U):
        return svc.create_trip(user, data)

    @app.put("/api/trips/{trip_id}")
    def trip_update(trip_id: int, data: dict = Body(...), user=U):
        return svc.update_trip(user, trip_id, data)

    @app.post("/api/trips/{trip_id}/advance")
    def trip_advance(trip_id: int, data: dict = Body(default={}), user=U):
        return svc.advance_trip(user, trip_id, data.get("version"))

    @app.post("/api/trips/{trip_id}/rollback")
    def trip_rollback(trip_id: int, data: dict = Body(default={}), user=U):
        return svc.rollback_trip(user, trip_id, data.get("version"))

    @app.post("/api/trips/{trip_id}/refuse")
    def trip_refuse(trip_id: int, data: dict = Body(default={}), user=U):
        return svc.refuse_trip(user, trip_id, data.get("version"), data.get("reason"), data.get("comment"))

    @app.post("/api/trips/{trip_id}/contact")
    def trip_contact(trip_id: int, data: dict = Body(default={}), user=U):
        return svc.contact_trip(user, trip_id, data.get("location"))

    @app.delete("/api/trips/{trip_id}")
    def trip_delete(trip_id: int, user=U):
        svc.delete_trip(user, trip_id)
        return {"ok": True}

    @app.get("/api/export/1c")
    def export_1c(month: str, employee_id: int | None = None, user=U):
        need(user, "export_1c")
        data = svc.list_trips(user, "registry", month, employee_id)
        items = [t for t in data["trips"] if t["stage"] >= L.LOADED]
        return xlsx_response(build_1c_export(items, month), export_filename(month))

    # ------------------------------------------------------------ лиды
    @app.get("/api/leads")
    def leads(month: str | None = None, owner_id: int | None = None, user=U):
        return svc.list_leads(user, month, owner_id)

    @app.get("/api/leads/check")
    def lead_check(company: str, user=U):
        return {"warnings": svc.lead_duplicates(user, company)}

    @app.get("/api/leads/{lead_id}")
    def lead(lead_id: int, user=U):
        return svc.get_lead(user, lead_id)

    @app.post("/api/leads")
    def lead_create(data: dict = Body(...), user=U):
        return svc.save_lead(user, data)

    @app.put("/api/leads/{lead_id}")
    def lead_update(lead_id: int, data: dict = Body(...), user=U):
        return svc.save_lead(user, data, lead_id)

    @app.post("/api/leads/{lead_id}/advance")
    def lead_advance(lead_id: int, data: dict = Body(default={}), user=U):
        return svc.advance_lead(user, lead_id, data.get("version"))

    @app.post("/api/leads/{lead_id}/refuse")
    def lead_refuse(lead_id: int, data: dict = Body(default={}), user=U):
        return svc.refuse_lead(user, lead_id, data.get("version"), data.get("reason"), data.get("comment"))

    @app.post("/api/leads/{lead_id}/rollback")
    def lead_rollback(lead_id: int, data: dict = Body(default={}), user=U):
        return svc.rollback_lead(user, lead_id, data.get("version"))

    @app.delete("/api/leads/{lead_id}")
    def lead_delete(lead_id: int, user=U):
        svc.delete_lead(user, lead_id)
        return {"ok": True}

    # ------------------------------------------------------------ дневные отчёты
    @app.get("/api/reports/day")
    def report_day(date: str | None = None, user_id: int | None = None, user=U):
        return svc.get_report(user, date, user_id)

    @app.put("/api/reports")
    def report_save(data: dict = Body(...), user=U):
        return svc.save_report(user, data)

    @app.get("/api/reports/grid")
    def report_grid(user=U):
        return svc.report_grid(user)

    # ------------------------------------------------------------ сводка
    @app.get("/api/summary")
    def summary(month: str | None = None, employee_id: int | None = None, user=U):
        return svc.summary(user, month, employee_id)

    # ------------------------------------------------------------ справочники
    @app.get("/api/clients")
    def clients(user=U):
        return svc.list_clients(user)

    @app.post("/api/clients")
    def client_create(data: dict = Body(...), user=U):
        return svc.save_directory(user, "clients", data)

    @app.put("/api/clients/{item_id}")
    def client_update(item_id: int, data: dict = Body(...), user=U):
        return svc.save_directory(user, "clients", data, item_id)

    @app.post("/api/clients/merge")
    def client_merge(data: dict = Body(...), user=U):
        svc.merge_directory(user, "clients", int(data["target_id"]), data.get("source_ids") or [])
        return {"ok": True}

    @app.get("/api/carriers")
    def carriers(user=U):
        return svc.list_carriers(user)

    @app.post("/api/carriers")
    def carrier_create(data: dict = Body(...), user=U):
        return svc.save_directory(user, "carriers", data)

    @app.put("/api/carriers/{item_id}")
    def carrier_update(item_id: int, data: dict = Body(...), user=U):
        return svc.save_directory(user, "carriers", data, item_id)

    @app.post("/api/carriers/merge")
    def carrier_merge(data: dict = Body(...), user=U):
        svc.merge_directory(user, "carriers", int(data["target_id"]), data.get("source_ids") or [])
        return {"ok": True}

    # ------------------------------------------------------------ администрирование
    @app.get("/api/users")
    def users(user=U):
        return svc.list_users(user)

    @app.post("/api/users")
    def user_create(data: dict = Body(...), user=U):
        return svc.save_user(user, data)

    @app.put("/api/users/{user_id}")
    def user_update(user_id: int, data: dict = Body(...), user=U):
        return svc.save_user(user, data, user_id)

    @app.post("/api/users/{user_id}/pin")
    def user_pin(user_id: int, data: dict = Body(...), user=U):
        svc.set_pin(user, user_id, str(data.get("pin") or ""))
        return {"ok": True}

    @app.post("/api/users/{user_id}/unlock")
    def user_unlock(user_id: int, user=U):
        svc.unlock_user(user, user_id)
        return {"ok": True}

    @app.delete("/api/users/{user_id}")
    def user_delete(user_id: int, user=U):
        svc.delete_user(user, user_id)
        return {"ok": True}

    @app.get("/api/settings")
    def settings(user=U):
        need(user, "admin")
        return {"settings": svc.settings(), "holidays": svc.holidays(), "defaults": L.DEFAULT_SETTINGS}

    @app.put("/api/settings")
    def settings_save(data: dict = Body(...), user=U):
        return svc.update_settings(user, data)

    @app.put("/api/holidays")
    def holidays_save(data: dict = Body(...), user=U):
        return svc.set_holidays(user, data.get("holidays") or [])

    @app.post("/api/import")
    def do_import(data: dict = Body(...), user=U):
        need(user, "admin")
        try:
            raw = base64.b64decode(data.get("data_b64") or "", validate=False)
        except ValueError:
            raise AppError(400, "Файл повреждён")
        if not raw:
            raise AppError(400, "Выберите файл .xlsx")
        return import_registry(svc, user, raw, bool(data.get("commit")), data.get("add_employees") or [])

    @app.get("/api/deleted")
    def deleted(user=U):
        return svc.deleted_items(user)

    @app.delete("/api/items/{entity}/{item_id}")
    def item_delete(entity: str, item_id: int, user=U):
        svc.delete_item(user, entity, item_id)
        return {"ok": True}

    @app.post("/api/restore/{entity}/{item_id}")
    def restore(entity: str, item_id: int, user=U):
        svc.restore(user, entity, item_id)
        return {"ok": True}

    @app.get("/api/audit")
    def audit(entity: str | None = None, entity_id: int | None = None, limit: int = 300, user=U):
        return svc.audit_list(user, entity, entity_id, limit)

    @app.get("/api/export/all")
    def export_all(user=U):
        need(user, "admin")
        with db.lock:
            tables = {}
            for t in TABLES:
                items = rows(db.conn.execute(f"SELECT * FROM {t}"))
                if t == "users":
                    for it in items:
                        it.pop("pin_hash", None)
                        it.pop("pin_salt", None)
                tables[t] = items
        return xlsx_response(build_full_export(tables), f"Все данные {svc.now().strftime('%Y-%m-%d')}.xlsx")

    @app.get("/api/backups")
    def backups(user=U):
        need(user, "admin")
        d = backup.backup_dir(svc.settings()["backup_dir"])
        sch = app.state.scheduler
        return {"dir": str(d.resolve()), "files": backup.list_backups(d),
                "last_error": sch.last_error if sch else None}

    @app.post("/api/backups")
    def backup_now(user=U):
        need(user, "admin")
        d = backup.backup_dir(svc.settings()["backup_dir"])
        try:
            p = backup.make_backup(db, d, svc.now().date().isoformat())
        except OSError as e:
            raise AppError(500, f"Не удалось сохранить копию в «{d}»: {e}")
        with db.tx() as conn:
            svc.audit(conn, user, "backup", None, "create", {"file": [None, p.name]})
        return {"file": p.name, "dir": str(Path(d).resolve())}

    return app
