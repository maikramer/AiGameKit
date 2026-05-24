@echo off
REM GameDev Monorepo — Instalador via Clified (PyPI)

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if not defined CLIFIED_TOOLS set "CLIFIED_TOOLS=%SCRIPT_DIR%tools.yaml"
if not defined UV_VENV_CLEAR set "UV_VENV_CLEAR=1"
if not defined UV_LINK_MODE set "UV_LINK_MODE=copy"

set "MIN_VERSION=0.4.0"
if defined CLIFIED_MIN_VERSION set "MIN_VERSION=%CLIFIED_MIN_VERSION%"

where clified-install >nul 2>&1
if not errorlevel 1 (
    clified-install %*
    exit /b !ERRORLEVEL!
)

python -c "import clified" >nul 2>&1
if not errorlevel 1 (
    python -m clified %*
    exit /b !ERRORLEVEL!
)

echo A instalar clified^>=%MIN_VERSION% via pip...
python -m pip install --user --upgrade "clified>=%MIN_VERSION%"
if errorlevel 1 exit /b 1

clified-install %*
exit /b !ERRORLEVEL!
