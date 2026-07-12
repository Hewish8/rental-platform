import csv
import io as io_module
import os
import secrets
import sqlite3
import smtplib
import threading
from contextlib import closing
from functools import wraps
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "kayak_rental.db"

# The business runs on Mauritius time regardless of where the server sits.
MAURITIUS_TZ = timezone(timedelta(hours=4))
CLOSING_HOUR = 17
BOOKING_MAX_DAYS = 14


def local_now():
    return datetime.now(MAURITIUS_TZ)


def local_today():
    return local_now().date()


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

DEBUG = os.getenv("KAYAK_DEBUG", "false").lower() == "true"
_PLACEHOLDER_SECRETS = {"", "dev-change-me", "change-this-to-a-long-random-secret"}


def default_config():
    return {
        "SECRET_KEY": os.getenv("KAYAK_SECRET_KEY", ""),
        "DATABASE": DATABASE,
        "OWNER_EMAIL": os.getenv("KAYAK_OWNER_EMAIL", "owner@example.com"),
        "OWNER_PASSWORD": os.getenv("KAYAK_OWNER_PASSWORD"),
        "FROM_EMAIL": os.getenv("KAYAK_FROM_EMAIL") or os.getenv("KAYAK_OWNER_EMAIL", "owner@example.com"),
        "SMTP_HOST": os.getenv("KAYAK_SMTP_HOST"),
        "SMTP_PORT": int(os.getenv("KAYAK_SMTP_PORT", "587")),
        "SMTP_USERNAME": os.getenv("KAYAK_SMTP_USERNAME"),
        "SMTP_PASSWORD": os.getenv("KAYAK_SMTP_PASSWORD"),
        "SMTP_USE_TLS": os.getenv("KAYAK_SMTP_USE_TLS", "true").lower() == "true",
    }


DEFAULT_KAYAKS = [
    {
        "name": "Lagoon Explorer",
        "tagline": "A stable sit-on-top kayak for calm turquoise water and scenic paddles.",
        "capacity": "1 adult",
        "price_per_day": 1200,
        "image_url": "/assets/KayakTurquoise.jpg",
    },
    {
        "name": "Aigrettes Cruiser",
        "tagline": "Light single kayak for relaxed paddles near Ile aux Aigrettes.",
        "capacity": "1 adult",
        "price_per_day": 1100,
        "image_url": "/assets/KayakSingle.jpg",
    },
    {
        "name": "Phares Tandem",
        "tagline": "Roomier kayak for two people heading toward the lighthouse views.",
        "capacity": "2 adults",
        "price_per_day": 1800,
        "image_url": "/assets/BeachTandem.jpg",
    },
]


# --- Database -----------------------------------------------------------


def connect_db(database):
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def get_db():
    if "db" not in g:
        g.db = connect_db(current_app.config["DATABASE"])
    return g.db


def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(database, owner_email=None, owner_password=None):
    with closing(connect_db(database)) as db:
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
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'staff',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                kind TEXT NOT NULL DEFAULT 'payment',
                method TEXT NOT NULL,
                note TEXT,
                recorded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (booking_id) REFERENCES bookings(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kayak_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                cost INTEGER,
                next_due TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (kayak_id) REFERENCES kayaks(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS damage_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kayak_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'minor',
                status TEXT NOT NULL DEFAULT 'open',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY (kayak_id) REFERENCES kayaks(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL DEFAULT 'percent',
                value INTEGER NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                max_uses INTEGER,
                uses INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_notes (
                identity TEXT PRIMARY KEY,
                note TEXT NOT NULL DEFAULT '',
                flagged INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT,
                updated_at TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                action TEXT NOT NULL,
                entity TEXT NOT NULL,
                entity_id TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        migrate_db(db)
        seed_kayaks(db)
        bootstrap_owner(db, owner_email, owner_password)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bookings_kayak_dates ON bookings(kayak_id, start_date, end_date)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_reference ON bookings(booking_reference)"
        )
        db.commit()


def bootstrap_owner(db, owner_email, owner_password):
    # First-run only: create the owner account from .env so the owner can log
    # in, then manage further users from the dashboard.
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    if not (owner_email and owner_password):
        return
    db.execute(
        "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, 'owner', ?)",
        (
            owner_email.strip().lower(),
            generate_password_hash(owner_password),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def seed_kayaks(db):
    # Seed starter kayaks only on a brand-new database. Once the fleet exists
    # it belongs to the owner; restarts must never overwrite their edits.
    if db.execute("SELECT COUNT(*) FROM kayaks").fetchone()[0]:
        return
    for kayak in DEFAULT_KAYAKS:
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
    kayak_columns = {row[1] for row in db.execute("PRAGMA table_info(kayaks)").fetchall()}
    if "status" not in kayak_columns:
        db.execute("ALTER TABLE kayaks ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        db.execute("UPDATE kayaks SET status = CASE WHEN active = 1 THEN 'active' ELSE 'retired' END")

    columns = {row[1] for row in db.execute("PRAGMA table_info(bookings)").fetchall()}
    migrations = {
        "booking_reference": "ALTER TABLE bookings ADD COLUMN booking_reference TEXT",
        "customer_phone": "ALTER TABLE bookings ADD COLUMN customer_phone TEXT",
        "total_price": "ALTER TABLE bookings ADD COLUMN total_price INTEGER",
        "email_status": "ALTER TABLE bookings ADD COLUMN email_status TEXT NOT NULL DEFAULT 'pending'",
        "email_error": "ALTER TABLE bookings ADD COLUMN email_error TEXT",
        "party_size": "ALTER TABLE bookings ADD COLUMN party_size INTEGER NOT NULL DEFAULT 1",
        "source": "ALTER TABLE bookings ADD COLUMN source TEXT NOT NULL DEFAULT 'online'",
        "expected_return": "ALTER TABLE bookings ADD COLUMN expected_return TEXT",
        "checked_out_at": "ALTER TABLE bookings ADD COLUMN checked_out_at TEXT",
        "checked_in_at": "ALTER TABLE bookings ADD COLUMN checked_in_at TEXT",
        "cancelled_reason": "ALTER TABLE bookings ADD COLUMN cancelled_reason TEXT",
        "promo_code": "ALTER TABLE bookings ADD COLUMN promo_code TEXT",
        "discount": "ALTER TABLE bookings ADD COLUMN discount INTEGER NOT NULL DEFAULT 0",
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


# --- Booking helpers ----------------------------------------------------


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


# Statuses that occupy the kayak for their dates. Cancelled and no-show
# bookings free the dates again.
BLOCKING_STATUSES = ("confirmed", "on_water", "returned")


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
          AND status IN ('confirmed', 'on_water', 'returned')
          AND start_date <= ?
          AND end_date >= ?
          {exclude_clause}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return overlapping is None


# --- Email --------------------------------------------------------------


def booked_dates(db, kayak_id, start, end):
    """All booked ISO dates for a kayak within [start, end], in one query."""
    rows = db.execute(
        """
        SELECT start_date, end_date FROM bookings
        WHERE kayak_id = ?
          AND status IN ('confirmed', 'on_water', 'returned')
          AND start_date <= ?
          AND end_date >= ?
        """,
        (kayak_id, end.isoformat(), start.isoformat()),
    ).fetchall()
    booked = set()
    for row in rows:
        current = max(parse_booking_date(row["start_date"]), start)
        last = min(parse_booking_date(row["end_date"]), end)
        while current <= last:
            booked.add(current.isoformat())
            current += timedelta(days=1)
    return booked


def nearest_available_dates(db, kayak_id, around, count=3, horizon=45):
    """Closest bookable dates to a requested day, nearest first, future-biased."""
    suggestions = []
    for offset in range(1, horizon + 1):
        for candidate in (around + timedelta(days=offset), around - timedelta(days=offset)):
            if candidate < local_today():
                continue
            if date_range_is_available(db, kayak_id, candidate, candidate):
                suggestions.append(candidate)
                if len(suggestions) >= count:
                    return sorted(suggestions)
    return sorted(suggestions)


def smtp_is_configured(cfg):
    return bool(cfg["SMTP_HOST"] and cfg["OWNER_EMAIL"] and cfg["FROM_EMAIL"])


def send_email(cfg, recipient, subject, body, reply_to=None):
    if not smtp_is_configured(cfg):
        raise RuntimeError(
            "SMTP is not configured. Fill in KAYAK_SMTP_HOST, KAYAK_FROM_EMAIL, and KAYAK_OWNER_EMAIL in .env."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["FROM_EMAIL"]
    message["To"] = recipient
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)

    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as smtp:
        if cfg["SMTP_USE_TLS"]:
            smtp.starttls()
        if cfg["SMTP_USERNAME"] and cfg["SMTP_PASSWORD"]:
            smtp.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
        smtp.send_message(message)


def send_booking_emails(cfg, booking, kayak):
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
        (cfg["OWNER_EMAIL"], owner_subject, owner_body, booking["customer_email"]),
        (booking["customer_email"], customer_subject, customer_body, cfg["OWNER_EMAIL"]),
    ):
        send_email(cfg, recipient, subject, body, reply_to)


_EMAIL_CONFIG_KEYS = (
    "OWNER_EMAIL",
    "FROM_EMAIL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_USE_TLS",
)


def deliver_booking_emails(cfg, database, booking_id, booking, kayak):
    """Send both booking emails and record the outcome. Safe to run in a thread."""
    try:
        send_booking_emails(cfg, booking, kayak)
        status, error = "sent", None
    except Exception as exc:
        status, error = "failed", str(exc)
    with closing(connect_db(database)) as db:
        db.execute(
            "UPDATE bookings SET email_status = ?, email_error = ? WHERE id = ?",
            (status, error, booking_id),
        )
        db.commit()


def queue_booking_emails(booking_id, booking, kayak):
    """Send booking emails without blocking the request."""
    cfg = {key: current_app.config[key] for key in _EMAIL_CONFIG_KEYS}
    database = current_app.config["DATABASE"]
    if current_app.config.get("TESTING") or not smtp_is_configured(cfg):
        # Synchronous: deterministic in tests, and instantly marks 'failed'
        # when SMTP is not configured instead of leaving 'pending' forever.
        deliver_booking_emails(cfg, database, booking_id, booking, kayak)
        return
    threading.Thread(
        target=deliver_booking_emails,
        args=(cfg, database, booking_id, dict(booking), dict(kayak)),
        daemon=True,
    ).start()


# --- Public routes ------------------------------------------------------


def render_index(form_values=None, form_errors=None, status=200, date_suggestions=None):
    db = get_db()
    kayaks = db.execute("SELECT * FROM kayaks WHERE status = 'active'").fetchall()
    return (
        render_template(
            "index.html",
            kayaks=kayaks,
            today=local_today().isoformat(),
            smtp_configured=smtp_is_configured(current_app.config),
            owner_email=current_app.config["OWNER_EMAIL"],
            form_values=form_values or {},
            form_errors=form_errors or {},
            date_suggestions=[
                {"date": d.isoformat(), "label": d.strftime("%a %d %b")} for d in (date_suggestions or [])
            ],
        ),
        status,
    )


def index():
    return render_index()


def assets(filename):
    return send_from_directory(BASE_DIR / "assets", filename)


def availability():
    db = get_db()
    try:
        kayak_id = int(request.args.get("kayak_id", 1))
        start = parse_booking_date(request.args.get("start", date.today().isoformat()))
        days = int(request.args.get("days", 30))
    except ValueError:
        return jsonify({"error": "Invalid kayak_id, start, or days parameter."}), 400

    days = max(1, min(days, 90))
    kayak = db.execute("SELECT id FROM kayaks WHERE id = ? AND status = 'active'", (kayak_id,)).fetchone()
    if kayak is None:
        return jsonify({"error": "Unknown kayak."}), 404

    end = start + timedelta(days=days - 1)
    booked = booked_dates(db, kayak_id, start, end)

    availability_days = []
    for offset in range(days):
        current = start + timedelta(days=offset)
        availability_days.append(
            {
                "date": current.isoformat(),
                "label": current.strftime("%d %b"),
                "weekday": current.strftime("%a"),
                "available": current.isoformat() not in booked,
            }
        )
    return jsonify(availability_days)


def parse_range_fields(form):
    """Accepts start_date/end_date, falling back to booking_date for both."""
    start_raw = form.get("start_date", "").strip() or form.get("booking_date", "").strip()
    end_raw = form.get("end_date", "").strip() or start_raw
    return start_raw, end_raw


def validate_booking_form(db, form):
    """Returns (values, errors, kayak, start_date, end_date)."""
    start_raw, end_raw = parse_range_fields(form)
    values = {
        "kayak_id": form.get("kayak_id", "").strip(),
        "start_date": start_raw,
        "end_date": end_raw,
        "customer_name": form.get("customer_name", "").strip(),
        "customer_email": form.get("customer_email", "").strip(),
        "customer_phone": form.get("customer_phone", "").strip(),
        "notes": form.get("notes", "").strip(),
    }
    errors = {}

    kayak = None
    try:
        kayak_id = int(values["kayak_id"])
    except ValueError:
        errors["kayak_id"] = "Please select a kayak."
    else:
        kayak = db.execute("SELECT * FROM kayaks WHERE id = ? AND status = 'active'", (kayak_id,)).fetchone()
        if kayak is None:
            errors["kayak_id"] = "That kayak is no longer available. Please pick another."

    start_date = end_date = None
    try:
        start_date = parse_booking_date(start_raw)
        end_date = parse_booking_date(end_raw)
    except ValueError:
        errors["booking_date"] = "Please pick valid dates from the calendar."
    else:
        if end_date < start_date:
            start_date, end_date = end_date, start_date
            values["start_date"], values["end_date"] = start_date.isoformat(), end_date.isoformat()
        if start_date < local_today():
            errors["booking_date"] = "That date has already passed. Please pick an upcoming date."
        elif count_days(start_date, end_date) > BOOKING_MAX_DAYS:
            errors["booking_date"] = f"Bookings are limited to {BOOKING_MAX_DAYS} days. For longer rentals, message us on WhatsApp."
        elif kayak is not None and not date_range_is_available(db, kayak["id"], start_date, end_date):
            errors["booking_date"] = "That kayak is already booked on one of those dates. Please pick different dates."

    if not values["customer_name"]:
        errors["customer_name"] = "Please enter your full name."
    if "@" not in values["customer_email"]:
        errors["customer_email"] = "Please enter a valid email address."
    if len(values["notes"]) > 2000:
        errors["notes"] = "Please keep your message under 2000 characters."

    values["promo_code"] = form.get("promo_code", "").strip().upper()

    return values, errors, kayak, start_date, end_date


def find_valid_promo(db, code):
    """Returns (promo_row, error_message)."""
    code = (code or "").strip().upper()
    if not code:
        return None, None
    promo = db.execute("SELECT * FROM promo_codes WHERE code = ?", (code,)).fetchone()
    today_iso = local_today().isoformat()
    if promo is None or not promo["active"]:
        return None, "That promo code is not valid."
    if promo["valid_from"] and today_iso < promo["valid_from"]:
        return None, "That promo code is not active yet."
    if promo["valid_to"] and today_iso > promo["valid_to"]:
        return None, "That promo code has expired."
    if promo["max_uses"] and promo["uses"] >= promo["max_uses"]:
        return None, "That promo code has been fully used."
    return promo, None


def promo_discount(promo, total):
    if promo is None:
        return 0
    if promo["kind"] == "percent":
        return min(total, round(total * promo["value"] / 100))
    return min(total, promo["value"])


def create_booking(db, kayak, start_date, end_date, values, source, party_size=1, promo=None):
    """Atomically create a booking. Returns (booking_id, booking) — id is None
    if the dates were taken between validation and insert."""
    base_total = count_days(start_date, end_date) * kayak["price_per_day"]
    discount = promo_discount(promo, base_total)
    booking = {
        "kayak_id": kayak["id"],
        "booking_reference": generate_booking_reference(),
        "customer_name": values["customer_name"],
        "customer_email": values.get("customer_email", ""),
        "customer_phone": values.get("customer_phone", ""),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "notes": values.get("notes", ""),
        "total_price": base_total - discount,
        "discount": discount,
        "promo_code": promo["code"] if promo else None,
    }
    for _attempt in range(2):
        try:
            db.execute("BEGIN IMMEDIATE")
            if not date_range_is_available(db, kayak["id"], start_date, end_date):
                db.rollback()
                return None, booking
            cursor = db.execute(
                """
                INSERT INTO bookings
                    (kayak_id, booking_reference, customer_name, customer_email, customer_phone,
                     start_date, end_date, notes, total_price, discount, promo_code,
                     source, party_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    booking["discount"],
                    booking["promo_code"],
                    source,
                    party_size,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            if promo is not None:
                db.execute("UPDATE promo_codes SET uses = uses + 1 WHERE id = ?", (promo["id"],))
            record_audit(db, "created", "booking", cursor.lastrowid, f"{booking['booking_reference']} via {source}")
            db.commit()
            return cursor.lastrowid, booking
        except sqlite3.IntegrityError:
            db.rollback()
            booking["booking_reference"] = generate_booking_reference()
    return None, booking


def conflict_suggestions(db, kayak, booking_date):
    if kayak is None or booking_date is None:
        return None
    return nearest_available_dates(db, kayak["id"], booking_date)


def promo_check():
    db = get_db()
    promo, error = find_valid_promo(db, request.args.get("code", ""))
    if error or promo is None:
        return jsonify({"valid": False, "error": error or "Enter a promo code."}), 200
    return jsonify({"valid": True, "kind": promo["kind"], "value": promo["value"], "code": promo["code"]})


def book():
    db = get_db()
    values, errors, kayak, start_date, end_date = validate_booking_form(db, request.form)
    promo, promo_error = find_valid_promo(db, values.get("promo_code"))
    if promo_error:
        errors["promo_code"] = promo_error
    if errors:
        suggestions = None
        if "already booked" in errors.get("booking_date", "") and start_date == end_date:
            suggestions = conflict_suggestions(db, kayak, start_date)
        return render_index(values, errors, 400, suggestions)

    booking_id, booking = create_booking(db, kayak, start_date, end_date, values, source="online", promo=promo)
    if booking_id is None:
        errors["booking_date"] = "Those dates were just booked. Please pick different dates."
        suggestions = conflict_suggestions(db, kayak, start_date) if start_date == end_date else None
        return render_index(values, errors, 409, suggestions)

    queue_booking_emails(booking_id, booking, kayak)
    if smtp_is_configured(current_app.config):
        flash("Booking confirmed. Your confirmation email is on its way.", "success")
    else:
        flash("Booking confirmed, but email could not be sent. The owner can still see it in the bookings dashboard.", "warning")

    return redirect(url_for("booking_success", reference=booking["booking_reference"]))


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


def booking_ics(reference):
    db = get_db()
    booking = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings
        JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE booking_reference = ?
        """,
        (reference,),
    ).fetchone()
    if booking is None:
        return redirect(url_for("index"))

    start = parse_booking_date(booking["start_date"])
    end_exclusive = parse_booking_date(booking["end_date"]) + timedelta(days=1)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ics = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Mahebourg Lagoon Kayak Rentals//EN",
            "BEGIN:VEVENT",
            f"UID:{booking['booking_reference']}@mahebourg-kayak",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{end_exclusive.strftime('%Y%m%d')}",
            f"SUMMARY:Kayak rental — {booking['kayak_name']}",
            f"DESCRIPTION:Booking {booking['booking_reference']}. Pay on arrival (Rs {booking['total_price']}). Arrive 15 minutes early.",
            "LOCATION:Mahebourg Waterfront\\, Mauritius",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
    return (
        ics,
        200,
        {
            "Content-Type": "text/calendar; charset=utf-8",
            "Content-Disposition": f"attachment; filename=kayak-{booking['booking_reference']}.ics",
        },
    )


# --- Admin routes -------------------------------------------------------


def payments_by_booking(db):
    rows = db.execute(
        """
        SELECT booking_id,
               COALESCE(SUM(CASE WHEN kind = 'refund' THEN -amount ELSE amount END), 0) AS paid
        FROM payments GROUP BY booking_id
        """
    ).fetchall()
    return {row["booking_id"]: row["paid"] for row in rows}


def admin_redirect():
    """POST handlers return to the page they came from (detail pages pass next)."""
    next_path = request.form.get("next", "")
    if next_path.startswith("/admin"):
        return redirect(next_path)
    return redirect(url_for("admin"))


def record_audit(db, action, entity, entity_id=None, details=None):
    """Append-only trail of who changed what. Committed with the caller's commit."""
    user = getattr(g, "user", None)
    db.execute(
        "INSERT INTO audit_log (user_email, action, entity, entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            user["email"] if user else "customer",
            action,
            entity,
            str(entity_id) if entity_id is not None else None,
            details,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def check_csrf():
    if request.method != "POST" or not current_app.config.get("CSRF_ENABLED", True):
        return
    token = session.get("csrf_token", "")
    provided = request.form.get("_csrf", "")
    if not token or not secrets.compare_digest(provided, token):
        # Stale or missing session (old tab, cleared cookies): never show a raw
        # error page — explain and send them back to a fresh form.
        flash("That page had expired, so nothing was saved. Please try again.", "error")
        referrer = request.referrer or ""
        if referrer.startswith(request.host_url):
            return redirect(referrer)
        return redirect(url_for("admin_login") if request.path.startswith("/admin") else url_for("index"))


def load_logged_in_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)
        ).fetchone()


DEFAULT_SETTINGS = {
    "whatsapp_number": "+230 5XXX XXXX",
    "phone_display": "+230 5XXX XXXX",
    "meeting_point": "Mahebourg Waterfront, near the Pointe des Regates monument",
    "hours_text": "Open daily, 8:00 - 17:00 (last departure 15:00)",
}


def get_settings(db):
    stored = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings").fetchall()}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    merged["whatsapp_link"] = "https://wa.me/" + "".join(
        ch for ch in merged["whatsapp_number"] if ch.isdigit()
    )
    return merged


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("admin_login", next=request.path))
        if g.user["role"] != "owner":
            flash("Only the owner can open that page.", "error")
            return redirect(url_for("admin_today"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_login():
    if g.user is not None:
        return redirect(url_for("admin"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE lower(email) = ? AND active = 1", (email,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            next_path = request.args.get("next", "")
            if not next_path.startswith("/admin"):
                next_path = url_for("admin_today")
            return redirect(next_path)
        flash("Wrong email or password.", "error")
    return render_template("login.html")


def admin_logout():
    session.clear()
    flash("You are signed out.", "success")
    return redirect(url_for("admin_login"))


def fetch_bookings(db, view, q):
    today_iso = local_today().isoformat()
    where = ["1=1"]
    params = []
    order = "bookings.start_date DESC, bookings.created_at DESC"
    if view == "upcoming":
        where.append("bookings.start_date >= ?")
        params.append(today_iso)
        where.append("bookings.status IN ('confirmed', 'on_water')")
        order = "bookings.start_date ASC, bookings.created_at DESC"
    elif view == "today":
        where.append("bookings.start_date = ?")
        params.append(today_iso)
    elif view == "past":
        where.append("bookings.start_date < ?")
        params.append(today_iso)
        where.append("bookings.status IN ('confirmed', 'on_water', 'returned')")
    elif view == "cancelled":
        where.append("bookings.status IN ('cancelled', 'no_show')")
    if q:
        where.append(
            "(bookings.customer_name LIKE ? OR bookings.customer_email LIKE ? "
            "OR bookings.customer_phone LIKE ? OR bookings.booking_reference LIKE ?)"
        )
        params.extend([f"%{q}%"] * 4)
    return db.execute(
        f"""
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings
        JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE {' AND '.join(where)}
        ORDER BY {order}
        """,
        params,
    ).fetchall()


@admin_required
def admin():
    cfg = current_app.config
    db = get_db()
    view = request.args.get("view", "upcoming")
    if view not in ("upcoming", "today", "past", "cancelled", "unpaid", "all"):
        view = "upcoming"
    q = request.args.get("q", "").strip()
    bookings = fetch_bookings(db, view, q)
    paid_map = payments_by_booking(db)
    if view == "unpaid":
        bookings = [
            b for b in bookings
            if b["status"] in ("confirmed", "on_water", "returned")
            and (b["total_price"] or 0) - paid_map.get(b["id"], 0) > 0
        ]
    return render_template(
        "admin.html",
        bookings=bookings,
        view=view,
        q=q,
        paid_map=paid_map,
        owner_email=cfg["OWNER_EMAIL"],
        smtp_host=cfg["SMTP_HOST"],
        smtp_port=cfg["SMTP_PORT"],
    )


@admin_required
def admin_today():
    db = get_db()
    today_iso = local_today().isoformat()
    tomorrow_iso = (local_today() + timedelta(days=1)).isoformat()
    now_iso = local_now().isoformat(timespec="seconds")

    todays = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE bookings.start_date <= ? AND bookings.end_date >= ?
          AND bookings.status IN ('confirmed', 'on_water', 'returned')
        ORDER BY bookings.status = 'returned', bookings.created_at
        """,
        (today_iso, today_iso),
    ).fetchall()
    paid_map = payments_by_booking(db)

    late = [
        b for b in todays
        if b["status"] == "on_water" and b["expected_return"] and b["expected_return"] < now_iso
    ]
    expected_revenue = sum(b["total_price"] or 0 for b in todays)
    unpaid_total = sum(
        max(0, (b["total_price"] or 0) - paid_map.get(b["id"], 0)) for b in todays
    )
    on_water_count = sum(1 for b in todays if b["status"] == "on_water")
    fleet_size = db.execute("SELECT COUNT(*) FROM kayaks WHERE status = 'active'").fetchone()[0]

    tomorrows = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE bookings.start_date = ? AND bookings.status IN ('confirmed', 'on_water')
        ORDER BY bookings.created_at
        """,
        (tomorrow_iso,),
    ).fetchall()

    maintenance_due = db.execute(
        """
        SELECT kayaks.name, MAX(maintenance_log.next_due) AS next_due, kayaks.id AS kayak_id
        FROM maintenance_log JOIN kayaks ON kayaks.id = maintenance_log.kayak_id
        WHERE kayaks.status != 'retired'
        GROUP BY maintenance_log.kayak_id
        HAVING MAX(maintenance_log.next_due) <= ?
        """,
        (today_iso,),
    ).fetchall()

    email_failures = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE bookings.email_status = 'failed' AND bookings.start_date >= ?
        ORDER BY bookings.start_date LIMIT 10
        """,
        (today_iso,),
    ).fetchall()

    return render_template(
        "admin_today.html",
        todays=todays,
        late=late,
        late_ids={b["id"] for b in late},
        tomorrows=tomorrows,
        email_failures=email_failures,
        maintenance_due=maintenance_due,
        paid_map=paid_map,
        expected_revenue=expected_revenue,
        unpaid_total=unpaid_total,
        on_water_count=on_water_count,
        fleet_size=fleet_size,
        today_label=local_today().strftime("%A %d %B"),
    )


@admin_required
def admin_checkout_booking(booking_id):
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if booking is None or booking["status"] != "confirmed":
        flash("Only a confirmed booking can be checked out.", "error")
        return admin_redirect()
    now = local_now()
    expected = now.replace(hour=CLOSING_HOUR, minute=0, second=0, microsecond=0)
    if expected <= now:
        expected = now + timedelta(hours=2)
    db.execute(
        "UPDATE bookings SET status = 'on_water', checked_out_at = ?, expected_return = ? WHERE id = ?",
        (now.isoformat(timespec="seconds"), expected.isoformat(timespec="seconds"), booking_id),
    )
    record_audit(db, "checked-out", "booking", booking_id, booking["booking_reference"])
    db.commit()
    flash(f"{booking['customer_name']} is on the water. Expected back by {expected.strftime('%H:%M')}.", "success")
    return admin_redirect()


@admin_required
def admin_checkin_booking(booking_id):
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if booking is None or booking["status"] != "on_water":
        flash("Only an on-water booking can be checked in.", "error")
        return admin_redirect()
    db.execute(
        "UPDATE bookings SET status = 'returned', checked_in_at = ? WHERE id = ?",
        (local_now().isoformat(timespec="seconds"), booking_id),
    )
    record_audit(db, "checked-in", "booking", booking_id, booking["booking_reference"])
    db.commit()
    flash(f"{booking['customer_name']} is back. Kayak free again.", "success")
    return admin_redirect()


@admin_required
def admin_new_booking():
    db = get_db()
    kayaks = db.execute("SELECT * FROM kayaks WHERE status = 'active' ORDER BY id").fetchall()
    form_values = {
        "kayak_id": request.args.get("kayak_id", ""),
        "booking_date": request.args.get("date", ""),
    }
    form_errors = {}

    if request.method == "POST":
        form = request.form
        form_values = {key: form.get(key, "").strip() for key in
                       ("kayak_id", "booking_date", "end_date", "customer_name", "customer_email",
                        "customer_phone", "notes", "party_size", "amount", "method")}
        kayak = None
        try:
            kayak = db.execute(
                "SELECT * FROM kayaks WHERE id = ? AND status = 'active'", (int(form_values["kayak_id"] or 0),)
            ).fetchone()
        except ValueError:
            pass
        if kayak is None:
            form_errors["kayak_id"] = "Pick a kayak."

        booking_date = end_date_value = None
        try:
            booking_date = parse_booking_date(form_values["booking_date"])
            end_date_value = (
                parse_booking_date(form_values["end_date"]) if form_values.get("end_date") else booking_date
            )
        except ValueError:
            form_errors["booking_date"] = "Pick a valid date."
        else:
            if end_date_value < booking_date:
                booking_date, end_date_value = end_date_value, booking_date
            if booking_date < local_today():
                form_errors["booking_date"] = "That date is in the past."
            elif count_days(booking_date, end_date_value) > BOOKING_MAX_DAYS:
                form_errors["booking_date"] = f"Bookings are limited to {BOOKING_MAX_DAYS} days."
            elif kayak and not date_range_is_available(db, kayak["id"], booking_date, end_date_value):
                form_errors["booking_date"] = "That kayak is already booked on one of those dates."

        if not form_values["customer_name"]:
            form_errors["customer_name"] = "Name is required."
        if form_values["customer_email"] and "@" not in form_values["customer_email"]:
            form_errors["customer_email"] = "That email looks wrong (leave empty for walk-ups)."
        try:
            party_size = max(1, int(form_values.get("party_size") or 1))
        except ValueError:
            party_size = 1

        amount = 0
        if form_values.get("amount"):
            try:
                amount = int(form_values["amount"])
                if amount < 0:
                    raise ValueError
            except ValueError:
                form_errors["amount"] = "Amount must be a positive number of rupees."

        if not form_errors:
            booking_id, booking = create_booking(
                db, kayak, booking_date, end_date_value, form_values, source="walkup", party_size=party_size
            )
            if booking_id is None:
                form_errors["booking_date"] = "That kayak was just booked on this date."
            else:
                if booking["customer_email"]:
                    queue_booking_emails(booking_id, booking, kayak)
                else:
                    db.execute("UPDATE bookings SET email_status = 'skipped' WHERE id = ?", (booking_id,))
                    db.commit()
                if amount:
                    method = form_values.get("method") or "cash"
                    if method not in ("cash", "card", "transfer"):
                        method = "cash"
                    db.execute(
                        "INSERT INTO payments (booking_id, amount, kind, method, note, recorded_by, created_at) VALUES (?, ?, 'payment', ?, '', ?, ?)",
                        (booking_id, amount, method, g.user["email"],
                         datetime.now(timezone.utc).isoformat(timespec="seconds")),
                    )
                    record_audit(db, "payment-recorded", "booking", booking_id, f"Rs {amount} {method} (at creation)")
                    db.commit()
                flash(f"Booking {booking['booking_reference']} created for {booking['customer_name']}.", "success")
                return redirect(url_for("admin_booking_detail", booking_id=booking_id))

    return render_template(
        "admin_new_booking.html",
        kayaks=kayaks,
        form_values=form_values,
        form_errors=form_errors,
        today=local_today().isoformat(),
    )


@admin_required
def admin_booking_detail(booking_id):
    db = get_db()
    booking = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings
        JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE bookings.id = ?
        """,
        (booking_id,),
    ).fetchone()
    if booking is None:
        flash("Booking not found.", "error")
        return redirect(url_for("admin"))
    payments = db.execute(
        "SELECT * FROM payments WHERE booking_id = ? ORDER BY id DESC", (booking_id,)
    ).fetchall()
    paid = sum(p["amount"] if p["kind"] != "refund" else -p["amount"] for p in payments)
    audit_entries = db.execute(
        "SELECT * FROM audit_log WHERE entity = 'booking' AND entity_id = ? ORDER BY id DESC LIMIT 50",
        (str(booking_id),),
    ).fetchall()
    kayaks = db.execute("SELECT * FROM kayaks WHERE status != 'retired' ORDER BY id").fetchall()
    return render_template(
        "admin_booking.html",
        booking=booking,
        payments=payments,
        paid=paid,
        due=(booking["total_price"] or 0) - paid,
        audit_entries=audit_entries,
        kayaks=kayaks,
    )


@admin_required
def admin_bookings_csv():
    db = get_db()
    rows = db.execute(
        """
        SELECT bookings.booking_reference, bookings.start_date, bookings.end_date,
               kayaks.name AS kayak, bookings.customer_name, bookings.customer_email,
               bookings.customer_phone, bookings.status, bookings.total_price,
               bookings.source, bookings.created_at, bookings.notes
        FROM bookings JOIN kayaks ON kayaks.id = bookings.kayak_id
        ORDER BY bookings.start_date DESC
        """
    ).fetchall()
    buffer = io_module.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["reference", "start_date", "end_date", "kayak", "customer", "email", "phone",
         "status", "total_rs", "source", "created_at", "notes"]
    )
    for row in rows:
        writer.writerow([row[k] for k in row.keys()])
    return (
        buffer.getvalue(),
        200,
        {
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": "attachment; filename=bookings.csv",
        },
    )


@admin_required
def admin_update_booking(booking_id):
    db = get_db()
    form = request.form
    try:
        kayak_id = int(form["kayak_id"])
        start_date = parse_booking_date(form["booking_date"])
        end_date = parse_booking_date(form.get("end_date") or form["booking_date"])
    except (KeyError, ValueError):
        flash("Booking could not be updated. Check the kayak and booking date.", "error")
        return redirect(url_for("admin"))

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    kayak = db.execute("SELECT * FROM kayaks WHERE id = ? AND status != 'retired'", (kayak_id,)).fetchone()
    booking = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if kayak is None or booking is None:
        flash("Booking could not be updated because the kayak or booking was not found.", "error")
        return redirect(url_for("admin"))

    status = form.get("status", "confirmed")
    db.execute("BEGIN IMMEDIATE")
    if status == "confirmed" and not date_range_is_available(db, kayak_id, start_date, end_date, booking_id):
        db.rollback()
        flash("Booking could not be updated because those dates are already booked for that kayak.", "error")
        return redirect(url_for("admin"))

    total_price = count_days(start_date, end_date) * kayak["price_per_day"]
    new_values = {
        "kayak_id": kayak_id,
        "customer_name": form.get("customer_name", "").strip(),
        "customer_email": form.get("customer_email", "").strip(),
        "customer_phone": form.get("customer_phone", "").strip(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "notes": form.get("notes", "").strip(),
        "total_price": total_price,
        "status": status,
    }
    changes = "; ".join(
        f"{field}: {booking[field]!r} -> {value!r}"
        for field, value in new_values.items()
        if booking[field] != value
    )
    db.execute(
        """
        UPDATE bookings
        SET kayak_id = ?, customer_name = ?, customer_email = ?, customer_phone = ?,
            start_date = ?, end_date = ?, notes = ?, total_price = ?, status = ?
        WHERE id = ?
        """,
        (*new_values.values(), booking_id),
    )
    if changes:
        record_audit(db, "updated", "booking", booking_id, changes)
    db.commit()
    flash("Booking updated.", "success")
    return admin_redirect()


@admin_required
def admin_cancel_booking(booking_id):
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if booking is None:
        flash("Booking not found.", "error")
        return redirect(url_for("admin"))
    reason = request.form.get("reason", "").strip() or "No reason recorded"
    status = "no_show" if request.form.get("kind") == "no_show" else "cancelled"
    db.execute(
        "UPDATE bookings SET status = ?, cancelled_reason = ? WHERE id = ?",
        (status, reason, booking_id),
    )
    record_audit(db, status, "booking", booking_id, f"{booking['booking_reference']}: {reason}")
    db.commit()
    flash(f"Booking {booking['booking_reference']} marked {status.replace('_', '-')}. The dates are available again.", "success")
    return admin_redirect()


@admin_required
def admin_resend_email(booking_id):
    cfg = current_app.config
    db = get_db()
    booking = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings
        JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE bookings.id = ?
        """,
        (booking_id,),
    ).fetchone()
    if booking is None:
        flash("Booking not found.", "error")
        return redirect(url_for("admin"))

    # Synchronous on purpose: the admin clicked the button and wants a verdict.
    try:
        send_booking_emails(cfg, booking, {"name": booking["kayak_name"]})
        db.execute("UPDATE bookings SET email_status = 'sent', email_error = NULL WHERE id = ?", (booking_id,))
        record_audit(db, "resent-email", "booking", booking_id, booking["booking_reference"])
        db.commit()
        flash(f"Emails for {booking['booking_reference']} sent.", "success")
    except Exception as error:
        db.execute(
            "UPDATE bookings SET email_status = 'failed', email_error = ? WHERE id = ?",
            (str(error), booking_id),
        )
        db.commit()
        flash(f"Email failed again: {error}", "error")
    return admin_redirect()


@admin_required
def admin_record_payment(booking_id):
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if booking is None:
        flash("Booking not found.", "error")
        return redirect(url_for("admin"))
    try:
        amount = int(request.form["amount"])
        if amount <= 0:
            raise ValueError
    except (KeyError, ValueError):
        flash("Enter a positive amount in rupees.", "error")
        return redirect(url_for("admin"))
    kind = "refund" if request.form.get("kind") == "refund" else "payment"
    method = request.form.get("method", "cash")
    if method not in ("cash", "card", "transfer"):
        method = "cash"
    note = request.form.get("note", "").strip()
    db.execute(
        "INSERT INTO payments (booking_id, amount, kind, method, note, recorded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            booking_id,
            amount,
            kind,
            method,
            note,
            g.user["email"],
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    record_audit(db, f"{kind}-recorded", "booking", booking_id, f"Rs {amount} {method} {note}".strip())
    db.commit()
    flash(f"{'Refund' if kind == 'refund' else 'Payment'} of Rs {amount} recorded for {booking['booking_reference']}.", "success")
    return admin_redirect()


KAYAK_STATUSES = ("active", "maintenance", "retired")
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def save_kayak_photo(file):
    """Store an uploaded kayak photo under assets/kayaks/. Returns URL path or (None, error)."""
    if not file or not file.filename:
        return None, None
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_PHOTO_EXTENSIONS:
        return None, "Photo must be a JPG, PNG, or WebP file."
    data = file.read()
    if len(data) > MAX_PHOTO_BYTES:
        return None, "Photo must be under 5 MB."
    name = f"{secrets.token_hex(6)}-{secure_filename(Path(file.filename).stem)[:40] or 'kayak'}{suffix}"
    folder = Path(current_app.config.get("UPLOAD_DIR") or BASE_DIR / "assets" / "kayaks")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return f"/assets/kayaks/{name}", None


def validate_kayak_form(form):
    values = {
        "name": form.get("name", "").strip(),
        "tagline": form.get("tagline", "").strip(),
        "capacity": form.get("capacity", "").strip(),
        "price_per_day": form.get("price_per_day", "").strip(),
    }
    errors = []
    if not values["name"]:
        errors.append("Name is required.")
    if not values["capacity"]:
        values["capacity"] = "1 adult"
    try:
        values["price_per_day"] = int(values["price_per_day"])
        if values["price_per_day"] <= 0:
            raise ValueError
    except ValueError:
        errors.append("Price per day must be a positive number of rupees.")
    return values, errors


@owner_required
def admin_fleet():
    db = get_db()
    month_start = local_today().replace(day=1).isoformat()
    kayaks = db.execute("SELECT * FROM kayaks ORDER BY status = 'retired', id").fetchall()
    stats = {
        row["kayak_id"]: row
        for row in db.execute(
            """
            SELECT kayak_id, COUNT(*) AS rentals, COALESCE(SUM(total_price), 0) AS revenue
            FROM bookings
            WHERE start_date >= ? AND status IN ('confirmed', 'on_water', 'returned')
            GROUP BY kayak_id
            """,
            (month_start,),
        ).fetchall()
    }
    maintenance = {}
    for row in db.execute("SELECT * FROM maintenance_log ORDER BY id DESC").fetchall():
        maintenance.setdefault(row["kayak_id"], []).append(row)
    damage = {}
    for row in db.execute("SELECT * FROM damage_reports ORDER BY status = 'resolved', id DESC").fetchall():
        damage.setdefault(row["kayak_id"], []).append(row)
    return render_template(
        "admin_fleet.html",
        kayaks=kayaks,
        stats=stats,
        maintenance=maintenance,
        damage=damage,
        month_label=local_today().strftime("%B"),
    )


@owner_required
def admin_kayak_save(kayak_id=None):
    db = get_db()
    values, errors = validate_kayak_form(request.form)
    photo_url, photo_error = save_kayak_photo(request.files.get("photo"))
    if photo_error:
        errors.append(photo_error)
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("admin_fleet"))

    if kayak_id is None:
        db.execute(
            "INSERT INTO kayaks (name, tagline, capacity, price_per_day, image_url, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (values["name"], values["tagline"], values["capacity"], values["price_per_day"],
             photo_url or "/assets/KayakTurquoise.jpg"),
        )
        record_audit(db, "created", "kayak", db.execute("SELECT last_insert_rowid()").fetchone()[0], values["name"])
        flash(f"{values['name']} added to the fleet.", "success")
    else:
        kayak = db.execute("SELECT * FROM kayaks WHERE id = ?", (kayak_id,)).fetchone()
        if kayak is None:
            flash("Kayak not found.", "error")
            return redirect(url_for("admin_fleet"))
        db.execute(
            "UPDATE kayaks SET name = ?, tagline = ?, capacity = ?, price_per_day = ?, image_url = ? WHERE id = ?",
            (values["name"], values["tagline"], values["capacity"], values["price_per_day"],
             photo_url or kayak["image_url"], kayak_id),
        )
        changes = "; ".join(
            f"{field}: {kayak[field]!r} -> {values[field]!r}"
            for field in ("name", "tagline", "capacity", "price_per_day")
            if kayak[field] != values[field]
        )
        if photo_url:
            changes = (changes + "; " if changes else "") + "photo updated"
        if changes:
            record_audit(db, "updated", "kayak", kayak_id, changes)
        flash(f"{values['name']} saved.", "success")
    db.commit()
    return redirect(url_for("admin_fleet"))


@owner_required
def admin_kayak_status(kayak_id):
    db = get_db()
    kayak = db.execute("SELECT * FROM kayaks WHERE id = ?", (kayak_id,)).fetchone()
    status = request.form.get("status", "")
    if kayak is None or status not in KAYAK_STATUSES:
        flash("Could not change kayak status.", "error")
        return redirect(url_for("admin_fleet"))
    db.execute(
        "UPDATE kayaks SET status = ?, active = ? WHERE id = ?",
        (status, 1 if status == "active" else 0, kayak_id),
    )
    record_audit(db, f"status-{status}", "kayak", kayak_id, kayak["name"])
    db.commit()
    flash(f"{kayak['name']} is now {status}.", "success")
    return redirect(url_for("admin_fleet"))


@owner_required
def admin_kayak_maintenance(kayak_id):
    db = get_db()
    kayak = db.execute("SELECT * FROM kayaks WHERE id = ?", (kayak_id,)).fetchone()
    description = request.form.get("description", "").strip()
    if kayak is None or not description:
        flash("Describe the maintenance work.", "error")
        return redirect(url_for("admin_fleet"))
    cost = None
    if request.form.get("cost"):
        try:
            cost = max(0, int(request.form["cost"]))
        except ValueError:
            cost = None
    next_due = request.form.get("next_due", "").strip() or None
    if next_due:
        try:
            parse_booking_date(next_due)
        except ValueError:
            next_due = None
    db.execute(
        "INSERT INTO maintenance_log (kayak_id, description, cost, next_due, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kayak_id, description, cost, next_due, g.user["email"],
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    record_audit(db, "maintenance-logged", "kayak", kayak_id, f"{kayak['name']}: {description}")
    db.commit()
    flash("Maintenance logged.", "success")
    return redirect(url_for("admin_fleet"))


@owner_required
def admin_kayak_damage(kayak_id):
    db = get_db()
    kayak = db.execute("SELECT * FROM kayaks WHERE id = ?", (kayak_id,)).fetchone()
    description = request.form.get("description", "").strip()
    if kayak is None or not description:
        flash("Describe the damage.", "error")
        return redirect(url_for("admin_fleet"))
    severity = request.form.get("severity", "minor")
    if severity not in ("minor", "major", "unusable"):
        severity = "minor"
    db.execute(
        "INSERT INTO damage_reports (kayak_id, description, severity, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (kayak_id, description, severity, g.user["email"],
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    record_audit(db, "damage-reported", "kayak", kayak_id, f"{kayak['name']} ({severity}): {description}")
    if severity == "unusable" and kayak["status"] == "active":
        db.execute("UPDATE kayaks SET status = 'maintenance', active = 0 WHERE id = ?", (kayak_id,))
        record_audit(db, "status-maintenance", "kayak", kayak_id, "auto: unusable damage report")
        flash(f"{kayak['name']} moved to maintenance — it can't take new bookings until repaired.", "warning")
    db.commit()
    flash("Damage report filed.", "success")
    return redirect(url_for("admin_fleet"))


@owner_required
def admin_damage_resolve(report_id):
    db = get_db()
    report = db.execute("SELECT * FROM damage_reports WHERE id = ?", (report_id,)).fetchone()
    if report is None or report["status"] == "resolved":
        return redirect(url_for("admin_fleet"))
    db.execute(
        "UPDATE damage_reports SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), report_id),
    )
    record_audit(db, "damage-resolved", "kayak", report["kayak_id"], report["description"][:120])
    db.commit()
    flash("Damage marked resolved.", "success")
    return redirect(url_for("admin_fleet"))


@admin_required
def admin_calendar():
    db = get_db()
    try:
        start = parse_booking_date(request.args.get("start", local_today().isoformat()))
    except ValueError:
        start = local_today()
    days = [start + timedelta(days=offset) for offset in range(7)]
    end = days[-1]

    kayaks = db.execute("SELECT * FROM kayaks WHERE status != 'retired' ORDER BY id").fetchall()
    rows = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE bookings.status IN ('confirmed', 'on_water', 'returned')
          AND bookings.start_date <= ? AND bookings.end_date >= ?
        """,
        (end.isoformat(), start.isoformat()),
    ).fetchall()

    grid = {}
    for booking in rows:
        b_start = parse_booking_date(booking["start_date"])
        b_end = parse_booking_date(booking["end_date"])
        for day in days:
            if b_start <= day <= b_end:
                grid[(booking["kayak_id"], day.isoformat())] = booking

    return render_template(
        "admin_calendar.html",
        kayaks=kayaks,
        days=days,
        grid=grid,
        today=local_today(),
        prev_start=(start - timedelta(days=7)).isoformat(),
        next_start=(start + timedelta(days=7)).isoformat(),
        window_label=f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}",
    )


def customer_identity(booking):
    phone = (booking["customer_phone"] or "").replace(" ", "")
    if phone:
        return phone
    email = (booking["customer_email"] or "").strip().lower()
    return email or booking["customer_name"].strip().lower()


@admin_required
def admin_more():
    return render_template("admin_more.html")


@owner_required
def admin_customers():
    db = get_db()
    q = request.args.get("q", "").strip().lower()
    bookings = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings JOIN kayaks ON kayaks.id = bookings.kayak_id
        ORDER BY bookings.start_date DESC
        """
    ).fetchall()
    customers = {}
    for booking in bookings:
        identity = customer_identity(booking)
        entry = customers.setdefault(identity, {
            "identity": identity,
            "name": booking["customer_name"],
            "email": booking["customer_email"],
            "phone": booking["customer_phone"],
            "visits": 0,
            "cancellations": 0,
            "spent": 0,
            "last_visit": booking["start_date"],
        })
        if booking["status"] in BLOCKING_STATUSES:
            entry["visits"] += 1
            entry["spent"] += booking["total_price"] or 0
            entry["last_visit"] = max(entry["last_visit"], booking["start_date"])
        elif booking["status"] in ("cancelled", "no_show"):
            entry["cancellations"] += 1
    notes = {
        row["identity"]: row
        for row in db.execute("SELECT * FROM customer_notes").fetchall()
    }
    result = sorted(customers.values(), key=lambda c: c["last_visit"], reverse=True)
    if q:
        result = [
            c for c in result
            if q in (c["name"] or "").lower() or q in (c["email"] or "").lower()
            or q in (c["phone"] or "").lower()
        ]
    return render_template("admin_customers.html", customers=result, notes=notes, q=q)


@owner_required
def admin_customer_note():
    db = get_db()
    identity = request.form.get("identity", "").strip()
    if not identity:
        return redirect(url_for("admin_customers"))
    note = request.form.get("note", "").strip()
    flagged = 1 if request.form.get("flagged") else 0
    db.execute(
        """
        INSERT INTO customer_notes (identity, note, flagged, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(identity) DO UPDATE SET note = ?, flagged = ?, updated_by = ?, updated_at = ?
        """,
        (identity, note, flagged, g.user["email"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
         note, flagged, g.user["email"], datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    record_audit(db, "customer-note", "customer", identity, note[:120])
    db.commit()
    flash("Customer note saved.", "success")
    return redirect(url_for("admin_customers", q=request.form.get("q", "")))


@owner_required
def admin_promos():
    db = get_db()
    promos = db.execute("SELECT * FROM promo_codes ORDER BY active DESC, id DESC").fetchall()
    return render_template("admin_promos.html", promos=promos)


@owner_required
def admin_promo_create():
    db = get_db()
    code = request.form.get("code", "").strip().upper()
    kind = request.form.get("kind", "percent")
    if kind not in ("percent", "fixed"):
        kind = "percent"
    try:
        value = int(request.form.get("value", ""))
        if value <= 0 or (kind == "percent" and value > 100):
            raise ValueError
    except ValueError:
        flash("Value must be a positive number (1-100 for percent).", "error")
        return redirect(url_for("admin_promos"))
    if not code or not code.replace("-", "").isalnum():
        flash("Code must be letters and numbers.", "error")
        return redirect(url_for("admin_promos"))
    max_uses = None
    if request.form.get("max_uses"):
        try:
            max_uses = max(1, int(request.form["max_uses"]))
        except ValueError:
            max_uses = None
    valid_from = request.form.get("valid_from") or None
    valid_to = request.form.get("valid_to") or None
    try:
        db.execute(
            "INSERT INTO promo_codes (code, kind, value, valid_from, valid_to, max_uses, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, kind, value, valid_from, valid_to, max_uses,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    except sqlite3.IntegrityError:
        flash("That code already exists.", "error")
        return redirect(url_for("admin_promos"))
    record_audit(db, "created", "promo", code, f"{kind} {value}")
    db.commit()
    flash(f"Promo code {code} created.", "success")
    return redirect(url_for("admin_promos"))


@owner_required
def admin_promo_toggle(promo_id):
    db = get_db()
    promo = db.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,)).fetchone()
    if promo is None:
        return redirect(url_for("admin_promos"))
    new_state = 0 if promo["active"] else 1
    db.execute("UPDATE promo_codes SET active = ? WHERE id = ?", (new_state, promo_id))
    record_audit(db, "enabled" if new_state else "disabled", "promo", promo["code"])
    db.commit()
    flash(f"{promo['code']} {'enabled' if new_state else 'disabled'}.", "success")
    return redirect(url_for("admin_promos"))


@owner_required
def admin_reports():
    db = get_db()
    month = request.args.get("month", local_today().strftime("%Y-%m"))
    try:
        month_start = parse_booking_date(month + "-01")
    except ValueError:
        month = local_today().strftime("%Y-%m")
        month_start = parse_booking_date(month + "-01")
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    days_in_month = (next_month - month_start).days

    bookings = db.execute(
        """
        SELECT bookings.*, kayaks.name AS kayak_name
        FROM bookings JOIN kayaks ON kayaks.id = bookings.kayak_id
        WHERE bookings.start_date >= ? AND bookings.start_date < ?
        """,
        (month_start.isoformat(), next_month.isoformat()),
    ).fetchall()
    active = [b for b in bookings if b["status"] in BLOCKING_STATUSES]
    cancelled = [b for b in bookings if b["status"] in ("cancelled", "no_show")]

    revenue = sum(b["total_price"] or 0 for b in active)
    by_kayak = {}
    booked_days = {}
    weekday_counts = [0] * 7
    source_counts = {}
    for b in active:
        by_kayak.setdefault(b["kayak_name"], {"rentals": 0, "revenue": 0})
        by_kayak[b["kayak_name"]]["rentals"] += 1
        by_kayak[b["kayak_name"]]["revenue"] += b["total_price"] or 0
        booked_days[b["kayak_id"]] = booked_days.get(b["kayak_id"], 0) + count_days(
            parse_booking_date(b["start_date"]), parse_booking_date(b["end_date"])
        )
        weekday_counts[parse_booking_date(b["start_date"]).weekday()] += 1
        source_counts[b["source"]] = source_counts.get(b["source"], 0) + 1

    payments_rows = db.execute(
        """
        SELECT method, kind, SUM(amount) AS total
        FROM payments WHERE created_at >= ? AND created_at < ?
        GROUP BY method, kind
        """,
        (month_start.isoformat(), next_month.isoformat()),
    ).fetchall()

    kayaks = db.execute("SELECT * FROM kayaks WHERE status != 'retired' ORDER BY id").fetchall()
    utilization = [
        {
            "name": kayak["name"],
            "booked": booked_days.get(kayak["id"], 0),
            "percent": round(100 * booked_days.get(kayak["id"], 0) / days_in_month),
        }
        for kayak in kayaks
    ]
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    busiest = max(zip(weekday_counts, weekdays))[1] if any(weekday_counts) else None

    prev_month = (month_start - timedelta(days=1)).strftime("%Y-%m")
    next_month_str = next_month.strftime("%Y-%m")
    return render_template(
        "admin_reports.html",
        month=month,
        month_label=month_start.strftime("%B %Y"),
        revenue=revenue,
        booking_count=len(active),
        cancelled_count=len(cancelled),
        by_kayak=by_kayak,
        utilization=utilization,
        payments_rows=payments_rows,
        source_counts=source_counts,
        busiest=busiest,
        prev_month=prev_month,
        next_month=next_month_str,
        show_next=next_month <= local_today(),
    )


@owner_required
def admin_users():
    db = get_db()
    users = db.execute("SELECT id, email, role, active, created_at FROM users ORDER BY id").fetchall()
    return render_template("admin_users.html", users=users)


@owner_required
def admin_user_create():
    db = get_db()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "staff")
    if role not in ("owner", "staff"):
        role = "staff"
    if "@" not in email or len(password) < 8:
        flash("Enter a valid email and a password of at least 8 characters.", "error")
        return redirect(url_for("admin_users"))
    try:
        db.execute(
            "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(password), role,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    except sqlite3.IntegrityError:
        flash("That email already has an account.", "error")
        return redirect(url_for("admin_users"))
    record_audit(db, "created", "user", email, role)
    db.commit()
    flash(f"{email} can now sign in as {role}.", "success")
    return redirect(url_for("admin_users"))


@owner_required
def admin_user_toggle(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return redirect(url_for("admin_users"))
    if user["id"] == g.user["id"]:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("admin_users"))
    new_state = 0 if user["active"] else 1
    db.execute("UPDATE users SET active = ? WHERE id = ?", (new_state, user_id))
    record_audit(db, "activated" if new_state else "deactivated", "user", user["email"])
    db.commit()
    flash(f"{user['email']} {'reactivated' if new_state else 'deactivated'}.", "success")
    return redirect(url_for("admin_users"))


@owner_required
def admin_settings():
    db = get_db()
    if request.method == "POST":
        changed = []
        for key in DEFAULT_SETTINGS:
            value = request.form.get(key, "").strip()
            if value:
                db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                    (key, value, value),
                )
                changed.append(key)
        if changed:
            record_audit(db, "updated", "settings", None, ", ".join(changed))
            db.commit()
            flash("Settings saved. The website updates immediately.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html", settings=get_settings(db))


@owner_required
def admin_audit():
    db = get_db()
    entries = db.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT 200"
    ).fetchall()
    return render_template("audit.html", entries=entries)


@owner_required
def admin_test_email():
    cfg = current_app.config
    recipient = request.form.get("test_email", "").strip() or cfg["OWNER_EMAIL"]
    try:
        send_email(
            cfg,
            recipient,
            "Mahebourg Kayak mail server test",
            "This is a test email from the Mahebourg Lagoon Kayak Rentals admin page.",
            cfg["OWNER_EMAIL"],
        )
        flash(f"Test email sent to {recipient}.", "success")
    except Exception as error:
        flash(f"Test email failed: {error}", "error")
    return admin_redirect()


# --- App factory --------------------------------------------------------


def create_app(config_overrides=None):
    app = Flask(__name__)
    app.config.update(default_config())
    if config_overrides:
        app.config.update(config_overrides)

    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = "test-secret" if app.config.get("TESTING") else ""
    if app.config["SECRET_KEY"] in _PLACEHOLDER_SECRETS and not (DEBUG or app.config.get("TESTING")):
        raise RuntimeError(
            "KAYAK_SECRET_KEY is not set. Generate one with "
            "'python -c \"import secrets; print(secrets.token_urlsafe(32))\"' and put it in .env, "
            "or set KAYAK_DEBUG=true for local development."
        )

    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.context_processor
    def inject_admin_context():
        context = {"smtp_configured": smtp_is_configured(app.config)}
        try:
            context["site"] = get_settings(get_db())
        except sqlite3.Error:
            context["site"] = dict(DEFAULT_SETTINGS, whatsapp_link="https://wa.me/")
        context["is_owner"] = bool(getattr(g, "user", None) and g.user["role"] == "owner")
        return context
    app.before_request(check_csrf)
    app.before_request(load_logged_in_user)
    app.teardown_appcontext(close_db)

    app.add_url_rule("/", "index", index)
    app.add_url_rule("/assets/<path:filename>", "assets", assets)
    app.add_url_rule("/api/availability", "availability", availability)
    app.add_url_rule("/api/promo", "promo_check", promo_check)
    app.add_url_rule("/book", "book", book, methods=["POST"])
    app.add_url_rule("/booking/<reference>", "booking_success", booking_success)
    app.add_url_rule("/booking/<reference>/calendar.ics", "booking_ics", booking_ics)
    app.add_url_rule("/admin/login", "admin_login", admin_login, methods=["GET", "POST"])
    app.add_url_rule("/admin/logout", "admin_logout", admin_logout, methods=["POST"])
    app.add_url_rule("/admin", "admin", admin)
    app.add_url_rule("/admin/today", "admin_today", admin_today)
    app.add_url_rule("/admin/bookings/new", "admin_new_booking", admin_new_booking, methods=["GET", "POST"])
    app.add_url_rule("/admin/calendar", "admin_calendar", admin_calendar)
    app.add_url_rule("/admin/more", "admin_more", admin_more)
    app.add_url_rule("/admin/customers", "admin_customers", admin_customers)
    app.add_url_rule("/admin/customers/note", "admin_customer_note", admin_customer_note, methods=["POST"])
    app.add_url_rule("/admin/reports", "admin_reports", admin_reports)
    app.add_url_rule("/admin/users", "admin_users", admin_users)
    app.add_url_rule("/admin/users/new", "admin_user_create", admin_user_create, methods=["POST"])
    app.add_url_rule("/admin/users/<int:user_id>/toggle", "admin_user_toggle", admin_user_toggle, methods=["POST"])
    app.add_url_rule("/admin/settings", "admin_settings", admin_settings, methods=["GET", "POST"])
    app.add_url_rule("/admin/promos", "admin_promos", admin_promos)
    app.add_url_rule("/admin/promos/new", "admin_promo_create", admin_promo_create, methods=["POST"])
    app.add_url_rule("/admin/promos/<int:promo_id>/toggle", "admin_promo_toggle", admin_promo_toggle, methods=["POST"])
    app.add_url_rule("/admin/fleet", "admin_fleet", admin_fleet)
    app.add_url_rule("/admin/fleet/new", "admin_kayak_new", admin_kayak_save, methods=["POST"])
    app.add_url_rule("/admin/fleet/<int:kayak_id>/save", "admin_kayak_save", admin_kayak_save, methods=["POST"])
    app.add_url_rule("/admin/fleet/<int:kayak_id>/status", "admin_kayak_status", admin_kayak_status, methods=["POST"])
    app.add_url_rule(
        "/admin/fleet/<int:kayak_id>/maintenance", "admin_kayak_maintenance", admin_kayak_maintenance, methods=["POST"]
    )
    app.add_url_rule("/admin/fleet/<int:kayak_id>/damage", "admin_kayak_damage", admin_kayak_damage, methods=["POST"])
    app.add_url_rule(
        "/admin/damage/<int:report_id>/resolve", "admin_damage_resolve", admin_damage_resolve, methods=["POST"]
    )
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/checkout", "admin_checkout_booking", admin_checkout_booking, methods=["POST"]
    )
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/checkin", "admin_checkin_booking", admin_checkin_booking, methods=["POST"]
    )
    app.add_url_rule("/admin/bookings/<int:booking_id>", "admin_booking_detail", admin_booking_detail)
    app.add_url_rule("/admin/bookings.csv", "admin_bookings_csv", admin_bookings_csv)
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/update", "admin_update_booking", admin_update_booking, methods=["POST"]
    )
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/cancel", "admin_cancel_booking", admin_cancel_booking, methods=["POST"]
    )
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/resend-email", "admin_resend_email", admin_resend_email, methods=["POST"]
    )
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/payments", "admin_record_payment", admin_record_payment, methods=["POST"]
    )
    app.add_url_rule("/admin/audit", "admin_audit", admin_audit)
    app.add_url_rule("/admin/test-email", "admin_test_email", admin_test_email, methods=["POST"])

    init_db(app.config["DATABASE"], app.config["OWNER_EMAIL"], app.config["OWNER_PASSWORD"])
    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=DEBUG)
