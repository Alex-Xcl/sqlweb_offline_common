@echo off
setlocal
rem sqlweb_offline_common - Windows installer entry point.
rem Running install.ps1 directly may be blocked by the PowerShell execution policy
rem (Windows client default is Restricted). This wrapper passes -ExecutionPolicy Bypass.

set "HERE=%~dp0"
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%HERE%install.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%install.ps1" %*
)
set "RC=%errorlevel%"
endlocal & exit /b %RC%