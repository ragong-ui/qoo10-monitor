@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

REM Redirect stdout/stderr to log file (date-stamped)
set LOGDATE=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%
set LOGFILE=logs\compliance_%LOGDATE%.log

python -u compliance_main.py >> "%LOGFILE%" 2>&1
echo Exit code: %ERRORLEVEL% >> "%LOGFILE%" 2>&1
