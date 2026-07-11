import sqlite3
from datetime import date, timedelta

import pytest

from app import create_app, connect_db

FUTURE = (date.today() + timedelta(days=10)).isoformat()
ADMIN_TOKEN = "test-admin-token"


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "ADMIN_TOKEN": ADMIN_TOKEN,
            "SECRET_KEY": "test-secret",
            "SMTP_HOST": None,  # email intentionally unconfigured in tests
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


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
    assert "error=unavailable" in second.headers["Location"]
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 1


def test_past_date_rejected(client):
    response = client.post("/book", data=booking_form(booking_date="2020-01-01"))
    assert "error=past-date" in response.headers["Location"]


def test_invalid_date_rejected(client):
    response = client.post("/book", data=booking_form(booking_date="not-a-date"))
    assert "error=invalid-date" in response.headers["Location"]


def test_missing_contact_rejected(app, client):
    response = client.post("/book", data=booking_form(customer_name=""))
    assert "error=contact" in response.headers["Location"]
    response = client.post("/book", data=booking_form(customer_email="no-at-sign"))
    assert "error=contact" in response.headers["Location"]
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 0


def test_unknown_kayak_rejected(client):
    response = client.post("/book", data=booking_form(kayak_id="999"))
    assert "error=unavailable" in response.headers["Location"]


def test_booking_references_are_unique(app, client):
    for i in range(3):
        day = (date.today() + timedelta(days=20 + i)).isoformat()
        client.post("/book", data=booking_form(booking_date=day))
    refs = [row[0] for row in db_rows(app, "SELECT booking_reference FROM bookings")]
    assert len(refs) == len(set(refs)) == 3


# --- Admin ---------------------------------------------------------------


def test_admin_requires_token(client):
    assert client.get("/admin").status_code == 401
    assert client.get("/admin?token=wrong").status_code == 401
    assert client.get(f"/admin?token={ADMIN_TOKEN}").status_code == 200


def test_admin_update_conflict_rejected(app, client):
    day_a = (date.today() + timedelta(days=5)).isoformat()
    day_b = (date.today() + timedelta(days=6)).isoformat()
    client.post("/book", data=booking_form(booking_date=day_a))
    client.post("/book", data=booking_form(booking_date=day_b, customer_name="Second"))
    second_id = db_rows(app, "SELECT id FROM bookings WHERE customer_name = 'Second'")[0][0]

    # Try to move the second booking onto the first booking's day.
    client.post(
        f"/admin/bookings/{second_id}/update?token={ADMIN_TOKEN}",
        data=booking_form(booking_date=day_a, customer_name="Second"),
    )
    assert db_rows(app, "SELECT start_date FROM bookings WHERE id = ?", (second_id,))[0][0] == day_b


def test_cancelled_booking_frees_the_date(app, client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    client.post(
        f"/admin/bookings/{booking_id}/update?token={ADMIN_TOKEN}",
        data=booking_form(status="cancelled"),
    )
    days = client.get(f"/api/availability?kayak_id=1&start={FUTURE}&days=1").get_json()
    assert days[0]["available"] is True


def test_resend_email_records_failure_without_smtp(app, client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    assert client.post(f"/admin/bookings/{booking_id}/resend-email").status_code == 401
    response = client.post(f"/admin/bookings/{booking_id}/resend-email?token={ADMIN_TOKEN}")
    assert response.status_code == 302
    row = db_rows(app, "SELECT email_status, email_error FROM bookings WHERE id = ?", (booking_id,))[0]
    assert row["email_status"] == "failed"
    assert "SMTP is not configured" in row["email_error"]


def test_admin_mutations_require_token(app, client):
    client.post("/book", data=booking_form())
    booking_id = db_rows(app, "SELECT id FROM bookings")[0][0]
    assert client.post(f"/admin/bookings/{booking_id}/delete").status_code == 401
    assert client.post(f"/admin/bookings/{booking_id}/update", data=booking_form()).status_code == 401
    assert db_rows(app, "SELECT COUNT(*) FROM bookings")[0][0] == 1
