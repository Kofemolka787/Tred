@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python
py -3 -B "configure_ai.py" --provider lunaroute
goto done
:python
python -B "configure_ai.py" --provider lunaroute
:done
pause
