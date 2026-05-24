# GameDev Monorepo — Instalador via Clified (PyPI)
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:CLIFIED_TOOLS = if ($env:CLIFIED_TOOLS) { $env:CLIFIED_TOOLS } else { Join-Path $ScriptDir "tools.yaml" }
$env:UV_VENV_CLEAR = if ($env:UV_VENV_CLEAR) { $env:UV_VENV_CLEAR } else { "1" }
$env:UV_LINK_MODE = if ($env:UV_LINK_MODE) { $env:UV_LINK_MODE } else { "copy" }

$py = $null
foreach ($cmd in @("python", "python3")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { $py = $found.Source; break }
}
if (-not $py) {
    Write-Host "Python 3 nao encontrado." -ForegroundColor Red
    exit 1
}

$MinVersion = if ($env:CLIFIED_MIN_VERSION) { $env:CLIFIED_MIN_VERSION } else { "0.4.0" }

if (Get-Command clified-install -ErrorAction SilentlyContinue) {
    & clified-install @args
    exit $LASTEXITCODE
}

& $py -c "import clified" 2>$null
if ($LASTEXITCODE -eq 0) {
    & $py -m clified @args
    exit $LASTEXITCODE
}

Write-Host "A instalar clified>=$MinVersion via pip..."
& $py -m pip install --user --upgrade "clified>=$MinVersion"
& clified-install @args
exit $LASTEXITCODE
