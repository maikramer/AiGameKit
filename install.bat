@echo off
REM GameDev Monorepo — Instalador via Clified (Windows CMD)

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if not defined CLIFIED_ROOT set "CLIFIED_ROOT=%USERPROFILE%\AI\clified"
set "CLIFIED_TOOLS=%SCRIPT_DIR%tools.yaml"
set "UV_VENV_CLEAR=1"
set "UV_LINK_MODE=copy"

echo GameDev Monorepo — Instalador (Clified)
echo ========================================

set "CLIFIED_PY=%CLIFIED_ROOT%\.installer-venv\Scripts\python.exe"
if exist "%CLIFIED_PY%" (
    "%CLIFIED_PY%" -m clified %*
    exit /b !ERRORLEVEL!
)

python --version >nul 2>&1
if errorlevel 1 (
    echo Python 3 nao encontrado. Instale Clified em %CLIFIED_ROOT%
    exit /b 1
)
python -m clified %*
exit /b %ERRORLEVEL%
