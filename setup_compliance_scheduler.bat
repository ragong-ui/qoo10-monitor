@echo off
REM Task Scheduler registration for Japan Compliance Briefing
REM Runs daily at 08:00 KST/JST (both are UTC+9, no offset needed)
REM Execute as SYSTEM or as your own user with "Run whether logged on or not"

set TASKNAME=Japan_Compliance_Briefing
set SCRIPTDIR=%~dp0
set BATFILE=%SCRIPTDIR%run_compliance.bat

echo Registering Task Scheduler task: %TASKNAME%

schtasks /Create /TN "%TASKNAME%" ^
  /TR "\"%BATFILE%\"" ^
  /SC DAILY ^
  /ST 08:00 ^
  /F ^
  /RL HIGHEST ^
  /RU "%USERNAME%" ^
  /IT

if %ERRORLEVEL% EQU 0 (
    echo Task registered successfully.
    echo Run: schtasks /Run /TN "%TASKNAME%"  to test immediately.
) else (
    echo Failed to register task. Try running as Administrator.
)
pause
