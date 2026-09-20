@echo off
echo =======================================================================
echo                 LEMMA APPLICATION SETUP AND DEVELOPMENT RUNNER
echo =======================================================================
echo.

cd /d "%~dp0"

:: 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to your system PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b 1
)

:: 2. Setup Virtual Environment
if not exist "venv" (
    echo [INFO] Creating Python virtual environment [venv]...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [INFO] Virtual environment [venv] already exists.
)

:: 3. Activate venv and install requirements
echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

echo [INFO] Installing/Upgrading python dependencies from backend/requirements.txt...
pip install --upgrade pip
pip install -r backend/requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install pip requirements.
    pause
    exit /b 1
)

:: 4. Install spaCy English Model
echo [INFO] Checking and downloading spaCy English tokenizer model...
python -m spacy download en_core_web_sm
if %errorlevel% neq 0 (
    echo [ERROR] Failed to download spaCy model.
    pause
    exit /b 1
)

:: 5. Run Backend Server
echo.
echo =======================================================================
echo          SUCCESS: Setup complete. Starting Uvicorn development server...
echo          You can access the client UI at: http://localhost:8000
echo          You can view API swagger docs at: http://localhost:8000/docs
echo =======================================================================
echo.

set PYTHONPATH=backend
python -m uvicorn app.main:app --host 0.0.0.0 --reload --port 8000

pause
