from datetime import date, datetime

from app import logic as L

S = dict(L.DEFAULT_SETTINGS)
CAL = L.WorkCalendar()


def dt(s):
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------- рабочее время

def test_add_work_hours_same_day():
    assert CAL.add_work_hours(dt("2026-11-03 10:00"), 2) == dt("2026-11-03 12:00")


def test_request_at_1730_due_next_day_1030():
    # вторник 17:30 → среда 10:30
    assert CAL.add_work_hours(dt("2026-11-03 17:30"), 2) == dt("2026-11-04 10:30")


def test_friday_evening_rolls_to_monday():
    assert CAL.add_work_hours(dt("2026-11-06 17:30"), 2) == dt("2026-11-09 10:30")


def test_before_work_and_weekend():
    assert CAL.add_work_hours(dt("2026-11-03 07:00"), 2) == dt("2026-11-03 11:00")
    assert CAL.add_work_hours(dt("2026-11-07 12:00"), 2) == dt("2026-11-09 11:00")  # суббота


def test_holidays_are_skipped():
    cal = L.WorkCalendar(holidays=["2026-11-04"])
    assert cal.add_work_hours(dt("2026-11-03 17:30"), 2) == dt("2026-11-05 10:30")


def test_work_hours_between():
    assert CAL.work_hours_between(dt("2026-11-03 17:30"), dt("2026-11-04 10:30")) == 2
    assert CAL.work_hours_between(dt("2026-11-06 17:00"), dt("2026-11-09 10:00")) == 2


def test_add_work_days():
    # доставлен в пятницу 14:00 → 5 рабочих дней → следующая пятница 14:00
    assert CAL.add_work_days(dt("2026-11-06 14:00"), 5) == dt("2026-11-13 14:00")


def test_last_workdays():
    days = CAL.last_workdays(date(2026, 11, 9), 5, include_today=False)  # понедельник
    assert days == [date(2026, 11, d) for d in (2, 3, 4, 5, 6)]


# ---------------------------------------------------------------- метки

def trip(stage, times, **kw):
    t = {"id": 1, "stage": stage, "times": times, "carrier_id": 1, "price": 100_000, "carrier_rate": 80_000}
    t.update(kw)
    return t


def codes(t, now):
    return {f["code"] for f in L.trip_flags(t, S, CAL, dt(now))}


def test_rate_late_after_two_work_hours():
    t = trip(1, {1: "2026-11-03 10:00:00"}, carrier_id=None, price=None, carrier_rate=None)
    assert "RATE_LATE" not in codes(t, "2026-11-03 12:00")
    assert "RATE_LATE" in codes(t, "2026-11-03 12:01")


def test_rate_late_request_at_1730():
    t = trip(1, {1: "2026-11-03 17:30:00"}, carrier_id=None, price=None, carrier_rate=None)
    assert "RATE_LATE" not in codes(t, "2026-11-04 10:30")
    assert "RATE_LATE" in codes(t, "2026-11-04 10:31")


def test_no_contact_13_hours():
    t = trip(7, {6: "2026-11-02 08:00:00", 7: "2026-11-02 09:00:00"}, last_contact_at=None)
    assert "NO_CONTACT" in codes(t, "2026-11-02 22:00")  # 13 ч
    assert "NO_CONTACT" not in codes(t, "2026-11-02 20:00")  # 11 ч
    t["last_contact_at"] = "2026-11-02 21:00:00"
    assert "NO_CONTACT" not in codes(t, "2026-11-02 22:00")


def test_close_late_after_5_work_days():
    t = trip(8, {8: "2026-11-02 12:00:00"})  # понедельник
    assert "CLOSE_LATE" not in codes(t, "2026-11-09 11:00")  # 5 раб. дней ещё не прошло
    assert "CLOSE_LATE" in codes(t, "2026-11-10 12:00")  # 6 рабочих дней


def test_low_margin_and_no_carrier():
    t = trip(4, {}, carrier_id=None, price=100_000, carrier_rate=95_000)
    c = codes(t, "2026-11-03 12:00")
    assert {"LOW_MARGIN", "NO_CARRIER"} <= c
    flags = L.trip_flags(t, S, CAL, dt("2026-11-03 12:00"))
    assert not L.is_overdue(flags)  # оранжевые метки — не просрочка


def test_required_fields():
    t = {"origin": "A", "destination": "B", "body_type": "Тент", "client_id": 1, "employee_id": 1,
         "client_type": "Разовый"}
    assert L.missing_fields(t, 1) == []
    assert L.missing_fields(t, 2) == ["Стоимость перевозки"]
    assert set(L.missing_fields(t, 6)) == {"Стоимость перевозки", "Перевозчик", "Ставка перевозчика",
                                           "Дата отправки"}


def test_status_1c():
    assert [L.status_1c(s) for s in (5, 6, 7, 8, 9, 10)] == ["", "В пути", "В пути", "Доставлен", "Доставлен",
                                                             "Доставлен"]


def test_reg_numbers_by_ship_date():
    trips = [{"id": 1, "stage": 10, "ship_date": "2026-10-05"}, {"id": 2, "stage": 10, "ship_date": "2026-10-01"},
             {"id": 3, "stage": 0, "received_at": "2026-10-02 10:00:00"},
             {"id": 4, "stage": 1, "received_at": "2026-10-03 10:00:00"}]
    assert L.assign_reg_numbers(trips) == {2: 1, 4: 2, 1: 3}


def test_report_color():
    assert L.report_color(None, S) == "none"
    assert L.report_color({"calls": 15, "proposals": 3}, S) == "green"
    assert L.report_color({"calls": 8, "proposals": 0}, S) == "yellow"
    assert L.report_color({"calls": 7, "proposals": 5}, S) == "red"


def test_lead_flags():
    lead = {"stage": 1, "times": {1: "2026-10-01 10:00:00"}, "next_step_date": "2026-11-01"}
    c = {f["code"] for f in L.lead_flags(lead, S, dt("2026-11-03 10:00"))}
    assert c == {"STUCK", "MISSED_STEP"}


def test_norm_key():
    assert L.norm_key("Coca Cola ") == L.norm_key("  coca   COLA")
    assert L.person_key("Жексебаев М.") == L.person_key("жексебаев м")
