# Manggahan Sports Complex: Reservation Management System

A web-based reservation system for Manggahan Sports Complex (General Trias, Cavite). It has two sides that share one database:

- **Player side:** players book a court in three steps, with no account needed (just a mobile number).
- **Admin side:** front desk staff and managers run the day, handle walk-ins and sales, approve refunds, and see reports.

Built with **Python (Django)**, **HTML**, **CSS**, and **JavaScript**. It uses **SQLite** by default, so there's nothing to set up, and it can switch to **PostgreSQL** later.

> **Demo mode.** No real SMS gateway or payment provider is connected. SMS messages are stored in an outbox (and verification codes are shown on screen). Online payments go through a simulated checkout page. See [Connecting real services](#connecting-real-services).

---

## Features

**Player side**
- Live slot availability with prices (open, taken, promo, evening rate)
- Guest booking with mobile number + 6-digit SMS code (no account needed)
- Optional accounts for regulars, with loyalty points
- Automatic discounts by player type (student, member, senior/PWD). Discounts don't stack: each hour gets the biggest single discount.
- Equipment add-ons (rackets, shuttlecocks, lockers)
- Pay online (GCash, Maya, card) or pay at the front desk
- Booking confirmation with QR code for check-in
- My reservations, reschedule, and cancel with the tiered refund policy
- SMS reminders for payment deadlines and game time

**Admin side**
- Today's schedule: every court on one timeline, colored by status, with a "now" line
- Check-in, payment collection, and no-show handling with a grace period
- Reservations & refunds: search, filters, and refund approvals with the amount already computed
- Walk-in & sales: book on the spot, sell drinks/rentals/lockers on one bill, compute change, print receipts
- League and tournament block booking (weekly repeat with conflict check, one invoice)
- Courts, rates & promos: edit rates, discounts, off-peak promos, memberships, packages, loyalty rules
- Block a court for maintenance (affected players get full refunds and an SMS automatically)
- Reports: revenue by source, bookings by sport, utilization, peak-hours heatmap, cancellations. Export to Excel (CSV) or print/save as PDF.
- Staff accounts, role permissions (front desk vs. manager), and an activity log

**Automatic rules** (run by `python manage.py run_jobs`, and lazily once a minute while the site is in use)
- Unpaid bookings are released at their payment deadline
- Payment-deadline and game reminders are texted
- Finished games are marked completed

---

## Run it at school with GitHub Codespaces (nothing to install)

Codespaces runs the whole project in your browser, so any school computer with internet works.

1. Go to your repository on github.com and sign in.
2. Click the green **Code** button, open the **Codespaces** tab, and click **Create codespace on main**.
3. Wait 2 to 4 minutes the first time. It installs Django, creates the database, and loads demo data automatically.
4. The app starts by itself. When the "Manggahan RMS" port pops up, click **Open in Browser**. If it doesn't pop up, open the **Ports** tab and click the globe icon next to port 8000.
5. When you're done, stop the codespace (Code button → Codespaces → ⋯ → Stop) to save your free monthly hours.

To restart the app in a codespace terminal: `python manage.py runserver 0.0.0.0:8000`

---

## Run it on your own computer

**Windows:** install Python 3.10 or newer from python.org (tick **Add Python to PATH**), then double-click `run_local.bat`.

**macOS / Linux:** run `./run_local.sh`

**Manual steps (any system):**
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py makemigrations reservations
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Then open:
- Player site: http://127.0.0.1:8000
- Admin side: http://127.0.0.1:8000/staff/
- Database admin (raw tables): http://127.0.0.1:8000/django-admin/

---

## Demo accounts

| Who | Sign in at | Username | Password |
|---|---|---|---|
| Manager (full access) | /staff/ | `manager` | `manager123` |
| Front desk | /staff/ | `frontdesk` | `frontdesk123` |
| Player with an account | /accounts/login/ | `0917 123 4567` | `player123` |
| Database admin | /django-admin/ | `admin` | `admin12345` |

Guests don't need an account: book from the home page and use any 11-digit mobile number. In demo mode, the 6-digit code is shown on the screen.

To start over with fresh demo data: `python manage.py seed_demo --reset`

---

## Putting this project on GitHub

### Option A: upload in the browser (no Git needed)
1. Unzip the project.
2. On github.com, click **+** → **New repository**. Name it (for example `manggahan-rms`), leave "Add a README" **unchecked**, and click **Create repository**.
3. On the new repository page, click **uploading an existing file**.
4. Open the unzipped `manggahan-rms` folder, select **everything inside it** (including `.devcontainer` and `.gitignore`), and drag it into the browser.
5. Click **Commit changes**.
6. Check that the `.devcontainer` folder appears in the file list. If it doesn't, click **Add file → Create new file**, type `.devcontainer/devcontainer.json` as the name, paste the contents of that file, and commit.

### Option B: with Git
```bash
cd manggahan-rms
git init
git add .
git commit -m "Manggahan Sports Complex reservation system"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/manggahan-rms.git
git push -u origin main
```

### Working as a group
- Add groupmates under **Settings → Collaborators**.
- Each person can open their own codespace or clone the repo.
- After the first `makemigrations`, commit the generated `reservations/migrations/0001_initial.py` so everyone uses the same database structure.
- Never commit `db.sqlite3` or `.env` (they're already in `.gitignore`).

---

## Project structure

```
manggahan-rms/
├── config/                  Django project settings and URLs
├── reservations/            The app
│   ├── pricing.py           Business rules: rates, discounts, refunds, points (no Django, easy to test)
│   ├── services.py          Availability, payments, cancellations, no-shows, auto-release, SMS outbox
│   ├── models.py            Database tables
│   ├── views_player.py      Player pages
│   ├── views_staff.py       Admin pages
│   ├── tests.py             Automated tests (python manage.py test)
│   └── management/commands/
│       ├── seed_demo.py     Loads courts, rates, staff, and sample bookings
│       └── run_jobs.py      Automatic rules for a scheduler (cron)
├── templates/               HTML pages (player/, staff/, partials/)
├── static/                  CSS and JavaScript
├── .devcontainer/           GitHub Codespaces setup
├── run_local.bat / .sh      One-click local setup
└── requirements.txt
```

### Database tables
Courts, Players, Reservations, Reservation items (equipment add-ons), Payments, Refunds, Items, Sales and sale lines, Memberships and packages, Player packages, Promos, Maintenance blocks, League bookings, Staff profiles, Activity log, SMS outbox, Phone verifications, Site settings.

---

## Business rules (summary)

| Rule | Default | Where to change |
|---|---|---|
| Daytime / evening rates | Per court, evening from 6:00 PM | Admin → Courts, rates & promos |
| Student / member discount | 10% / 10% | Admin → Courts, rates & promos |
| Senior citizen / PWD discount | 20% (by law) | Fixed |
| Discount stacking | Biggest single discount per hour | `reservations/pricing.py` |
| Pay-at-desk deadline | 24 hours after booking, at least 3 hours before the game | Admin → Booking rules |
| No-show grace period | 15 minutes | Admin → Booking rules |
| Refunds | 100% if 24+ hrs before, 50% if less, 0% after start; 100% if the complex cancels | `reservations/pricing.py` |
| Loyalty points | 1 point per ₱100; 100 points = 1 free hour | Admin → Booking rules |

---

## Connecting real services

| Service | Demo behavior | To go live |
|---|---|---|
| SMS (codes, confirmations, reminders) | Saved to the SMS outbox (Admin → Staff & activity) | Replace `send_sms()` in `reservations/services.py` with a call to a Philippine SMS gateway such as Semaphore |
| Online payments (GCash, Maya, card) | Simulated checkout page | Replace the `pay` view in `views_player.py` with a payment gateway such as PayMongo (checkout + webhook calls `services.record_payment`) |
| Automatic rules | Run lazily while pages load | Schedule `python manage.py run_jobs` every 5 minutes |
| Database | SQLite file | Set `DB_ENGINE=postgres` and the `DB_*` values in `.env`, install `psycopg[binary]` |

Before deploying publicly: set `DJANGO_DEBUG=False`, a long random `DJANGO_SECRET_KEY`, your domain in `DJANGO_ALLOWED_HOSTS`, run `python manage.py collectstatic`, and serve with a production server (for example gunicorn).

---

## Running the tests
```bash
python manage.py test
```
