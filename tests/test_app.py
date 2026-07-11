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
    assert b"already booked on this date" in second.data
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
    assert b"valid booking date" in response.data


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


def test_post_without_csrf_token_rejected(csrf_app):
    client = csrf_app.test_client()
    response = client.post("/book", data=booking_form())
    assert response.status_code == 400
    assert b"CSRF" in response.data


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
