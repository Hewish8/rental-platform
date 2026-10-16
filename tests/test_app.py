import sqlite3
from datetime import date, timedelta

import pytest

from app import create_app, connect_db

FUTURE = (date.today() + timedelta(days=10)).isoformat()
OWNER_EMAIL = "owner@test.mu"
OWNER_PASSWORD = "test-owner-password"


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "SECRET_KEY": "test-secret",
            "SMTP_HOST": None,  # email intentionally unconfigured in tests
            "OWNER_EMAIL": OWNER_EMAIL,
            "OWNER_PASSWORD": OWNER_PASSWORD,
            "CSRF_ENABLED": False,  # re-enabled in the dedicated CSRF tests
            "UPLOAD_DIR": tmp_path / "uploads",
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    client.post("/admin/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    return client


def booking_form(**overrides):
    form = {
        "kayak_id": "1",
        "booking_date": FUTURE,
        "customer_name": "Jane Visitor",
        "customer_email": "jane@example.com",
        "customer_phone": "+230 5000 0000",
        "notes": "",
    }
    form.update(overrides)
    return form


def db_rows(app, query, params=()):
    with connect_db(app.config["DATABASE"]) as db:
        return db.execute(query, params).fetchall()


# --- Pages ---------------------------------------------------------------


def test_index_lists_kayaks(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Lagoon Explorer" in response.data


def test_fresh_db_seeds_three_kayaks(app):
    rows = db_rows(app, "SELECT COUNT(*) FROM kayaks")
    assert rows[0][0] == 3


def test_seed_does_not_overwrite_owner_edits(app, tmp_path):
    with connect_db(app.config["DATABASE"]) as db:
        db.execute("UPDATE kayaks SET price_per_day = 5555 WHERE id = 1")
        db.commit()
    create_app(
        {
            "TESTING": True,
            "DATABASE": app.config["DATABASE"],
            "SECRET_KEY": "test-secret",
        }
    )
    assert db_rows(app, "SELECT price_per_day FROM kayaks WHERE id = 1")[0][0] == 5555


# --- Availability API ----------------------------------------------------


def test_availability_returns_days(client):
    response = client.get(f"/api/availability?kayak_id=1&start={FUTURE}&days=7")
    assert response.status_code == 200
    days = response.get_json()
    assert len(days) == 7
    assert all(day["available"] for day in days)


def test_availability_rejects_bad_input(client):
    assert client.get("/api/availability?kayak_id=abc").status_code == 400
    assert client.get("/api/availability?start=garbage").status_code == 400
    assert client.get("/api/availability?days=x").status_code == 400
    assert client.get("/api/availability?kayak_id=999").status_code == 404


def test_booked_day_shows_unavailable(client):
    client.post("/book", data=booking_form())
    days = client.get(f"/api/availability?kayak_id=1&start={FUTURE}&days=1").get_json()
    assert days[0]["available"] is False
    # Other kayaks unaffected
    days = client.get(f"/api/availability?kayak_id=2&start={FUTURE}&days=1").get_json()
    assert days[0]["available"] is True


# --- Booking -------------------------------------------------------------


def test_booking_happy_path(app, client):
    response = client.post("/book", data=booking_form())
    assert response.status_code == 302
    assert "/booking/MLK-" in response.headers["Location"]

    rows = db_rows(app, "SELECT * FROM bookings")
    assert len(rows) == 1
    booking = rows[0]
    assert booking["customer_name"] == "Jane Visitor"
    assert booking["status"] == "confirmed"
    assert booking["total_price"] == 1200
    # SMTP is not configured in tests, so email must be marked failed, not crash.
    assert booking["email_status"] == "failed"

    reference = response.headers["Location"].rsplit("/", 1)[-1]
    page = client.get(f"/booking/{reference}")
    assert page.status_code == 200
    assert reference.encode() in page.data


def test_double_booking_rejected(app, client):
    first = client.post("/book", data=booking_form())
    assert "/booking/" in first.headers["Location"]
    second = client.post("/book", data=booking_form(customer_name="Other Person"))
    assert second.status_code == 400
    assert b"already booked on one of those dates" in second.data
    # Rejected form keeps what the visitor typed
    assert b"Other Person" in second.data
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 1


def test_past_date_rejected(client):
    response = client.post("/book", data=booking_form(booking_date="2020-01-01"))
    assert response.status_code == 400
    assert b"already passed" in response.data


def test_invalid_date_rejected(client):
    response = client.post("/book", data=booking_form(booking_date="not-a-date"))
    assert response.status_code == 400
    assert b"valid dates from the calendar" in response.data


def test_missing_contact_rejected(app, client):
    response = client.post("/book", data=booking_form(customer_name=""))
    assert response.status_code == 400
    assert b"full name" in response.data
    response = client.post("/book", data=booking_form(customer_email="no-at-sign"))
    assert response.status_code == 400
    assert b"valid email address" in response.data
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 0


def test_unknown_kayak_rejected(client):
    response = client.post("/book", data=booking_form(kayak_id="999"))
    assert response.status_code == 400
    assert b"no longer available" in response.data


def test_conflict_offers_nearest_available_dates(client):
    client.post("/book", data=booking_form())
    response = client.post("/book", data=booking_form(customer_name="Second Person"))
    assert response.status_code == 400
    assert b"date-chip" in response.data
    assert response.data.count(b"date-chip") >= 3


def test_booking_references_are_unique(app, client):
    for i in range(3):
        day = (date.today() + timedelta(days=20 + i)).isoformat()
        client.post("/book", data=booking_form(booking_date=day))
    refs = [row[0] for row in db_rows(app, "SELECT booking_reference FROM bookings")]
    assert len(refs) == len(set(refs)) == 3


# --- Admin ---------------------------------------------------------------


def test_admin_requires_login(client, admin_client):
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]
    assert admin_client.get("/admin").status_code == 200


def test_login_rejects_bad_credentials(client):
    response = client.post(
        "/admin/login", data={"email": OWNER_EMAIL, "password": "wrong"}, follow_redirects=True
    )
    assert b"Wrong email or password" in response.data
    assert client.get("/admin").status_code == 302


def test_logout_ends_session(admin_client):
    admin_client.post("/admin/logout")
    assert admin_client.get("/admin").status_code == 302


def test_admin_update_conflict_rejected(app, client, admin_client):
    day_a = (date.today() + timedelta(days=5)).isoformat()
    day_b = (date.today() + timedelta(days=6)).isoformat()
    client.post("/book", data=booking_form(booking_date=day_a))
    client.post("/book", data=booking_form(booking_date=day_b, customer_name="Second"))
    second_id = db_rows(app, "SELECT id FROM bookings WHERE customer_name = 'Second'")[0][0]

    # Try to move the second booking onto the first booking's day.
    admin_client.post(
        f"/admin/bookings/{second_id}/update",
        data=booking_form(booking_date=day_a, customer_name="Second"),
    )
    assert db_rows(app, "SELECT start_date FROM bookings WHERE id = ?", (second_id,))[0][0] == day_b


def test_cancelled_booking_frees_the_date(app, client, admin_client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    admin_client.post(
        f"/admin/bookings/{booking_id}/update",
        data=booking_form(status="cancelled"),
    )
    days = client.get(f"/api/availability?kayak_id=1&start={FUTURE}&days=1").get_json()
    assert days[0]["available"] is True


def test_resend_email_records_failure_without_smtp(app, client, admin_client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    assert client.post(f"/admin/bookings/{booking_id}/resend-email").status_code == 302
    response = admin_client.post(f"/admin/bookings/{booking_id}/resend-email")
    assert response.status_code == 302
    row = db_rows(app, "SELECT email_status, email_error FROM bookings WHERE id = ?", (booking_id,))[0]
    assert row["email_status"] == "failed"
    assert "SMTP is not configured" in row["email_error"]


def test_cancel_records_reason_and_frees_date(app, client, admin_client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    admin_client.post(f"/admin/bookings/{booking_id}/cancel", data={"reason": "weather"})
    row = db_rows(app, "SELECT status, cancelled_reason FROM bookings WHERE id = ?", (booking_id,))[0]
    assert row["status"] == "cancelled"
    assert row["cancelled_reason"] == "weather"
    days = client.get(f"/api/availability?kayak_id=1&start={FUTURE}&days=1").get_json()
    assert days[0]["available"] is True


def test_no_hard_delete_route(client):
    # Bookings are never hard-deleted anymore.
    assert client.post("/admin/bookings/1/delete").status_code == 404


def test_admin_mutations_require_login(app, client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    assert client.post(f"/admin/bookings/{booking_id}/cancel").status_code == 302
    assert client.post(f"/admin/bookings/{booking_id}/update", data=booking_form()).status_code == 302
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 1


def test_audit_log_records_actions(app, client, admin_client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    admin_client.post(f"/admin/bookings/{booking_id}/cancel", data={"reason": "test"})
    entries = db_rows(app, "SELECT user_email, action FROM audit_log ORDER BY id")
    actions = [(e["user_email"], e["action"]) for e in entries]
    assert ("customer", "created") in actions
    assert (OWNER_EMAIL, "cancelled") in actions
    page = admin_client.get("/admin/audit")
    assert page.status_code == 200
    assert b"cancelled" in page.data


def test_payment_recording_and_due_calculation(app, client, admin_client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]

    admin_client.post(f"/admin/bookings/{booking_id}/payments", data={"amount": "700", "method": "cash"})
    page = admin_client.get("/admin")
    assert b"Due Rs 500" in page.data

    admin_client.post(f"/admin/bookings/{booking_id}/payments", data={"amount": "500", "method": "card"})
    page = admin_client.get("/admin")
    assert b">Paid<" in page.data

    admin_client.post(
        f"/admin/bookings/{booking_id}/payments",
        data={"amount": "1200", "method": "cash", "kind": "refund", "note": "weather cancel"},
    )
    page = admin_client.get("/admin")
    assert b"Due Rs 1200" in page.data

    rejected = admin_client.post(f"/admin/bookings/{booking_id}/payments", data={"amount": "-5"})
    assert rejected.status_code == 302
    assert db_rows(app, "SELECT COUNT(*) FROM payments")[0][0] == 3


def test_bookings_list_filters_and_search(app, client, admin_client):
    client.post("/book", data=booking_form())
    client.post("/book", data=booking_form(
        booking_date=(date.today() + timedelta(days=12)).isoformat(),
        customer_name="Zara Search-Target", kayak_id="2",
    ))
    page = admin_client.get("/admin")
    assert b"Zara Search-Target" in page.data and b"Jane Visitor" in page.data
    page = admin_client.get("/admin?view=upcoming&q=Zara")
    assert b"Zara Search-Target" in page.data and b"Jane Visitor" not in page.data
    page = admin_client.get("/admin?view=cancelled")
    assert b"No bookings match" in page.data
    page = admin_client.get("/admin?view=unpaid")
    assert b"Zara Search-Target" in page.data


def test_booking_detail_page(app, client, admin_client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    page = admin_client.get(f"/admin/bookings/{booking_id}")
    assert page.status_code == 200
    assert b"Jane Visitor" in page.data
    assert b"Edit booking" in page.data
    assert b"Payments" in page.data
    # POST with next returns to the detail page
    response = admin_client.post(
        f"/admin/bookings/{booking_id}/payments",
        data={"amount": "1200", "method": "cash", "next": f"/admin/bookings/{booking_id}"},
    )
    assert response.headers["Location"] == f"/admin/bookings/{booking_id}"
    page = admin_client.get(f"/admin/bookings/{booking_id}")
    assert b"Rs 1200 received" in page.data


def test_bookings_csv_export(app, client, admin_client):
    client.post("/book", data=booking_form())
    response = admin_client.get("/admin/bookings.csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["Content-Type"]
    body = response.data.decode()
    assert body.splitlines()[0].startswith("reference,")
    assert "Jane Visitor" in body


def test_today_screen_and_lifecycle(app, client, admin_client):
    today_iso = date.today().isoformat()
    client.post("/book", data=booking_form(booking_date=today_iso))
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]

    page = admin_client.get("/admin/today")
    assert page.status_code == 200
    assert b"Jane Visitor" in page.data
    assert "Check out →".encode() in page.data

    admin_client.post(f"/admin/bookings/{booking_id}/checkout")
    row = db_rows(app, "SELECT status, checked_out_at, expected_return FROM bookings WHERE id = ?", (booking_id,))[0]
    assert row["status"] == "on_water"
    assert row["checked_out_at"] and row["expected_return"]

    # A checkout on an already on-water booking is rejected
    admin_client.post(f"/admin/bookings/{booking_id}/checkout")
    assert db_rows(app, "SELECT COUNT(*) FROM audit_log WHERE action = 'checked-out'")[0][0] == 1

    admin_client.post(f"/admin/bookings/{booking_id}/checkin")
    row = db_rows(app, "SELECT status, checked_in_at FROM bookings WHERE id = ?", (booking_id,))[0]
    assert row["status"] == "returned"
    assert row["checked_in_at"]


def test_late_return_flagged(app, client, admin_client):
    today_iso = date.today().isoformat()
    client.post("/book", data=booking_form(booking_date=today_iso))
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    admin_client.post(f"/admin/bookings/{booking_id}/checkout")
    # Force the expected return into the past
    from app import connect_db as _connect
    with _connect(app.config["DATABASE"]) as db:
        db.execute("UPDATE bookings SET expected_return = '2020-01-01T10:00:00+04:00' WHERE id = ?", (booking_id,))
        db.commit()
    page = admin_client.get("/admin/today")
    assert b"Expected back" in page.data


def test_login_lands_on_today(client):
    response = client.post("/admin/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert response.headers["Location"].endswith("/admin/today")


def test_walkup_booking_without_email(app, admin_client):
    response = admin_client.post("/admin/bookings/new", data={
        "kayak_id": "1", "booking_date": FUTURE, "customer_name": "Beach Walkup",
        "customer_email": "", "customer_phone": "+230 5999 0000",
        "party_size": "2", "amount": "1200", "method": "cash",
    })
    assert response.status_code == 302
    row = db_rows(app, "SELECT * FROM bookings WHERE customer_name = 'Beach Walkup'")[0]
    assert row["source"] == "walkup"
    assert row["party_size"] == 2
    assert row["email_status"] == "skipped"
    paid = db_rows(app, "SELECT SUM(amount) FROM payments WHERE booking_id = ?", (row["id"],))[0][0]
    assert paid == 1200


def test_walkup_conflict_rejected(app, client, admin_client):
    client.post("/book", data=booking_form())
    response = admin_client.post("/admin/bookings/new", data={
        "kayak_id": "1", "booking_date": FUTURE, "customer_name": "Second",
    })
    assert response.status_code == 200
    assert b"already booked" in response.data
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 1


def test_walkup_requires_login(client):
    assert client.get("/admin/bookings/new").status_code == 302


def test_fleet_page_and_kayak_crud(app, admin_client, client):
    page = admin_client.get("/admin/fleet")
    assert page.status_code == 200
    assert b"Lagoon Explorer" in page.data

    # Add a kayak
    admin_client.post("/admin/fleet/new", data={
        "name": "Reef Runner", "tagline": "Test kayak", "capacity": "1 adult", "price_per_day": "999",
    })
    assert db_rows(app, "SELECT COUNT(*) FROM kayaks")[0][0] == 4
    public = client.get("/")
    assert b"Reef Runner" in public.data

    # Edit price
    kayak_id = db_rows(app, "SELECT id FROM kayaks WHERE name = 'Reef Runner'")[0][0]
    admin_client.post(f"/admin/fleet/{kayak_id}/save", data={
        "name": "Reef Runner", "tagline": "Test kayak", "capacity": "1 adult", "price_per_day": "1500",
    })
    assert db_rows(app, "SELECT price_per_day FROM kayaks WHERE id = ?", (kayak_id,))[0][0] == 1500

    # Retire hides from public site and availability API
    admin_client.post(f"/admin/fleet/{kayak_id}/status", data={"status": "retired"})
    public = client.get("/")
    assert b"Reef Runner" not in public.data
    assert client.get(f"/api/availability?kayak_id={kayak_id}").status_code == 404


def test_unusable_damage_blocks_bookings(app, admin_client, client):
    admin_client.post("/admin/fleet/1/damage", data={"description": "Cracked hull", "severity": "unusable"})
    row = db_rows(app, "SELECT status FROM kayaks WHERE id = 1")[0]
    assert row["status"] == "maintenance"
    # Public booking for that kayak is rejected
    response = client.post("/book", data=booking_form(kayak_id="1"))
    assert response.status_code == 400
    # Resolving damage does not auto-reactivate (owner decides)
    report_id = db_rows(app, "SELECT id FROM damage_reports")[0][0]
    admin_client.post(f"/admin/damage/{report_id}/resolve")
    assert db_rows(app, "SELECT status FROM damage_reports WHERE id = ?", (report_id,))[0][0] == "resolved"


def test_kayak_photo_upload_validation(app, admin_client):
    import io as _io
    # Wrong extension rejected
    admin_client.post("/admin/fleet/1/save", data={
        "name": "Lagoon Explorer", "tagline": "x", "capacity": "1 adult", "price_per_day": "1200",
        "photo": (_io.BytesIO(b"MZ fake exe"), "malware.exe"),
    }, content_type="multipart/form-data")
    assert db_rows(app, "SELECT image_url FROM kayaks WHERE id = 1")[0][0] == "/assets/KayakTurquoise.jpg"

    # Valid jpg accepted
    admin_client.post("/admin/fleet/1/save", data={
        "name": "Lagoon Explorer", "tagline": "x", "capacity": "1 adult", "price_per_day": "1200",
        "photo": (_io.BytesIO(b"\xff\xd8\xff" + b"0" * 100), "new-photo.jpg"),
    }, content_type="multipart/form-data")
    url = db_rows(app, "SELECT image_url FROM kayaks WHERE id = 1")[0][0]
    assert url.startswith("/assets/kayaks/") and url.endswith(".jpg")


def test_maintenance_log_and_today_reminder(app, admin_client):
    admin_client.post("/admin/fleet/1/maintenance", data={
        "description": "Hull inspection", "cost": "500", "next_due": "2020-01-01",
    })
    rows = db_rows(app, "SELECT description, cost, next_due FROM maintenance_log")
    assert rows[0]["description"] == "Hull inspection"
    page = admin_client.get("/admin/today")
    assert b"Maintenance due" in page.data


def test_resource_calendar(app, client, admin_client):
    client.post("/book", data=booking_form())
    page = admin_client.get(f"/admin/calendar?start={FUTURE}")
    assert page.status_code == 200
    assert b"Jane Visitor" in page.data
    # Empty-cell link prefills the new-booking form
    form_page = admin_client.get(f"/admin/bookings/new?kayak_id=2&date={FUTURE}")
    assert f'value="{FUTURE}"'.encode() in form_page.data
    # Garbage start falls back to today instead of erroring
    assert admin_client.get("/admin/calendar?start=nonsense").status_code == 200


def test_customers_view_aggregates(app, client, admin_client):
    client.post("/book", data=booking_form())
    client.post("/book", data=booking_form(
        booking_date=(date.today() + timedelta(days=11)).isoformat()
    ))
    client.post("/book", data=booking_form(
        booking_date=(date.today() + timedelta(days=12)).isoformat(),
        customer_name="Other Person", customer_email="other@x.mu", customer_phone="+230 5111 1111",
    ))
    page = admin_client.get("/admin/customers")
    assert page.status_code == 200
    assert b"Jane Visitor" in page.data and b"Other Person" in page.data
    assert b"Rs 2400" in page.data  # two visits aggregated

    # Note + flag round-trips
    admin_client.post("/admin/customers/note", data={
        "identity": "+23050000000", "note": "prefers tandem", "flagged": "on",
    })
    page = admin_client.get("/admin/customers")
    assert b"prefers tandem" in page.data


def test_reports_page(app, client, admin_client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    admin_client.post(f"/admin/bookings/{booking_id}/payments", data={"amount": "1200", "method": "cash"})
    month = FUTURE[:7]
    page = admin_client.get(f"/admin/reports?month={month}")
    assert page.status_code == 200
    assert b"Rs 1200" in page.data
    assert b"Lagoon Explorer" in page.data
    assert admin_client.get("/admin/reports?month=garbage").status_code == 200


# --- Multi-day bookings ------------------------------------------------------


def range_form(days=3, offset=10, **overrides):
    start = date.today() + timedelta(days=offset)
    end = start + timedelta(days=days - 1)
    form = booking_form()
    del form["booking_date"]
    form["start_date"] = start.isoformat()
    form["end_date"] = end.isoformat()
    form.update(overrides)
    return form


def test_multiday_booking_prices_and_blocks(app, client):
    response = client.post("/book", data=range_form(days=3))
    assert response.status_code == 302
    row = db_rows(app, "SELECT start_date, end_date, total_price FROM bookings")[0]
    assert row["total_price"] == 3600  # 1200 x 3 days
    # Every day of the range is now unavailable
    days = client.get(f"/api/availability?kayak_id=1&start={row['start_date']}&days=3").get_json()
    assert all(not day["available"] for day in days)


def test_overlapping_range_rejected(app, client):
    client.post("/book", data=range_form(days=3, offset=10))
    # Overlaps the middle day
    response = client.post("/book", data=range_form(days=3, offset=12, customer_name="Overlap"))
    assert response.status_code == 400
    assert b"already booked on one of those dates" in response.data
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 1


def test_range_cap_and_swap(app, client):
    response = client.post("/book", data=range_form(days=20))
    assert response.status_code == 400
    assert b"limited to 14 days" in response.data

    # Reversed dates are swapped, not rejected
    start = (date.today() + timedelta(days=12)).isoformat()
    end = (date.today() + timedelta(days=10)).isoformat()
    form = booking_form()
    del form["booking_date"]
    form["start_date"], form["end_date"] = start, end
    response = client.post("/book", data=form)
    assert response.status_code == 302
    row = db_rows(app, "SELECT start_date, end_date, total_price FROM bookings")[0]
    assert row["start_date"] == end and row["end_date"] == start
    assert row["total_price"] == 3600


def test_admin_walkup_range(app, admin_client):
    start = (date.today() + timedelta(days=20)).isoformat()
    end = (date.today() + timedelta(days=21)).isoformat()
    admin_client.post("/admin/bookings/new", data={
        "kayak_id": "1", "booking_date": start, "end_date": end, "customer_name": "Range Walkup",
    })
    row = db_rows(app, "SELECT start_date, end_date, total_price FROM bookings WHERE customer_name = 'Range Walkup'")[0]
    assert row["end_date"] == end
    assert row["total_price"] == 2400


# --- Manage my booking -------------------------------------------------------


def get_reference(app):
    return db_rows(app, "SELECT booking_reference FROM bookings")[0][0]


def test_manage_lookup_requires_matching_email(app, client):
    client.post("/book", data=booking_form())
    reference = get_reference(app)
    wrong = client.post("/manage", data={"reference": reference, "email": "attacker@evil.com"})
    assert b"No booking found" in wrong.data
    assert b"Jane Visitor" not in wrong.data
    found = client.post("/manage", data={"reference": reference.lower(), "email": "JANE@example.com"})
    assert b"Cancel this booking" in found.data


def test_customer_can_cancel_future_booking(app, client):
    client.post("/book", data=booking_form())
    reference = get_reference(app)
    response = client.post(
        "/manage/cancel",
        data={"reference": reference, "email": "jane@example.com"},
        follow_redirects=True,
    )
    assert b"is cancelled" in response.data
    row = db_rows(app, "SELECT status, cancelled_reason FROM bookings")[0]
    assert row["status"] == "cancelled"
    assert "customer online" in row["cancelled_reason"]
    days = client.get(f"/api/availability?kayak_id=1&start={FUTURE}&days=1").get_json()
    assert days[0]["available"] is True


def test_customer_cannot_cancel_same_day_or_past(app, client, admin_client):
    today_iso = date.today().isoformat()
    client.post("/book", data=booking_form(booking_date=today_iso))
    reference = get_reference(app)
    page = client.post("/manage", data={"reference": reference, "email": "jane@example.com"})
    assert b"no longer be cancelled online" in page.data
    response = client.post(
        "/manage/cancel",
        data={"reference": reference, "email": "jane@example.com"},
        follow_redirects=True,
    )
    assert b"no longer be cancelled online" in response.data
    assert db_rows(app, "SELECT status FROM bookings")[0][0] == "confirmed"


# --- Promo codes -----------------------------------------------------------


def make_promo(admin_client, **overrides):
    data = {"code": "HOTEL10", "kind": "percent", "value": "10"}
    data.update(overrides)
    return admin_client.post("/admin/promos/new", data=data)


def test_promo_applies_discount(app, client, admin_client):
    make_promo(admin_client)
    response = client.post("/book", data=booking_form(promo_code="hotel10"))
    assert response.status_code == 302
    row = db_rows(app, "SELECT total_price, discount, promo_code FROM bookings")[0]
    assert row["promo_code"] == "HOTEL10"
    assert row["discount"] == 120
    assert row["total_price"] == 1080
    assert db_rows(app, "SELECT uses FROM promo_codes")[0][0] == 1


def test_invalid_promo_rejected_but_form_kept(app, client, admin_client):
    response = client.post("/book", data=booking_form(promo_code="NOPE"))
    assert response.status_code == 400
    assert b"not valid" in response.data
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 0


def test_promo_use_limit_and_toggle(app, client, admin_client):
    make_promo(admin_client, code="ONCE", max_uses="1")
    client.post("/book", data=booking_form(promo_code="ONCE"))
    second = client.post("/book", data=booking_form(
        promo_code="ONCE", booking_date=(date.today() + timedelta(days=15)).isoformat()
    ))
    assert second.status_code == 400
    assert b"fully used" in second.data

    make_promo(admin_client, code="PAUSED", value="20")
    promo_id = db_rows(app, "SELECT id FROM promo_codes WHERE code = 'PAUSED'")[0][0]
    admin_client.post(f"/admin/promos/{promo_id}/toggle")
    check = client.get("/api/promo?code=PAUSED").get_json()
    assert check["valid"] is False


def test_promo_api(client, admin_client):
    make_promo(admin_client, code="FIXED500", kind="fixed", value="500")
    data = client.get("/api/promo?code=FIXED500").get_json()
    assert data == {"valid": True, "kind": "fixed", "value": 500, "code": "FIXED500"}
    data = client.get("/api/promo?code=unknown").get_json()
    assert data["valid"] is False


# --- Roles & settings --------------------------------------------------------


@pytest.fixture
def staff_client(app, admin_client):
    admin_client.post("/admin/users/new", data={
        "email": "staff@test.mu", "password": "staff-password-1", "role": "staff",
    })
    client = app.test_client()
    client.post("/admin/login", data={"email": "staff@test.mu", "password": "staff-password-1"})
    return client


def test_staff_can_run_the_day(app, client, staff_client):
    today_iso = date.today().isoformat()
    client.post("/book", data=booking_form(booking_date=today_iso))
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]

    assert staff_client.get("/admin/today").status_code == 200
    assert staff_client.get("/admin").status_code == 200
    assert staff_client.get("/admin/calendar").status_code == 200
    staff_client.post(f"/admin/bookings/{booking_id}/checkout")
    assert db_rows(app, "SELECT status FROM bookings WHERE id = ?", (booking_id,))[0][0] == "on_water"
    staff_client.post(f"/admin/bookings/{booking_id}/payments", data={"amount": "1200", "method": "cash"})
    assert db_rows(app, "SELECT COUNT(*) FROM payments")[0][0] == 1


def test_staff_blocked_from_owner_pages(staff_client):
    for path in ["/admin/fleet", "/admin/reports", "/admin/promos", "/admin/customers",
                 "/admin/audit", "/admin/users", "/admin/settings"]:
        response = staff_client.get(path)
        assert response.status_code == 302, path
        assert "/admin/today" in response.headers["Location"], path
    # Owner-only mutations too
    response = staff_client.post("/admin/fleet/1/status", data={"status": "retired"})
    assert response.status_code == 302
    assert "/admin/today" in response.headers["Location"]


def test_user_management(app, admin_client):
    admin_client.post("/admin/users/new", data={
        "email": "helper@test.mu", "password": "helper-pass-123", "role": "staff",
    })
    user_id = db_rows(app, "SELECT id FROM users WHERE email = 'helper@test.mu'")[0][0]
    admin_client.post(f"/admin/users/{user_id}/toggle")
    assert db_rows(app, "SELECT active FROM users WHERE id = ?", (user_id,))[0][0] == 0
    # Deactivated staff cannot sign in
    client = app.test_client()
    client.post("/admin/login", data={"email": "helper@test.mu", "password": "helper-pass-123"})
    assert client.get("/admin/today").status_code == 302
    # Short password rejected
    admin_client.post("/admin/users/new", data={"email": "x@test.mu", "password": "short"})
    assert db_rows(app, "SELECT COUNT(*) FROM users WHERE email = 'x@test.mu'")[0][0] == 0


def test_settings_flow_to_public_site(app, client, admin_client):
    admin_client.post("/admin/settings", data={
        "whatsapp_number": "+230 5777 8888",
        "phone_display": "+230 5777 8888",
        "meeting_point": "Blue Bay public beach, kiosk 3",
        "hours_text": "Open daily 7:30 - 16:30",
    })
    page = client.get("/")
    assert b"Blue Bay public beach, kiosk 3" in page.data
    assert b"wa.me/23057778888" in page.data
    assert b"+230 5777 8888" in page.data


def next_weekday(weekday):
    """Next date (at least a week out) falling on the given weekday, Monday = 0."""
    start = date.today() + timedelta(days=7)
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def test_weekends_only_switch(app, client, admin_client):
    saturday, sunday, friday = next_weekday(5), next_weekday(6), next_weekday(4)
    admin_client.post("/admin/settings", data={"weekends_only": "1", "multi_day": "1"})
    assert db_rows(app, "SELECT value FROM settings WHERE key = 'weekends_only'")[0][0] == "1"

    days = client.get(f"/api/availability?kayak_id=1&start={friday.isoformat()}&days=3").get_json()
    assert [(d["available"], d["closed"]) for d in days] == [(False, True), (True, False), (True, False)]

    response = client.post("/book", data=booking_form(booking_date=friday.isoformat()))
    assert b"Saturdays and Sundays only" in response.data
    response = client.post("/book", data=booking_form(
        booking_date="", start_date=friday.isoformat(), end_date=sunday.isoformat()))
    assert b"Saturdays and Sundays only" in response.data
    assert client.post("/book", data=booking_form(
        booking_date="", start_date=saturday.isoformat(), end_date=sunday.isoformat())).status_code == 302

    # Unchecking the box (absent from the form) switches it off again.
    admin_client.post("/admin/settings", data={"multi_day": "1"})
    assert client.post("/book", data=booking_form(booking_date=friday.isoformat())).status_code == 302


def test_multi_day_switch(app, client, admin_client):
    saturday, sunday = next_weekday(5), next_weekday(6)
    two_days = booking_form(booking_date="", start_date=saturday.isoformat(), end_date=sunday.isoformat())
    assert b'data-multi-day="1"' in client.get("/").data

    admin_client.post("/admin/settings", data={})  # multi-day box unticked
    assert b'data-multi-day="0"' in client.get("/").data
    assert b"one day at a time" in client.post("/book", data=two_days).data
    assert client.post("/book", data=booking_form(booking_date=saturday.isoformat())).status_code == 302

    admin_client.post("/admin/settings", data={"multi_day": "1"})
    assert client.post("/book", data=dict(two_days, kayak_id="2")).status_code == 302


def test_change_password(app, admin_client):
    # Wrong current password rejected
    admin_client.post("/admin/account", data={
        "current_password": "wrong", "new_password": "new-password-123", "confirm_password": "new-password-123",
    })
    # Correct flow
    admin_client.post("/admin/account", data={
        "current_password": OWNER_PASSWORD, "new_password": "new-password-123", "confirm_password": "new-password-123",
    })
    fresh = app.test_client()
    fresh.post("/admin/login", data={"email": OWNER_EMAIL, "password": "new-password-123"})
    assert fresh.get("/admin/today").status_code == 200
    stale = app.test_client()
    stale.post("/admin/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert stale.get("/admin/today").status_code == 302


def test_login_rate_limit(app, client):
    import app as app_module
    app_module._LOGIN_ATTEMPTS.clear()
    for _ in range(5):
        client.post("/admin/login", data={"email": OWNER_EMAIL, "password": "wrong"})
    response = client.post("/admin/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert response.status_code == 429
    app_module._LOGIN_ATTEMPTS.clear()


def test_seo_meta_and_favicon(client):
    page = client.get("/")
    assert b'name="description"' in page.data
    assert b'property="og:title"' in page.data
    assert b'rel="icon"' in page.data
    # Admin pages are noindexed
    login = client.get("/admin/login")
    assert b'name="robots" content="noindex"' not in login.data  # login extends base, not admin_base


def test_friendly_404(client):
    response = client.get("/nonexistent-page")
    assert response.status_code == 404
    assert b"drifted off with the tide" in response.data
    assert b"Back to the site" in response.data


# --- CSRF ------------------------------------------------------------------


@pytest.fixture
def csrf_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "csrf.db",
            "SECRET_KEY": "test-secret",
            "SMTP_HOST": None,
            "OWNER_EMAIL": OWNER_EMAIL,
            "OWNER_PASSWORD": OWNER_PASSWORD,
            "CSRF_ENABLED": True,
        }
    )


def test_post_without_csrf_token_redirects_with_message(csrf_app):
    client = csrf_app.test_client()
    response = client.post("/book", data=booking_form())
    assert response.status_code == 302
    followed = client.post("/book", data=booking_form(), follow_redirects=True)
    assert b"had expired" in followed.data
    # Nothing was saved either time
    assert db_rows(csrf_app, "SELECT COUNT(*) FROM bookings")[0][0] == 0


def test_post_with_csrf_token_accepted(csrf_app):
    import re

    client = csrf_app.test_client()
    page = client.get("/")
    token = re.search(rb'name="_csrf" value="([^"]+)"', page.data).group(1).decode()
    form = booking_form()
    form["_csrf"] = token
    response = client.post("/book", data=form)
    assert response.status_code == 302
    assert "/booking/MLK-" in response.headers["Location"]
