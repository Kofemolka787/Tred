@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto try_python
set "TRED_PY=py -3"
goto python_ready

:try_python
where python >nul 2>nul
if errorlevel 1 goto python_required
set "TRED_PY=python"

:python_ready
%TRED_PY% -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12 or later is required'"
if errorlevel 1 goto python_required
if not exist ".venv\Scripts\python.exe" %TRED_PY% -m venv ".venv"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r "requirements-web.txt"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" "free_start.py"
if errorlevel 1 goto failed
pause
exit /b 0

:python_required
echo.
echo Install Python 3.12 or later from:
echo https://www.python.org/downloads/windows/
echo Then open START-WINDOWS.cmd again.
pause
exit /b 1

:failed
echo.
echo TRED setup stopped. Keep this window open and share the error message.
pause
exit /b 1
