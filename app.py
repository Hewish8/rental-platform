import os
import secrets
import sqlite3
import smtplib
from contextlib import closing
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from flask import Flask, flash, g, jsonify, redirect, render_template, request, send_from_directory, url_for


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "kayak_rental.db"


def load_env_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

OWNER_EMAIL = os.getenv("KAYAK_OWNER_EMAIL", "owner@example.com")
FROM_EMAIL = os.getenv("KAYAK_FROM_EMAIL", OWNER_EMAIL)
SMTP_HOST = os.getenv("KAYAK_SMTP_HOST")
SMTP_PORT = int(os.getenv("KAYAK_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("KAYAK_SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("KAYAK_SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("KAYAK_SMTP_USE_TLS", "true").lower() == "true"
ADMIN_TOKEN = os.getenv("KAYAK_ADMIN_TOKEN", "change-me")
DEBUG = os.getenv("KAYAK_DEBUG", "false").lower() == "true"

_PLACEHOLDER_SECRETS = {"", "dev-change-me", "change-this-to-a-long-random-secret"}
SECRET_KEY = os.getenv("KAYAK_SECRET_KEY", "")
if SECRET_KEY in _PLACEHOLDER_SECRETS and not DEBUG:
    raise RuntimeError(
        "KAYAK_SECRET_KEY is not set. Generate one with "
        "'python -c \"import secrets; print(secrets.token_urlsafe(32))\"' and put it in .env, "
        "or set KAYAK_DEBUG=true for local development."
    )

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY or secrets.token_urlsafe(32)


DEFAULT_KAYAKS = [
    {
        "name": "Lagoon Explorer",
        "tagline": "A stable sit-on-top kayak for calm turquoise water and scenic paddles.",
        "capacity": "1 adult",
        "price_per_day": 1200,
        "image_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Man%20paddling%20on%20kayak%20in%20turquoise%20water%20%2852829435254%29.jpg?width=1200",
    },
    {
        "name": "Aigrettes Cruiser",
        "tagline": "Light single kayak for relaxed paddles near Ile aux Aigrettes.",
        "capacity": "1 adult",
        "price_per_day": 1100,
        "image_url": "https://images.unsplash.com/photo-1571068316344-75bc76f77890?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "name": "Phares Tandem",
        "tagline": "Roomier kayak for two people heading toward the lighthouse views.",
        "capacity": "2 adults",
        "price_per_day": 1800,
        "image_url": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1200&q=80",
    },
]


def connect_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with closing(sqlite3.connect(DATABASE)) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS kayaks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                tagline TEXT NOT NULL,
                capacity TEXT NOT NULL,
                price_per_day INTEGER NOT NULL,
                image_url TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kayak_id INTEGER NOT NULL,
                booking_reference TEXT,
                customer_name TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                customer_phone TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                notes TEXT,
                total_price INTEGER,
                status TEXT NOT NULL DEFAULT 'confirmed',
                email_status TEXT NOT NULL DEFAULT 'pending',
                email_error TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (kayak_id) REFERENCES kayaks(id)
            )
            """
        )
        migrate_db(db)
        seed_kayaks(db)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bookings_kayak_dates ON bookings(kayak_id, start_date, end_date)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_reference ON bookings(booking_reference)"
        )
        db.commit()


def seed_kayaks(db):
    for kayak in DEFAULT_KAYAKS:
        existing = db.execute("SELECT id FROM kayaks WHERE name = ?", (kayak["name"],)).fetchone()
        if existing:
            db.execute(
                """
                UPDATE kayaks
                SET tagline = ?, capacity = ?, price_per_day = ?, image_url = ?, active = 1
                WHERE name = ?
                """,
                (
                    kayak["tagline"],
                    kayak["capacity"],
                    kayak["price_per_day"],
                    kayak["image_url"],
                    kayak["name"],
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO kayaks
                    (name, tagline, capacity, price_per_day, image_url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    kayak["name"],
                    kayak["tagline"],
                    kayak["capacity"],
                    kayak["price_per_day"],
                    kayak["image_url"],
                ),
            )


def migrate_db(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(bookings)").fetchall()}
    migrations = {
        "booking_reference": "ALTER TABLE bookings ADD COLUMN booking_reference TEXT",
        "customer_phone": "ALTER TABLE bookings ADD COLUMN customer_phone TEXT",
        "total_price": "ALTER TABLE bookings ADD COLUMN total_price INTEGER",
        "email_status": "ALTER TABLE bookings ADD COLUMN email_status TEXT NOT NULL DEFAULT 'pending'",
        "email_error": "ALTER TABLE bookings ADD COLUMN email_error TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            db.execute(statement)

    # Backfill NULL and duplicate references so the unique index can be created.
    seen = set()
    rows = db.execute("SELECT id, booking_reference FROM bookings ORDER BY id").fetchall()
    for row in rows:
        reference = row[1]
        if reference is None or reference in seen:
            db.execute(
                "UPDATE bookings SET booking_reference = ? WHERE id = ?",
                (generate_booking_reference(), row[0]),
            )
        else:
            seen.add(reference)


def generate_booking_reference():
    return f"MLK-{secrets.token_hex(5).upper()}"


def parse_booking_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def count_days(start_date, end_date):
    return (end_date - start_date).days + 1


def display_booking_date(booking):
    if booking["start_date"] == booking["end_date"]:
        return booking["start_date"]
    return f"{booking['start_date']} to {booking['end_date']}"


def date_is_available(db, kayak_id, start_date, end_date):
    return date_range_is_available(db, kayak_id, start_date, end_date)


def date_range_is_available(db, kayak_id, start_date, end_date, exclude_booking_id=None):
    params = [kayak_id, end_date.isoformat(), start_date.isoformat()]
    exclude_clause = ""
    if exclude_booking_id is not None:
        exclude_clause = "AND id != ?"
        params.append(exclude_booking_id)

    overlapping = db.execute(
        f"""
        SELECT 1 FROM bookings
        WHERE kayak_id = ?
          AND status = 'confirmed'
          AND start_date <= ?
          AND end_date >= ?
          {exclude_clause}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return overlapping is None


def smtp_is_configured():
    return bool(SMTP_HOST and OWNER_EMAIL and FROM_EMAIL)


def send_email(recipient, subject, body, reply_to=None):
    if not smtp_is_configured():
        raise RuntimeError("SMTP is not configured. Fill in KAYAK_SMTP_HOST, KAYAK_FROM_EMAIL, and KAYAK_OWNER_EMAIL in .env.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = FROM_EMAIL
    message["To"] = recipient
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)


def send_booking_emails(booking, kayak):
    reference = booking["booking_reference"]
    owner_subject = f"New kayak booking {reference}: {kayak['name']}"
    customer_subject = f"Your Mahebourg Lagoon kayak booking {reference}"
    owner_body = f"""
New booking received

Reference: {reference}
Kayak: {kayak['name']}
Customer: {booking['customer_name']} <{booking['customer_email']}>
Phone/WhatsApp: {booking['customer_phone'] or 'Not provided'}
Booking date: {display_booking_date(booking)}
Total: Rs {booking['total_price']}
Notes: {booking['notes'] or 'None'}
""".strip()
    customer_body = f"""
Hi {booking['customer_name']},

Thank you for booking {kayak['name']} in Mahebourg Lagoon.

Reference: {reference}
Booking date: {display_booking_date(booking)}
Total: Rs {booking['total_price']}
Status: Confirmed

Please keep this email. The owner will contact you directly before the rental. Online payment will be added soon; for now payment is arranged directly.
""".strip()

    for recipient, subject, body, reply_to in (
        (OWNER_EMAIL, owner_subject, owner_body, booking["customer_email"]),
        (booking["customer_email"], customer_subject, customer_body, OWNER_EMAIL),
    ):
        send_email(recipient, subject, body, reply_to)


@app.route("/")
def index():
    db = get_db()
    kayaks = db.execute("SELECT * FROM kayaks WHERE active = 1").fetchall()
    return render_template(
        "index.html",
        kayaks=kayaks,
        today=date.today().isoformat(),
        smtp_configured=smtp_is_configured(),
    )


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(BASE_DIR / "assets", filename)


@app.route("/api/availability")
def availability():
    db = get_db()
    try:
        kayak_id = int(request.args.get("kayak_id", 1))
        start = parse_booking_date(request.args.get("start", date.today().isoformat()))
        days = int(request.args.get("days", 30))
    except ValueError:
        return jsonify({"error": "Invalid kayak_id, start, or days parameter."}), 400

    days = max(1, min(days, 90))
    kayak = db.execute("SELECT id FROM kayaks WHERE id = ? AND active = 1", (kayak_id,)).fetchone()
    if kayak is None:
        return jsonify({"error": "Unknown kayak."}), 404

    availability_days = []
    for offset in range(days):
        current = start + timedelta(days=offset)
        available = date_is_available(db, kayak_id, current, current)
        availability_days.append(
            {
                "date": current.isoformat(),
                "label": current.strftime("%d %b"),
                "weekday": current.strftime("%a"),
                "available": available,
            }
        )
    return jsonify(availability_days)


@app.route("/book", methods=["POST"])
def book():
    db = get_db()
    form = request.form
    try:
        kayak_id = int(form["kayak_id"])
        booking_date = parse_booking_date(form["booking_date"])
    except (KeyError, ValueError):
        return redirect(url_for("index", error="invalid-date"))

    start_date = booking_date
    end_date = booking_date

    if booking_date < date.today():
        return redirect(url_for("index", error="past-date"))

    kayak = db.execute("SELECT * FROM kayaks WHERE id = ? AND active = 1", (kayak_id,)).fetchone()
    if kayak is None or not date_is_available(db, kayak_id, start_date, end_date):
        return redirect(url_for("index", error="unavailable"))

    booking = {
        "kayak_id": kayak_id,
        "booking_reference": generate_booking_reference(),
        "customer_name": form.get("customer_name", "").strip(),
        "customer_email": form.get("customer_email", "").strip(),
        "customer_phone": form.get("customer_phone", "").strip(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "notes": form.get("notes", "").strip(),
        "total_price": count_days(start_date, end_date) * kayak["price_per_day"],
    }

    if not booking["customer_name"] or "@" not in booking["customer_email"]:
        return redirect(url_for("index", error="contact"))

    # Check availability and insert atomically: BEGIN IMMEDIATE takes the write
    # lock up front so two simultaneous requests cannot both pass the check.
    booking_id = None
    for _attempt in range(2):
        try:
            db.execute("BEGIN IMMEDIATE")
            if not date_range_is_available(db, kayak_id, start_date, end_date):
                db.rollback()
                return redirect(url_for("index", error="unavailable"))
            cursor = db.execute(
                """
                INSERT INTO bookings
                    (kayak_id, booking_reference, customer_name, customer_email, customer_phone,
                     start_date, end_date, notes, total_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    booking["kayak_id"],
                    booking["booking_reference"],
                    booking["customer_name"],
                    booking["customer_email"],
                    booking["customer_phone"],
                    booking["start_date"],
                    booking["end_date"],
                    booking["notes"],
                    booking["total_price"],
                    datetime.utcnow().isoformat(timespec="seconds"),
                ),
            )
            db.commit()
            booking_id = cursor.lastrowid
            break
        except sqlite3.IntegrityError:
            # Reference collision on the unique index: retry once with a new one.
            db.rollback()
            booking["booking_reference"] = generate_booking_reference()

    if booking_id is None:
        return redirect(url_for("index", error="unavailable"))

    try:
        send_booking_emails(booking, kayak)
        db.execute("UPDATE bookings SET email_status = 'sent', email_error = NULL WHERE id = ?", (booking_id,))
        db.commit()
        flash("Booking confirmed. Email notifications were sent to you and the owner.", "success")
    except Exception as error:
        db.execute(
            "UPDATE bookings SET email_status = 'failed', email_error = ? WHERE id = ?",
            (str(error), booking_id),
        )
        db.commit()
        flash("Booking confirmed, but email could not be sent. The owner can still see it in the bookings dashboard.", "warning")

    return redirect(url_for("booking_success", reference=booking["booking_reference"]))


@app.route("/booking/<reference>")
def booking_success(reference):
    db = get_db()
    booking = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name, kayaks.price_per_day
        FROM bookings
        JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE booking_reference = ?
        """,
        (reference,),
    ).fetchone()
    if booking is None:
        return redirect(url_for("index"))
    return render_template("booking_success.html", booking=booking, display_booking_date=display_booking_date)


def admin_is_authorized():
    if ADMIN_TOKEN == "change-me" or request.args.get("token") != ADMIN_TOKEN:
        return False
    return True


def require_admin():
    if not admin_is_authorized():
        return "Unauthorized. Set KAYAK_ADMIN_TOKEN in .env and open /admin?token=YOUR_TOKEN.", 401
    return None


@app.route("/admin")
def admin():
    unauthorized = require_admin()
    if unauthorized:
        return unauthorized

    db = get_db()
    bookings = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings
        JOIN kayaks ON kayaks.id = bookings.kayak_id
        ORDER BY start_date ASC, created_at DESC
        """
    ).fetchall()
    kayaks = db.execute("SELECT * FROM kayaks WHERE active = 1 ORDER BY id").fetchall()
    return render_template(
        "admin.html",
        bookings=bookings,
        kayaks=kayaks,
        admin_token=ADMIN_TOKEN,
        smtp_configured=smtp_is_configured(),
        owner_email=OWNER_EMAIL,
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
    )


@app.route("/admin/bookings/<int:booking_id>/update", methods=["POST"])
def admin_update_booking(booking_id):
    unauthorized = require_admin()
    if unauthorized:
        return unauthorized

    db = get_db()
    form = request.form
    try:
        kayak_id = int(form["kayak_id"])
        booking_date = parse_booking_date(form["booking_date"])
    except (KeyError, ValueError):
        flash("Booking could not be updated. Check the kayak and booking date.", "error")
        return redirect(url_for("admin", token=ADMIN_TOKEN))

    start_date = booking_date
    end_date = booking_date

    kayak = db.execute("SELECT * FROM kayaks WHERE id = ? AND active = 1", (kayak_id,)).fetchone()
    booking = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if kayak is None or booking is None:
        flash("Booking could not be updated because the kayak or booking was not found.", "error")
        return redirect(url_for("admin", token=ADMIN_TOKEN))

    status = form.get("status", "confirmed")
    db.execute("BEGIN IMMEDIATE")
    if status == "confirmed" and not date_range_is_available(db, kayak_id, start_date, end_date, booking_id):
        db.rollback()
        flash("Booking could not be updated because those dates are already booked for that kayak.", "error")
        return redirect(url_for("admin", token=ADMIN_TOKEN))

    total_price = count_days(start_date, end_date) * kayak["price_per_day"]
    db.execute(
        """
        UPDATE bookings
        SET kayak_id = ?, customer_name = ?, customer_email = ?, customer_phone = ?,
            start_date = ?, end_date = ?, notes = ?, total_price = ?, status = ?
        WHERE id = ?
        """,
        (
            kayak_id,
            form.get("customer_name", "").strip(),
            form.get("customer_email", "").strip(),
            form.get("customer_phone", "").strip(),
            start_date.isoformat(),
            end_date.isoformat(),
            form.get("notes", "").strip(),
            total_price,
            status,
            booking_id,
        ),
    )
    db.commit()
    flash("Booking updated.", "success")
    return redirect(url_for("admin", token=ADMIN_TOKEN))


@app.route("/admin/bookings/<int:booking_id>/delete", methods=["POST"])
def admin_delete_booking(booking_id):
    unauthorized = require_admin()
    if unauthorized:
        return unauthorized

    db = get_db()
    db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
    db.commit()
    flash("Booking removed. The dates are available again.", "success")
    return redirect(url_for("admin", token=ADMIN_TOKEN))


@app.route("/admin/test-email", methods=["POST"])
def admin_test_email():
    unauthorized = require_admin()
    if unauthorized:
        return unauthorized

    recipient = request.form.get("test_email", "").strip() or OWNER_EMAIL
    try:
        send_email(
            recipient,
            "Mahebourg Kayak mail server test",
            "This is a test email from the Mahebourg Lagoon Kayak Rentals admin page.",
            OWNER_EMAIL,
        )
        flash(f"Test email sent to {recipient}.", "success")
    except Exception as error:
        flash(f"Test email failed: {error}", "error")
    return redirect(url_for("admin", token=ADMIN_TOKEN))


init_db()


if __name__ == "__main__":
    app.run(debug=DEBUG)
