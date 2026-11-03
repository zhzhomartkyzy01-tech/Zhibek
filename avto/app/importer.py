"""Импорт старого Excel-реестра (п. 5.2): предпросмотр и запись."""
from __future__ import annotations

from collections import Counter

from . import logic as L
from .db import one, rows
from .excel_io import import_key, parse_registry
from .services import AppError, Service, need

DATA_FIELDS = ["ship_date", "truck_brand", "plate", "driver", "origin", "destination", "price", "body_type",
               "payment_doc", "driver_location", "carrier_rate", "ttn", "pay_client", "pay_agent", "postpay"]


def import_registry(svc: Service, user: dict, data: bytes, commit: bool = False, add_employees=()) -> dict:
    need(user, "admin")
    try:
        parsed = parse_registry(data)
    except ValueError as e:
        raise AppError(400, str(e))
    add_keys = {L.person_key(n) for n in add_employees or []}
    res = {"sheets": parsed["sheets"], "total_rows": len(parsed["rows"]) + len(parsed["skipped"]),
           "new": 0, "updated": 0, "unchanged": 0, "skipped": list(parsed["skipped"]),
           "unknown_employees": [], "months": Counter(), "committed": commit, "samples": []}
    with svc.db.tx() as conn:
        users = rows(conn.execute("SELECT id, name FROM users WHERE deleted=0"))
        ukeys = {L.person_key(u["name"]): u["id"] for u in users}
        unknown = {}
        for r in parsed["rows"]:
            k = L.person_key(r["employee"])
            if r["employee"] and k not in ukeys:
                unknown.setdefault(k, r["employee"])
        res["unknown_employees"] = sorted(unknown.values())
        if commit:
            for k, name in unknown.items():
                if k in add_keys:
                    # добавляем неактивным менеджером: администратор включит и задаст PIN, если нужно
                    cur = conn.execute("INSERT INTO users(name, role, active, created_at) VALUES(?,?,0,?)",
                                       (name, "manager", svc.ts()))
                    ukeys[k] = cur.lastrowid
                    svc.audit(conn, user, "user", cur.lastrowid, "create", {"name": [None, name], "source": "импорт"})
        existing = {t["import_key"]: t for t in rows(conn.execute(
            "SELECT t.*, c.name_key client_key, k.name_key carrier_key FROM trips t "
            "LEFT JOIN clients c ON c.id=t.client_id LEFT JOIN carriers k ON k.id=t.carrier_id "
            "WHERE t.deleted=0 AND t.import_key IS NOT NULL"))}
        seen = set()
        for r in parsed["rows"]:
            where = {"sheet": r["sheet"], "row": r["row"]}
            ek = L.person_key(r["employee"])
            if not r["employee"]:
                res["skipped"].append({**where, "reason": "не указан сотрудник"})
                continue
            if ek not in ukeys and not (not commit and ek in add_keys):
                res["skipped"].append({**where, "reason": f"неизвестный сотрудник «{r['employee']}»"})
                continue
            month = r["ship_date"][:7]
            key = import_key(month, r["reg_no"], r["plate"], r["client"])
            if key in seen:
                res["skipped"].append({**where, "reason": "повтор строки в файле"})
                continue
            seen.add(key)
            res["months"][month] += 1
            old = existing.get(key)
            if old is None:
                res["new"] += 1
                if len(res["samples"]) < 5:
                    res["samples"].append({k: r[k] for k in ("ship_date", "client", "origin", "destination",
                                                             "price", "carrier_rate", "employee", "status")})
                if commit:
                    _insert(svc, conn, user, r, key, ukeys[ek])
                continue
            changes = {f: [old[f], r[f]] for f in DATA_FIELDS if old[f] != r[f]}
            if old["client_key"] != L.norm_key(r["client"]):
                changes["client"] = [old["client_key"], r["client"]]
            if (old["carrier_key"] or "") != L.norm_key(r["carrier"]):
                changes["carrier"] = [old["carrier_key"], r["carrier"]]
            if ukeys.get(ek) != old["employee_id"]:
                changes["employee_id"] = [old["employee_id"], ukeys.get(ek)]
            raise_stage = old["imported"] and r["stage"] > old["stage"]
            if raise_stage:
                changes["stage"] = [L.TRIP_STAGES[old["stage"]], L.TRIP_STAGES[r["stage"]]]
            if not changes:
                res["unchanged"] += 1
                continue
            res["updated"] += 1
            if commit:
                _update(svc, conn, user, old, r, ukeys[ek], changes, raise_stage)
    res["months"] = dict(sorted(res["months"].items()))
    res["skipped"].sort(key=lambda x: (x["sheet"], x["row"]))
    return res


def _values(svc, conn, user, r, employee_id):
    v = {f: r[f] for f in DATA_FIELDS}
    v["client_id"] = svc.client_id(conn, r["client"], user)
    v["carrier_id"] = svc.carrier_id(conn, r["carrier"], user) if r["carrier"] else None
    v["employee_id"] = employee_id
    return v


def _insert(svc, conn, user, r, key, employee_id):
    v = _values(svc, conn, user, r, employee_id)
    at = f"{r['ship_date']} 00:00:00"
    v["client_type"] = one(conn.execute("SELECT type FROM clients WHERE id=?", (v["client_id"],)))["type"]
    v.update({"stage": r["stage"], "received_at": at, "imported": 1, "import_key": key, "version": 1,
              "created_at": svc.ts(), "created_by": user["id"], "updated_at": svc.ts(), "updated_by": user["id"]})
    cur = conn.execute(f"INSERT INTO trips({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
    for st in range(1, r["stage"] + 1):
        svc._add_trip_history(conn, cur.lastrowid, st, user, at, imported=1)
    svc.audit(conn, user, "trip", cur.lastrowid, "import", {"sheet": [None, r["sheet"]], "row": [None, r["row"]]})


def _update(svc, conn, user, old, r, employee_id, changes, raise_stage):
    v = _values(svc, conn, user, r, employee_id)
    if raise_stage:
        v["stage"] = r["stage"]
        at = f"{r['ship_date']} 00:00:00"
        for st in range(old["stage"] + 1, r["stage"] + 1):
            svc._add_trip_history(conn, old["id"], st, user, at, imported=1)
    v.update({"version": old["version"] + 1, "updated_at": svc.ts(), "updated_by": user["id"]})
    conn.execute(f"UPDATE trips SET {','.join(k + '=?' for k in v)} WHERE id=?", [*v.values(), old["id"]])
    svc.audit(conn, user, "trip", old["id"], "import_update", changes)
