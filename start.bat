@echo off
chcp 65001 >nul 2>&1
title VideoSubtitleExtractor

echo ============================================
echo   Video Subtitle Extractor
echo ============================================
echo.

REM Kill any process holding ports 8002 (frontend) and 8654 (backend)
for %%P in (8002 8654) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
        echo Killing PID %%A on port %%P
        taskkill /F /PID %%A >nul 2>&1
    )
)

REM Wait for ports to release
timeout /t 1 /nobreak >nul

REM Clear Python cache
if exist "__pycache__" rd /s /q "__pycache__" >nul 2>&1
if exist "src\__pycache__" rd /s /q "src\__pycache__" >nul 2>&1

REM Ensure .env exists
if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul

REM Verify Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

REM Verify Streamlit (auto-install if missing)
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
)

echo.
echo Starting on http://localhost:8002
echo Press Ctrl+C to stop.
echo.
python run.py
