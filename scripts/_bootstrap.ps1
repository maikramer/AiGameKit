# =============================================================================
# Vendored do repo Clified (v0.9.0) — manter em sincronia com upstream.
# Primitivas partilhadas de bootstrap do Clified (Windows PowerShell).
# Dot-sourced por scripts/install-bootstrap.ps1 e por install.ps1 de projectos
# consumidores. NÃO chama exit por si — a orquestração fica no wrapper.
# =============================================================================

#Requires -Version 5.1

function Get-ClifiedPython {
    if ($env:PYTHON_CMD) {
        if (Get-Command $env:PYTHON_CMD -ErrorAction SilentlyContinue) {
            return (Get-Command $env:PYTHON_CMD).Source
        }
        if (Test-Path -LiteralPath $env:PYTHON_CMD) {
            return $env:PYTHON_CMD
        }
        Write-Error "PYTHON_CMD aponta para caminho invalido: $($env:PYTHON_CMD)"
        exit 1
    }

    foreach ($cmd in @("python3.12", "python3", "python")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        $py = $found.Source
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        & $py -m pip --version 2>$null | Out-Null
        $hasPip = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prev
        if ($hasPip) { return $py }
    }

    Write-Host "Nenhum Python com pip encontrado. Instale Python 3.10+ ou defina PYTHON_CMD." -ForegroundColor Red
    exit 1
}

function Test-ClifiedInstalled {
    param([string]$PythonExe)

    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $PythonExe -c @"
import importlib.util
import sys
sys.exit(0 if importlib.util.find_spec('clified') else 1)
"@ 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

function Test-PathInPathEnv {
    param(
        [string]$Dir,
        [string]$PathEnv
    )
    if (-not $Dir -or -not (Test-Path -LiteralPath $Dir)) { return $false }
    try {
        $target = [System.IO.Path]::GetFullPath($Dir).TrimEnd('\', '/').ToLowerInvariant()
    }
    catch {
        return $false
    }
    foreach ($part in ($PathEnv -split ';')) {
        $p = $part.Trim()
        if (-not $p) { continue }
        try {
            $norm = [System.IO.Path]::GetFullPath($p).TrimEnd('\', '/').ToLowerInvariant()
            if ($norm -eq $target) { return $true }
        }
        catch { }
    }
    return $false
}

function Get-PythonUserScriptDirs {
    param([string]$PythonExe)

    $dirs = [System.Collections.Generic.List[string]]::new()

    $userBase = (& $PythonExe -m site --user-base 2>$null).Trim()
    if ($userBase) {
        $dirs.Add((Join-Path $userBase "Scripts"))
    }

    # pip --user no Windows (especialmente com Anaconda) usa AppData\Roaming\Python\PythonXY\Scripts
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $ntUserScripts = (
        & $PythonExe -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))" 2>$null
    ).Trim()
    $ErrorActionPreference = $prev
    if ($ntUserScripts) { $dirs.Add($ntUserScripts) }

    return ($dirs | Select-Object -Unique)
}

function Add-PythonUserScriptsToPath {
    param(
        [string]$PythonExe,
        [switch]$Persist
    )

    $addedPersist = $false
    foreach ($dir in (Get-PythonUserScriptDirs -PythonExe $PythonExe)) {
        if (-not (Test-Path -LiteralPath $dir)) { continue }

        if (-not (Test-PathInPathEnv -Dir $dir -PathEnv $env:Path)) {
            $env:Path = "$dir;$env:Path"
        }

        if ($Persist) {
            $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
            if (-not (Test-PathInPathEnv -Dir $dir -PathEnv $userPath)) {
                $newUserPath = if ($userPath) { "$dir;$userPath" } else { $dir }
                [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
                $addedPersist = $true
            }
        }
    }
    return $addedPersist
}

function Install-ClifiedPackageSpec {
    param(
        [string]$PythonExe,
        [string]$PackageSpec,
        [switch]$PersistPath
    )

    Write-Host "A instalar $PackageSpec via pip ($PythonExe)..." -ForegroundColor Cyan
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Stop"
    try {
        & $PythonExe -m pip install --user --upgrade $PackageSpec
        if ($LASTEXITCODE -ne 0) {
            Write-Host "A repetir pip com --break-system-packages (PEP 668)..." -ForegroundColor Yellow
            & $PythonExe -m pip install --user --break-system-packages --upgrade $PackageSpec
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Falha ao instalar clified." -ForegroundColor Red
            exit $LASTEXITCODE
        }
    }
    finally {
        $ErrorActionPreference = $prev
    }

    Add-PythonUserScriptsToPath -PythonExe $PythonExe -Persist:$PersistPath | Out-Null
}

function Install-ClifiedPackage {
    param(
        [string]$PythonExe,
        [string]$MinVersion,
        [switch]$PersistPath
    )

    Install-ClifiedPackageSpec -PythonExe $PythonExe -PackageSpec "clified>=$MinVersion" -PersistPath:$PersistPath
}

function Invoke-ClifiedExec {
    param(
        [string]$PythonExe,
        [string]$ToolName,
        [string[]]$ClifiedArgs
    )

    Add-PythonUserScriptsToPath -PythonExe $PythonExe | Out-Null

    $clifiedInstall = Get-Command clified-install -ErrorAction SilentlyContinue
    if ($clifiedInstall) {
        & clified-install $ToolName @ClifiedArgs
        exit $LASTEXITCODE
    }
    & $PythonExe -m clified $ToolName @ClifiedArgs
    exit $LASTEXITCODE
}

function Invoke-ClifiedExecArgs {
    param(
        [string]$PythonExe,
        [string[]]$ClifiedArgs
    )

    Add-PythonUserScriptsToPath -PythonExe $PythonExe | Out-Null

    $clifiedInstall = Get-Command clified-install -ErrorAction SilentlyContinue
    if ($clifiedInstall) {
        & $clifiedInstall.Source @ClifiedArgs
        exit $LASTEXITCODE
    }
    & $PythonExe -m clified @ClifiedArgs
    exit $LASTEXITCODE
}
