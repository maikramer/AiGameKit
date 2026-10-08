# AiGameKit Monorepo — Instalador via Clified (PyPI)
#
#   .\install.ps1              → perfil 'core' (10 tools, zero-a-jogo)
#   .\install.ps1 examples     → core + céu/áudio/texturas/terreno/rochas + Viber
#   .\install.ps1 -All | all   → catálogo completo (acrescenta part3d, motion3d, intrinsic)
#   .\install.ps1 <tool>       → ferramenta individual (chaves de tools.yaml)
#   .\install.ps1 -List        → listar perfis e ferramentas
#
# Pre-flight (scripts/preflight.py) valida pré-requisitos externos e pára uma
# única vez com todos os comandos para instalar o que falta.
# Bypass para agentes/CI: $env:AIGAMEKIT_PREFLIGHT = "0"
param(
    [switch]$List,
    [switch]$All,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:CLIFIED_TOOLS = if ($env:CLIFIED_TOOLS) { $env:CLIFIED_TOOLS } else { Join-Path $ScriptDir "tools.yaml" }
$env:UV_VENV_CLEAR = if ($env:UV_VENV_CLEAR) { $env:UV_VENV_CLEAR } else { "1" }
$env:UV_LINK_MODE = if ($env:UV_LINK_MODE) { $env:UV_LINK_MODE } else { "copy" }

. (Join-Path $ScriptDir "scripts\install-bootstrap.ps1")

# Perfis de instalação (docs/findings/TOOLKIT_CORE_PROFILE_STUDY.md)
$CoreTools = @("vramd", "text2d", "text3d", "paint3d", "rigging3d", "animator3d", "gameassets", "materialize", "aigamekitlab", "vibegame")
$ExamplesExtraTools = @("texture2d", "skymap2d", "text2sound", "terrain3d", "rocks3d", "viber")

# Resolve um Python sem fazer exit (o bootstrap faz a validação completa depois).
function Get-PythonOrNull {
    foreach ($cmd in @("python3", "python")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) { return $found.Source }
    }
    return $null
}

function Invoke-Preflight {
    param([string[]]$PreflightArgs)
    $py = Get-PythonOrNull
    if (-not $py) {
        Write-Host "Falta Python (com pip) — pré-requisito do instalador." -ForegroundColor Red
        Write-Host ""
        Write-Host "  winget install Python.Python.3.13" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Depois volta a correr: .\install.ps1"
        exit 1
    }
    & $py (Join-Path $ScriptDir "scripts\preflight.py") @PreflightArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# Cada tool corre num subprocesso PowerShell porque Invoke-ClifiedExec termina o
# processo (exit) — assim o perfil continua em falha e mostra o resumo no fim.
$Pwsh = if (Get-Command pwsh -ErrorAction SilentlyContinue) { (Get-Command pwsh).Source } else { "powershell" }

function Install-ToolSubprocess {
    param([string]$Tool, [string[]]$ToolArgs)
    $bootstrap = Join-Path $ScriptDir "scripts\install-bootstrap.ps1"
    $cmd = ". '{0}'; Invoke-ClifiedBootstrap -ToolName '{1}'" -f $bootstrap, $Tool
    if ($ToolArgs) {
        $cmd += " " + (($ToolArgs | ForEach-Object { "'$_'" }) -join " ")
    }
    & $Pwsh -NoProfile -ExecutionPolicy Bypass -Command $cmd
    return ($LASTEXITCODE -eq 0)
}

function Show-Summary {
    param([string]$Profile, [string[]]$Tools, [string[]]$FailedTools)
    Write-Host ""
    Write-Host "Resumo do perfil '$Profile':"
    foreach ($t in $Tools) {
        if ($FailedTools -contains $t) {
            Write-Host "  [X] $t — FALHOU" -ForegroundColor Red
        } else {
            Write-Host "  [OK] $t" -ForegroundColor Green
        }
    }
}

function Show-NextSteps {
    Write-Host ""
    Write-Host "Próximos passos:"
    Write-Host "  1. Abre um novo terminal (o PATH foi atualizado pelo instalador)"
    Write-Host "  2. gameassets doctor — confirma GPU, compressão GLB e LLM do dream"
    Write-Host "  3. gameassets dream `"A dark fantasy RPG with skeletons and treasure chests`" --dry-run"
}

function Invoke-MaybeDoctor {
    $ga = Join-Path $ScriptDir "GameAssets\.venv\Scripts\gameassets.exe"
    if (-not (Test-Path $ga)) {
        $cmd = Get-Command gameassets -ErrorAction SilentlyContinue
        if ($cmd) { $ga = $cmd.Source } else { return }
    }
    Write-Host ""
    & $ga doctor
    if ($LASTEXITCODE -ne 0) {
        Write-Host "(gameassets doctor falhou — corre-o manualmente mais tarde)" -ForegroundColor Yellow
    }
}

# --- Dispatch -------------------------------------------------------------------

if ($List) {
    Invoke-Preflight @("--list-profiles")
    exit 0
}

$profile = $null
$extraArgs = @()
if ($All) {
    $profile = "all"
    $extraArgs = @($Rest)
} elseif ($Rest -and $Rest.Count -gt 0) {
    $first = $Rest[0]
    if ($first -in @("all")) {
        $profile = "all"
        $extraArgs = @($Rest | Select-Object -Skip 1)
    } elseif ($first -in @("core", "examples")) {
        $profile = $first
        $extraArgs = @($Rest | Select-Object -Skip 1)
    } elseif ($first.StartsWith("-")) {
        # Flags do clified (--catalog, --doctor, --json, ...) passam diretamente.
        Invoke-ClifiedBootstrapMonorepo -ClifiedArgs $Rest
        exit $LASTEXITCODE
    } else {
        # Ferramenta individual: pre-flight só para o que ela precisa.
        Invoke-Preflight @("--tools", $first)
        Invoke-ClifiedBootstrapMonorepo -ClifiedArgs $Rest
        exit $LASTEXITCODE
    }
} else {
    $profile = "core"
}

if ($profile -eq "all") {
    Invoke-Preflight @("--profile", "all")
    $bootstrap = Join-Path $ScriptDir "scripts\install-bootstrap.ps1"
    $cmd = ". '{0}'; Invoke-ClifiedBootstrapMonorepo '--all'" -f $bootstrap
    & $Pwsh -NoProfile -ExecutionPolicy Bypass -Command $cmd
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Invoke-MaybeDoctor
    Show-NextSteps
    exit 0
}

$tools = @($CoreTools)
if ($profile -eq "examples") { $tools += $ExamplesExtraTools }

if (-not $All -and ($null -eq $Rest -or $Rest.Count -eq 0)) {
    Write-Host "Sem argumentos — a instalar o perfil 'core' ($($tools.Count) tools, zero-a-jogo)."
    Write-Host "Outras opções: .\install.ps1 examples | -All | <tool> | -List"
} else {
    Write-Host "Perfil '$profile': $($tools -join ' ')"
}

Invoke-Preflight @("--profile", $profile)

$failed = @()
foreach ($t in $tools) {
    $ok = Install-ToolSubprocess -Tool $t -ToolArgs $extraArgs
    if (-not $ok) { $failed += $t }
}

Show-Summary -Profile $profile -Tools $tools -FailedTools $failed

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "Falharam: $($failed -join ' ')"
    Write-Host "Repete individualmente: .\install.ps1 <tool>"
    exit 1
}

Invoke-MaybeDoctor
Show-NextSteps
