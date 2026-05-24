# =============================================================================
# GameDev Monorepo — Instalador via Clified (Windows PowerShell)
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClifiedRoot = if ($env:CLIFIED_ROOT) { $env:CLIFIED_ROOT } else { Join-Path $env:USERPROFILE "AI\clified" }

if (-not (Test-Path -LiteralPath (Join-Path $ClifiedRoot "install.sh"))) {
    $installPs1 = Join-Path $ClifiedRoot "install.ps1"
    if (-not (Test-Path -LiteralPath $installPs1)) {
        Write-Host "Clified nao encontrado em $ClifiedRoot" -ForegroundColor Red
        Write-Host "Clone https://github.com/maikramer/clified ou defina CLIFIED_ROOT."
        exit 1
    }
}

$env:CLIFIED_ROOT = $ClifiedRoot
$env:CLIFIED_TOOLS = Join-Path $ScriptDir "tools.yaml"
$env:UV_VENV_CLEAR = "1"
$env:UV_LINK_MODE = "copy"

Write-Host "GameDev Monorepo — Instalador (Clified)" -ForegroundColor Cyan
Write-Host "========================================"

$py = Join-Path $ClifiedRoot ".installer-venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) { $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $pythonCmd) {
        Write-Host "Python 3 nao encontrado." -ForegroundColor Red
        exit 1
    }
    $py = $pythonCmd.Source
}

& $py -m clified @args
exit $LASTEXITCODE
