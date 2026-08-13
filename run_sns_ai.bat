@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
python -u run_sns_ai.py --sheet all >> logs\sns_ai_%date:~0,4%%date:~5,2%%date:~8,2%.log 2>&1
exit /b %errorlevel%
