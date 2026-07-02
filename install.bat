@echo off
REM GameDev Monorepo — wrapper fino para install.ps1 (Clified / PyPI)
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %ERRORLEVEL%
