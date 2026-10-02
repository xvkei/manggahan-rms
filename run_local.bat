@echo off
REM One-click setup and run for Windows. Needs Python 3.10+ installed (check "Add to PATH").
cd /d "%~dp0"
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python manage.py makemigrations reservations
python manage.py migrate
python manage.py seed_demo
echo.
echo Open http://127.0.0.1:8000 (player site) and http://127.0.0.1:8000/staff/ (admin)
python manage.py runserver
pause
