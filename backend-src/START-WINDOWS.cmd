@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Установи Python 3.12 или новее с https://www.python.org/downloads/windows/
  echo Затем снова открой этот файл. Пароли и платёжные данные не нужны.
  pause
  exit /b 1
)
py -3 -c "import sys; assert sys.version_info >= (3,12), 'Нужен Python 3.12+'"
if errorlevel 1 goto failed
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r requirements-web.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" free_start.py
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Запуск остановлен. Скопируй сообщение об ошибке без личных данных.
pause
exit /b 1
