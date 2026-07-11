# Mahebourg Lagoon Kayak Rentals

Flask booking website for kayak rentals in Mahebourg Lagoon.

## Run locally

```powershell
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000/
```

## Owner bookings dashboard

Set `KAYAK_ADMIN_TOKEN` in `.env` to a long random value (never commit or share it), then open:

```text
http://127.0.0.1:5000/admin?token=YOUR_TOKEN_FROM_ENV
```

## Enable real email notifications

Edit `.env` and fill in the SMTP values.

For Gmail, create an App Password in your Google account and use it as `KAYAK_SMTP_PASSWORD`.

```text
KAYAK_OWNER_EMAIL=your-email@example.com
KAYAK_FROM_EMAIL=your-email@example.com
KAYAK_SMTP_HOST=smtp.gmail.com
KAYAK_SMTP_PORT=587
KAYAK_SMTP_USE_TLS=true
KAYAK_SMTP_USERNAME=your-email@example.com
KAYAK_SMTP_PASSWORD=your-email-app-password
```

Restart the Flask app after editing `.env`.

## What happens on booking

- The booking is saved in `kayak_rental.db`.
- The selected dates become unavailable.
- The customer sees a confirmation reference.
- The owner and customer receive email alerts when SMTP is configured.
- If email fails, the booking still stays saved and the dashboard shows `failed`.
