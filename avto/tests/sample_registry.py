"""Тестовый реестр «как октябрьский»: те же итоги, что в п. 8.3 ТЗ.
Данные выдуманные (настоящий файл в репозиторий не кладём), но совпадают по контрольным цифрам:
26 рейсов, выручка 12 344 000 ₸, маржа 2 493 000 ₸, Coca Cola — 44% маржи,
Солнышка ИП — 13 рейсов из 26, плюс пустая строка со статусом «В пути»."""
import io
from datetime import datetime

from openpyxl import Workbook

from app.excel_io import EXPORT_HEADERS

ZH, MD = "Жексебаев М.", "Меден Д."


def sample_rows():
    rows = []
    # Жексебаев: 20 рейсов, 4 клиента, 5 372 000 / 1 546 000
    for i in range(10):
        client = "Coca Cola " if i % 3 == 0 else "Coca Cola"  # дубль с пробелом в конце
        rows.append((ZH, client, 260_000, 260_000 - (107_000 if i == 9 else 110_000)))
    rows += [(ZH, "Астана Агро", 280_000, 235_000)] * 5
    rows += [(ZH, "Шымкент Пиво", 300_000, 250_000)] * 3
    rows += [(ZH, "Туран Трейд", 236_000, 199_000)] * 2
    # Меден: 6 рейсов, 6 клиентов, 6 972 000 / 947 000
    for client, price, margin in (("Казфосфат", 1_200_000, 160_000), ("Евраз Каспиан", 1_150_000, 150_000),
                                  ("Алтын Дан", 1_100_000, 155_000), ("Рахат", 1_250_000, 170_000),
                                  ("Кублей", 1_122_000, 152_000), ("Тараз Цемент", 1_150_000, 160_000)):
        rows.append((MD, client, price, price - margin))
    out = []
    for i, (emp, client, price, rate) in enumerate(rows):
        day = 1 + (i * 29) // len(rows)
        out.append({
            "date": datetime(2026, 10, day),
            "brand": "Volvo" if i % 2 else "MAN",
            "plate": f"{100 + i}ABC13/{20 + i}XYZ13",
            "driver": f"Водитель {i + 1}",
            "from": "Шымкент" if i % 2 else "Алматы",
            "to": "Сарыагаш" if i % 3 else "Ташкент",
            "price": price,
            "body": "Реф" if i % 4 == 0 else "Тент",
            "pay": f"Шымкент Сарыагаш {949 + i}ATL13 100%",
            "loc": "Ташкент" if i % 5 == 0 else None,
            "client": client,
            "emp": emp,
            "carrier": "Солнышка ИП" if i < 13 else f"Перевозчик {i % 4 + 1} ТОО",
            "rate": rate,
            "ttn": 515745163 + i if i % 7 else f"№ 2026/{60 + i}",
            "status": "В пути" if i in (5, 20) else "Доставлен",
            "pay_client": "оплачено" if i % 2 else None,
            "pay_agent": "100%" if i % 3 == 0 else None,
            "postpay": "10 дней" if i % 6 == 0 else None,
        })
    return out


def make_sample_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Октябрь 2026"
    ws.append(EXPORT_HEADERS)
    for n, r in enumerate(sample_rows(), 1):
        ws.append([n, r["date"], r["brand"], r["plate"], r["driver"], r["from"], r["to"], r["price"], r["body"],
                   r["pay"], r["loc"], r["client"], r["emp"], r["carrier"], r["rate"], r["price"] - r["rate"],
                   r["ttn"], r["status"], r["pay_client"], r["pay_agent"], None, r["postpay"]])
    # пустая строка без клиента, но со статусом — должна пропускаться
    ws.append([None] * 17 + ["В пути"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
