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
            if candidate < date.today():
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
    kayaks = db.execute("SELECT * FROM kayaks WHERE active = 1").fetchall()
    return (
        render_template(
            "index.html",
            kayaks=kayaks,
            today=date.today().isoformat(),
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
    kayak = db.execute("SELECT id FROM kayaks WHERE id = ? AND active = 1", (kayak_id,)).fetchone()
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


def validate_booking_form(db, form):
    """Returns (values, errors, kayak, booking_date)."""
    values = {
        "kayak_id": form.get("kayak_id", "").strip(),
        "booking_date": form.get("booking_date", "").strip(),
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
        kayak = db.execute("SELECT * FROM kayaks WHERE id = ? AND active = 1", (kayak_id,)).fetchone()
        if kayak is None:
            errors["kayak_id"] = "That kayak is no longer available. Please pick another."

    booking_date = None
    try:
        booking_date = parse_booking_date(values["booking_date"])
    except ValueError:
        errors["booking_date"] = "Please pick a valid booking date."
    else:
        if booking_date < date.today():
            errors["booking_date"] = "That date has already passed. Please pick an upcoming date."
        elif kayak is not None and not date_range_is_available(db, kayak["id"], booking_date, booking_date):
            errors["booking_date"] = "That kayak is already booked on this date. Please pick another date."

    if not values["customer_name"]:
        errors["customer_name"] = "Please enter your full name."
    if "@" not in values["customer_email"]:
        errors["customer_email"] = "Please enter a valid email address."
    if len(values["notes"]) > 2000:
        errors["notes"] = "Please keep your message under 2000 characters."

    return values, errors, kayak, booking_date


def conflict_suggestions(db, kayak, booking_date):
    if kayak is None or booking_date is None:
        return None
    return nearest_available_dates(db, kayak["id"], booking_date)


def book():
    db = get_db()
    values, errors, kayak, booking_date = validate_booking_form(db, request.form)
    if errors:
        suggestions = None
        if "already booked" in errors.get("booking_date", ""):
            suggestions = conflict_suggestions(db, kayak, booking_date)
        return render_index(values, errors, 400, suggestions)

    start_date = booking_date
    end_date = booking_date
    kayak_id = kayak["id"]

    booking = {
        "kayak_id": kayak_id,
        "booking_reference": generate_booking_reference(),
        "customer_name": values["customer_name"],
        "customer_email": values["customer_email"],
        "customer_phone": values["customer_phone"],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "notes": values["notes"],
        "total_price": count_days(start_date, end_date) * kayak["price_per_day"],
    }

    # Check availability and insert atomically: BEGIN IMMEDIATE takes the write
    # lock up front so two simultaneous requests cannot both pass the check.
    booking_id = None
    for _attempt in range(2):
        try:
            db.execute("BEGIN IMMEDIATE")
            if not date_range_is_available(db, kayak_id, start_date, end_date):
                db.rollback()
                errors["booking_date"] = "That kayak was just booked on this date. Please pick another date."
                return render_index(values, errors, 409, conflict_suggestions(db, kayak, booking_date))
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
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        errors["booking_date"] = "That kayak was just booked on this date. Please pick another date."
        return render_index(values, errors, 409, conflict_suggestions(db, kayak, booking_date))

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
        abort(400, description="Invalid or missing CSRF token. Please reload the page and try again.")


def load_logged_in_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)
        ).fetchone()


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
                next_path = url_for("admin")
            return redirect(next_path)
        flash("Wrong email or password.", "error")
    return render_template("login.html")


def admin_logout():
    session.clear()
    flash("You are signed out.", "success")
    return redirect(url_for("admin_login"))


@admin_required
def admin():
    cfg = current_app.config
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
        smtp_configured=smtp_is_configured(cfg),
        owner_email=cfg["OWNER_EMAIL"],
        smtp_host=cfg["SMTP_HOST"],
        smtp_port=cfg["SMTP_PORT"],
    )


@admin_required
def admin_update_booking(booking_id):
    db = get_db()
    form = request.form
    try:
        kayak_id = int(form["kayak_id"])
        booking_date = parse_booking_date(form["booking_date"])
    except (KeyError, ValueError):
        flash("Booking could not be updated. Check the kayak and booking date.", "error")
        return redirect(url_for("admin"))

    start_date = booking_date
    end_date = booking_date

    kayak = db.execute("SELECT * FROM kayaks WHERE id = ? AND active = 1", (kayak_id,)).fetchone()
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
    return redirect(url_for("admin"))


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
    db.commit()
    flash(f"Booking {booking['booking_reference']} marked {status.replace('_', '-')}. The dates are available again.", "success")
    return redirect(url_for("admin"))


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
        db.commit()
        flash(f"Emails for {booking['booking_reference']} sent.", "success")
    except Exception as error:
        db.execute(
            "UPDATE bookings SET email_status = 'failed', email_error = ? WHERE id = ?",
            (str(error), booking_id),
        )
        db.commit()
        flash(f"Email failed again: {error}", "error")
    return redirect(url_for("admin"))


@admin_required
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
    return redirect(url_for("admin"))


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
    app.before_request(check_csrf)
    app.before_request(load_logged_in_user)
    app.teardown_appcontext(close_db)

    app.add_url_rule("/", "index", index)
    app.add_url_rule("/assets/<path:filename>", "assets", assets)
    app.add_url_rule("/api/availability", "availability", availability)
    app.add_url_rule("/book", "book", book, methods=["POST"])
    app.add_url_rule("/booking/<reference>", "booking_success", booking_success)
    app.add_url_rule("/booking/<reference>/calendar.ics", "booking_ics", booking_ics)
    app.add_url_rule("/admin/login", "admin_login", admin_login, methods=["GET", "POST"])
    app.add_url_rule("/admin/logout", "admin_logout", admin_logout, methods=["POST"])
    app.add_url_rule("/admin", "admin", admin)
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/update", "admin_update_booking", admin_update_booking, methods=["POST"]
    )
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/cancel", "admin_cancel_booking", admin_cancel_booking, methods=["POST"]
    )
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/resend-email", "admin_resend_email", admin_resend_email, methods=["POST"]
    )
    app.add_url_rule("/admin/test-email", "admin_test_email", admin_test_email, methods=["POST"])

    init_db(app.config["DATABASE"], app.config["OWNER_EMAIL"], app.config["OWNER_PASSWORD"])
    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=DEBUG)
