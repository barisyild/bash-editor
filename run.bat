@echo off
rem Launch Bash Editor on Windows, setting up the environment on first run.
rem
rem Creates .venv if it is missing, installs the requirements when they change,
rem then starts the editor. Double-click it, or pass a game EXE to open:
rem   run.bat C:\games\CrashBash\SCUS_945.70
rem
rem run.sh is the macOS and Linux equivalent.

setlocal
cd /d "%~dp0"

set "PYTHON="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PYTHON=py -3"
if not defined PYTHON (
    python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PYTHON=python"
)

if not defined PYTHON (
    echo Bash Editor needs Python 3.10 or newer, and none was found on PATH.
    echo Install it from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" in the installer.
    echo.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" goto :environment_ready
echo Creating the virtual environment in .venv ...
%PYTHON% -m venv .venv
if errorlevel 1 goto :failed

:environment_ready
rem The stamp is a copy of the requirements the venv was last built against, so
rem a changed requirements.txt reinstalls and an unchanged one costs nothing.
fc /b requirements.txt ".venv\requirements.stamp" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :failed
    copy /y requirements.txt ".venv\requirements.stamp" >nul
)

rem python.exe rather than pythonw.exe, so a startup failure -- a missing OpenGL
rem driver, say -- prints its traceback instead of the window never appearing.
".venv\Scripts\python.exe" app\main.py %*
if errorlevel 1 (
    echo.
    echo Bash Editor exited with an error. The messages above say why.
    pause
)
exit /b %errorlevel%

:failed
echo.
echo Setup failed. The messages above say why.
pause
exit /b 1
