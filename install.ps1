# =============================================================================
# GameDev Monorepo — Instalador Unificado (Windows PowerShell)
# =============================================================================
#
# Instala qualquer ferramenta do monorepo GameDev.
#
# Uso:
#   .\install.ps1 materialize           # Instalar Materialize (Rust)
#   .\install.ps1 text2d              # com .venv no projecto, instala no venv do projecto
#   .\install.ps1 all                     # Instalar tudo
#   .\install.ps1 --all                    # Igual a «all»
#   .\install.ps1 --list                # Listar ferramentas
#
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SharedRoot = Join-Path $ScriptDir "Shared"
$SharedSrc = Join-Path $SharedRoot "src"

$Cyan = "`e[36m"
$Red = "`e[31m"
$Reset = "`e[0m"

function Prepare-InstallerEnvironment {
    Write-Host "${Cyan}Preparando ambiente do instalador...${Reset}"

    $pkgPath = Join-Path $SharedSrc "gamedev_shared"
    if (-not (Test-Path -LiteralPath $pkgPath)) {
        Write-Host "${Red}Monorepo incompleto: nao existe $pkgPath${Reset}"
        Write-Host "  Clona o repositorio completo (pasta Shared/ e obrigatoria)."
        exit 1
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
    }
    if (-not $pythonCmd) {
        Write-Host "${Red}Python 3 nao encontrado. Instale de https://python.org${Reset}"
        exit 1
    }

    $py = $pythonCmd.Source
    & $py -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "${Red}Python 3.10 ou superior e necessario.${Reset}"
        & $py -V 2>$null
        exit 1
    }

    $env:PYTHONPATH = "$SharedSrc;$($env:PYTHONPATH)"

    $projectsPython = $py
    & $projectsPython -c "import rich" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "${Cyan}  -> Ambiente isolado do instalador (venv + Rich)...${Reset}"
        $installerVenv = Join-Path $SharedRoot ".installer-venv"
        $launcherPython = Join-Path $installerVenv "Scripts\\python.exe"
        if (-not (Test-Path -LiteralPath $launcherPython)) {
            & $projectsPython -m venv $installerVenv
            if ($LASTEXITCODE -ne 0) {
                Write-Host "${Red}Falha ao criar venv do instalador em $installerVenv${Reset}"
                exit 1
            }
        }
        & $launcherPython -c "import rich" 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & $launcherPython -m pip install -q --upgrade pip "rich>=13"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "${Red}Falha ao instalar Rich no venv do instalador.${Reset}"
                exit 1
            }
        }
        return @{ Launcher = $launcherPython; Projects = $projectsPython }
    }

    return @{ Launcher = $projectsPython; Projects = $projectsPython }
}

$p = Prepare-InstallerEnvironment

# Evita prompts do ``uv venv`` quando .venv ja existe (alinha com install.sh).
$env:UV_VENV_CLEAR = "1"
$env:UV_LINK_MODE = "copy"

Write-Host "${Cyan}GameDev Monorepo - Instalador Unificado${Reset}"
Write-Host "========================================"

# Launcher com Rich (--python mantém-se o interpretador de referência dos projectos / venvs).
& $p.Launcher -m gamedev_shared.installer.unified --python $p.Projects @args
exit $LASTEXITCODE
