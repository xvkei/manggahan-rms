#!/usr/bin/env bash
# One-command setup and run for macOS / Linux.
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -q -r requirements.txt
python manage.py makemigrations reservations
python manage.py migrate
python manage.py seed_demo
echo ""
echo "Open http://127.0.0.1:8000 (player site) and http://127.0.0.1:8000/staff/ (admin)"
python manage.py runserver
