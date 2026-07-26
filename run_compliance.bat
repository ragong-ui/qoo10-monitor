@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

REM compliance_main.py owns the date-stamped log file.
REM Redirecting here too locks the same file on Windows.
python -u compliance_main.py
set "EXIT_CODE=%ERRORLEVEL%"
exit /b %EXIT_CODE%
