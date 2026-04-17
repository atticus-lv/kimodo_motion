@echo off
rem Kimodo Motion - launcher for install.ps1 (ASCII only).
rem Works even when user path / username contains CJK characters.
rem Forward all args to PowerShell and preserve the exit code.

chcp 65001 > nul
setlocal
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install.ps1" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
