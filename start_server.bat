@echo off
cd /d "%~dp0"
set PYTHONPATH=backend
echo Starting Uvicorn development server...
venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
