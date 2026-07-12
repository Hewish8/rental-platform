# Mahebourg Lagoon Kayak Rentals

Flask booking website + owner console for kayak rentals in Mahebourg Lagoon, Mauritius.

## Run locally

```powershell
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000/`.

## First-run setup

Copy `.env.example` to `.env` and fill in:

- `KAYAK_SECRET_KEY` — long random string (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- `KAYAK_OWNER_EMAIL` + `KAYAK_OWNER_PASSWORD` — creates the owner account on first start
- SMTP settings (optional) — for booking confirmation emails; Gmail needs an App Password

## Owner console

Sign in at `http://127.0.0.1:5000/admin`:

- **Today** — KPIs, departures with one-tap check-out/check-in, late-return alerts, tomorrow preview, attention feed
- **Bookings** — searchable, filterable list; detail pages with edit, payments/refunds, resend email, cancel with reason
- **Calendar** — kayaks × days week grid; tap an empty cell to book a walk-up
- **Fleet** — add/edit kayaks, photos, prices, maintenance log, damage reports (owner only)
- **More** — customers, monthly reports, promo codes, staff accounts, activity log, site settings (owner only)

Staff accounts (created under More → Staff) can run the day but cannot change the fleet, prices, promos, or accounts.

## Tests

```powershell
python -m pytest tests/ -q
```

## Notes

- Bookings are never hard-deleted; they are cancelled with a reason and audited.
- Availability, prices, and promo discounts are always re-validated server-side inside a write transaction.
- The database is `kayak_rental.db` (SQLite). Back it up before upgrades; `backups/` is git-ignored.
