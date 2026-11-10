@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m wallet_recover.gui
) else (
    python -m wallet_recover.gui
)
if errorlevel 1 (
    echo.
    echo GUI startup failed. Check Python, Tkinter and dependencies.
    echo See the Windows section in README.md.
    pause
)
endlocal
