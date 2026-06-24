@echo off
cd /d "%~dp0"
python monitor.py >> logs\monitor_%date:~0,4%%date:~5,2%%date:~8,2%.log 2>&1
python x_monitor.py >> logs\x_monitor_%date:~0,4%%date:~5,2%%date:~8,2%.log 2>&1
