@echo off
rem Cycle archive -- the wrapper a scheduled task calls. Appends to study\cycle_archive\logs\collect_YYYYMM.log
setlocal
set "REPO=%~dp0..\.."
if not exist "%~dp0logs" mkdir "%~dp0logs"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMM"') do set "YM=%%i"
cd /d "%REPO%"
echo ==== %DATE% %TIME% ==== >> "%~dp0logs\collect_%YM%.log"
"C:\Program Files\Python310\python.exe" -W ignore "%~dp0collect_cycles.py" >> "%~dp0logs\collect_%YM%.log" 2>&1
echo exit %ERRORLEVEL% >> "%~dp0logs\collect_%YM%.log"
endlocal
