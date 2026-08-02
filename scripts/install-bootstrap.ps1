# Bootstrap Clified (PyPI) — wrapper fino sobre scripts/_bootstrap.ps1.
# Dot-sourced por install.ps1 dos projectos consumidores.
#
# Vendored do repo Clified (v0.9.0) com adições AiGameKit:
#   - Install-UvIfMissing: instala `uv` (pip --user) quando ausente (não-fatal).
#   - Invoke-ClifiedBootstrapMonorepo: passthrough de args (install.ps1 do monorepo).
#   - CLIFIED_MIN_VERSION default 0.8.1 (features de tools.yaml do monorepo).
#
# Uso (no install.ps1 do projecto):
#   . "$PSScriptRoot\scripts\install-bootstrap.ps1"
#   Invoke-ClifiedBootstrapMonorepo @args

#Requires -Version 5.1

. "$PSScriptRoot\_bootstrap.ps1"

function Install-UvIfMissing {
    param([string]$PythonExe)

    if (Get-Command uv -ErrorAction SilentlyContinue) { return }
    if ($env:CLIFIED_SKIP_UV -eq "1") {
        Write-Host "uv ausente - CLIFIED_SKIP_UV=1, a usar venv classico..." -ForegroundColor Yellow
        return
    }
    Write-Host "uv nao encontrado - a instalar via pip ($PythonExe)..." -ForegroundColor Cyan
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $PythonExe -m pip install --user --upgrade uv 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & $PythonExe -m pip install --user --break-system-packages --upgrade uv 2>$null | Out-Null
    }
    $ErrorActionPreference = $prev
    # Nao-fatal: falha apenas deixa o install mais lento (venv classico).
}

function Invoke-ClifiedBootstrap {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ToolName,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ClifiedArgs
    )

    $py = Get-ClifiedPython
    Add-PythonUserScriptsToPath -PythonExe $py | Out-Null
    $minVer = if ($env:CLIFIED_MIN_VERSION) { $env:CLIFIED_MIN_VERSION } else { "0.8.1" }

    if (Get-Command clified-install -ErrorAction SilentlyContinue) {
        Invoke-ClifiedExec -PythonExe $py -ToolName $ToolName -ClifiedArgs $ClifiedArgs
    }
    if (Test-ClifiedInstalled -PythonExe $py) {
        Add-PythonUserScriptsToPath -PythonExe $py | Out-Null
        if (Get-Command clified-install -ErrorAction SilentlyContinue) {
            Invoke-ClifiedExec -PythonExe $py -ToolName $ToolName -ClifiedArgs $ClifiedArgs
        }
        Invoke-ClifiedExec -PythonExe $py -ToolName $ToolName -ClifiedArgs $ClifiedArgs
    }

    Install-ClifiedPackage -PythonExe $py -MinVersion $minVer -PersistPath
    Invoke-ClifiedExec -PythonExe $py -ToolName $ToolName -ClifiedArgs $ClifiedArgs
}

# === AiGameKit: variante monorepo — passthrough de args (sem -ToolName) ===
function Invoke-ClifiedBootstrapMonorepo {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ClifiedArgs
    )

    $py = Get-ClifiedPython
    Add-PythonUserScriptsToPath -PythonExe $py | Out-Null
    Install-UvIfMissing -PythonExe $py
    $minVer = if ($env:CLIFIED_MIN_VERSION) { $env:CLIFIED_MIN_VERSION } else { "0.8.1" }

    if (Get-Command clified-install -ErrorAction SilentlyContinue) {
        Invoke-ClifiedExecArgs -PythonExe $py -ClifiedArgs $ClifiedArgs
    }
    if (Test-ClifiedInstalled -PythonExe $py) {
        Add-PythonUserScriptsToPath -PythonExe $py | Out-Null
        if (Get-Command clified-install -ErrorAction SilentlyContinue) {
            Invoke-ClifiedExecArgs -PythonExe $py -ClifiedArgs $ClifiedArgs
        }
        Invoke-ClifiedExecArgs -PythonExe $py -ClifiedArgs $ClifiedArgs
    }

    Install-ClifiedPackage -PythonExe $py -MinVersion $minVer -PersistPath
    Invoke-ClifiedExecArgs -PythonExe $py -ClifiedArgs $ClifiedArgs
}
