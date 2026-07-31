# AiGameKit Monorepo — Instalador via Clified (PyPI)
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:CLIFIED_TOOLS = if ($env:CLIFIED_TOOLS) { $env:CLIFIED_TOOLS } else { Join-Path $ScriptDir "tools.yaml" }
$env:UV_VENV_CLEAR = if ($env:UV_VENV_CLEAR) { $env:UV_VENV_CLEAR } else { "1" }
$env:UV_LINK_MODE = if ($env:UV_LINK_MODE) { $env:UV_LINK_MODE } else { "copy" }

. (Join-Path $ScriptDir "scripts\install-bootstrap.ps1")
Invoke-ClifiedBootstrapMonorepo @args
