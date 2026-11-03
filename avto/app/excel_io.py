"""Excel: выгрузка реестра для 1С (п. 5.1), чтение старых реестров (п. 5.2), выгрузка всех данных."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from . import logic as L

# Заголовки ровно как в нынешнем реестре: пробелы в конце, строчная «оплата», пустая колонка U.
EXPORT_HEADERS = [
    "№", "Дата отправки", "Марка Машины", "ГОС.Номер", "Данные водителя", "Пункт Отправления",
    "Пункт назначения", "Стоимость перевозки", "Тент/Реф", "Платежка ", "Местонахождение Водителей",
    "Клиент", "Сотрудник", "Перевозчик", "Ставка перевозчика", "Разница", "НОМЕР ТТН", "Статус",
    "Оплата от Клиента ", "оплата Агенту", "", "Постоплата",
]
COLUMN_WIDTHS = [5, 11, 13, 15, 22, 18, 18, 12, 9, 26, 20, 20, 15, 22, 12, 11, 13, 10, 18, 14, 3, 14]
FONT = "Open Sans"


def _hkey(s) -> str:
    """Заголовок без учёта регистра и пробелов; ё = е."""
    return re.sub(r"\s+", "", str(s or "")).casefold().replace("ё", "е")


# Поиск колонок при импорте — по заголовку, а не по позиции.
IMPORT_MAP = {
    "№": "reg_no", "no": "reg_no", "n": "reg_no",
    "датаотправки": "ship_date", "маркамашины": "truck_brand", "гос.номер": "plate", "госномер": "plate",
    "данныеводителя": "driver", "пунктотправления": "origin", "пунктназначения": "destination",
    "стоимостьперевозки": "price", "тент/реф": "body_type", "платежка": "payment_doc",
    "местонахождениеводителей": "driver_location", "местонахождениеводителя": "driver_location",
    "клиент": "client", "сотрудник": "employee", "перевозчик": "carrier", "ставкаперевозчика": "carrier_rate",
    "разница": "margin", "номерттн": "ttn", "статус": "status", "оплатаотклиента": "pay_client",
    "оплатаагенту": "pay_agent", "постоплата": "postpay",
}


# ---------------------------------------------------------------- выгрузка для 1С

def export_filename(month: str) -> str:
    return f"Реестр авто {L.month_title(month)}.xlsx"


def _ttn_value(v):
    s = str(v or "").strip()
    if s.isdigit() and len(s) < 16:
        return int(s)
    return s or None


def build_1c_export(trips: list[dict], month: str) -> bytes:
    """trips — рейсы месяца на этапах «Загружен» и дальше (уже отфильтрованы)."""
    trips = sorted(trips, key=lambda t: (str(t.get("ship_date") or ""), t.get("reg_no") or 0, t["id"]))
    wb = Workbook()
    ws = wb.active
    ws.title = L.month_title(month)
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_font = Font(name=FONT, size=10, bold=True)
    data_font = Font(name=FONT, size=8)
    for col, title in enumerate(EXPORT_HEADERS, 1):
        c = ws.cell(row=1, column=col, value=title if title else None)
        c.font = head_font
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = border
        ws.column_dimensions[get_column_letter(col)].width = COLUMN_WIDTHS[col - 1]
    margins = {}
    for i, t in enumerate(trips, 1):
        r = i + 1
        ship = L.parse_date(t.get("ship_date"))
        margin = (t.get("price") or 0) - (t.get("carrier_rate") or 0)
        margins[r] = margin
        values = [
            i, datetime.combine(ship, datetime.min.time()) if ship else None, t.get("truck_brand"), t.get("plate"),
            t.get("driver"), t.get("origin"), t.get("destination"), t.get("price"), t.get("body_type"),
            t.get("payment_doc"), t.get("driver_location"), t.get("client_name"), t.get("employee_name"),
            t.get("carrier_name"), t.get("carrier_rate"), f"=SUM(H{r}-O{r})", _ttn_value(t.get("ttn")),
            L.status_1c(t["stage"]), t.get("pay_client"), t.get("pay_agent"), None, t.get("postpay"),
        ]
        for col, v in enumerate(values, 1):
            c = ws.cell(row=r, column=col, value=v)
            c.font = data_font
            c.border = border
            c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(row=r, column=2).number_format = "DD.MM.YYYY"
        for col in (8, 15, 16):
            ws.cell(row=r, column=col).number_format = "#,##0"
    last = max(1, len(trips) + 1)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(EXPORT_HEADERS))}{last}"
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return _fill_formula_values(buf.getvalue(), margins)


def _fill_formula_values(data: bytes, margins: dict[int, int]) -> bytes:
    """openpyxl сохраняет формулу без значения. Вписываем вычисленную «Разницу»,
    чтобы её видели программы, которые читают файл без пересчёта (в т.ч. загрузка в 1С)."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            content = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                xml = content.decode("utf-8")

                def repl(m):
                    r = int(m.group(1))
                    return f'<c r="P{r}"{m.group(2)}><f>{m.group(3)}</f><v>{margins.get(r, 0)}</v></c>'

                xml = re.sub(r'<c r="P(\d+)"([^>]*)><f>([^<]*)</f><v\s*/?>(?:</v>)?</c>', repl, xml)
                content = xml.encode("utf-8")
            dst.writestr(item, content)
    return out.getvalue()


# ---------------------------------------------------------------- импорт

def _cell_text(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (datetime, date)):
        return v.strftime("%d.%m.%Y")
    s = str(v).strip()
    return s or None


def _cell_money(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(v)
    s = re.sub(r"[\s ₸]", "", str(v)).replace(",", ".")
    if not s or s.startswith("="):
        return None
    try:
        return round(float(s))
    except ValueError:
        return None


def _cell_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # число-дата Excel
        from openpyxl.utils.datetime import from_excel
        return from_excel(v).date()
    s = str(v).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _find_header(ws, max_rows=15):
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_rows, values_only=True), 1):
        cols = {}
        for c_idx, v in enumerate(row):
            key = IMPORT_MAP.get(_hkey(v))
            if key and key not in cols:
                cols[key] = c_idx
        if "client" in cols and len(cols) >= 5:
            return r_idx, cols
    return None, None


def parse_registry(data: bytes) -> dict:
    """Читает все листы с заголовками реестра. Возвращает строки и пропуски."""
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Не удалось открыть файл Excel: {e}")
    result = {"sheets": [], "rows": [], "skipped": []}
    for ws in wb.worksheets:
        header_row, cols = _find_header(ws)
        if not header_row:
            continue
        result["sheets"].append(ws.title)
        for r_idx, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), header_row + 1):
            def get(key):
                i = cols.get(key)
                return row[i] if i is not None and i < len(row) else None

            if all(v is None or str(v).strip() == "" for v in row):
                continue
            where = {"sheet": ws.title, "row": r_idx}
            client = L.clean_name(get("client"))
            if not client:
                result["skipped"].append({**where, "reason": "нет клиента"})
                continue
            ship = _cell_date(get("ship_date"))
            if not ship:
                result["skipped"].append({**where, "reason": "нет даты отправки"})
                continue
            status = str(get("status") or "").strip().casefold()
            reg_no = get("reg_no")
            try:
                reg_no = int(float(reg_no)) if reg_no not in (None, "") else None
            except (TypeError, ValueError):
                reg_no = None
            result["rows"].append({
                **where,
                "reg_no": reg_no,
                "ship_date": ship.isoformat(),
                "truck_brand": _cell_text(get("truck_brand")),
                "plate": _cell_text(get("plate")),
                "driver": _cell_text(get("driver")),
                "origin": _cell_text(get("origin")),
                "destination": _cell_text(get("destination")),
                "price": _cell_money(get("price")),
                "body_type": _cell_text(get("body_type")),
                "payment_doc": _cell_text(get("payment_doc")),
                "driver_location": _cell_text(get("driver_location")),
                "client": client,
                "employee": L.clean_name(get("employee")),
                "carrier": L.clean_name(get("carrier")),
                "carrier_rate": _cell_money(get("carrier_rate")),
                "ttn": _cell_text(get("ttn")),
                "status": status,
                "stage": L.CLOSED if status.startswith("доставлен") else L.IN_TRANSIT,
                "pay_client": _cell_text(get("pay_client")),
                "pay_agent": _cell_text(get("pay_agent")),
                "postpay": _cell_text(get("postpay")),
            })
    wb.close()
    if not result["sheets"]:
        raise ValueError("В файле не найдено листов с заголовками реестра (нужны хотя бы «Клиент» и «Дата отправки»)")
    return result


def import_key(month: str, reg_no, plate, client) -> str:
    """Ключ против дублей при повторном импорте: месяц + № + гос. номер + клиент."""
    return "|".join([month, str(reg_no or ""), re.sub(r"\s+", "", str(plate or "")).casefold(), L.norm_key(client)])


# ---------------------------------------------------------------- выгрузка всех данных

def build_full_export(tables: dict[str, list[dict]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    bold = Font(bold=True)
    for name, items in tables.items():
        ws = wb.create_sheet(name[:31])
        if not items:
            ws.append(["(пусто)"])
            continue
        cols = list(items[0].keys())
        ws.append(cols)
        for c in ws[1]:
            c.font = bold
        for it in items:
            ws.append([it.get(c) for c in cols])
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
