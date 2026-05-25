@echo off
title Rooster Guardian
cd /d "%~dp0"

:: Activate virtual environment if available
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

set "PIDFILE=.rooster\guardian.pid"

if not exist "%PIDFILE%" goto :start

set /p OLD_PID=<"%PIDFILE%" 2>nul
if "%OLD_PID%"=="" goto :start

:: Check if the process is alive
tasklist /FI "PID eq %OLD_PID%" 2>nul | findstr /C:"%OLD_PID%" >nul 2>&1
if errorlevel 1 (
    echo [Guardian] Stale lock found (PID=%OLD_PID% not running^), cleaning up...
    del "%PIDFILE%" 2>nul
    goto :start
)

:: Process is alive -- ask user
echo.
echo [Guardian] Guardian is already running (PID=%OLD_PID%^)
echo.
echo   Y = Kill and restart
echo   N = Exit
echo.
choice /C YN /M "Kill existing Guardian and restart?"
if errorlevel 2 (
    echo [Guardian] Cancelled.
    pause
    exit /b 0
)
echo [Guardian] Killing PID=%OLD_PID% ...
taskkill /PID %OLD_PID% /F >nul 2>&1
del "%PIDFILE%" 2>nul
timeout /t 1 /nobreak >nul

:start
echo.
echo [Guardian] Starting in background mode (Daemon)...
echo.
start "" pythonw guardian.py
echo [Guardian] Process detached. You can safely close this window.
timeout /t 3
exit
