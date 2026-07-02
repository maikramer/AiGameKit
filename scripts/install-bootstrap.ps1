# Bootstrap Clified (PyPI) — monorepo GameDev (passa todos os args a clified-install).
# Dot-sourced por install.ps1 na raiz do GameDev.

#Requires -Version 5.1

. "$PSScriptRoot\_bootstrap.ps1"

function Invoke-ClifiedBootstrapMonorepo {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ClifiedArgs
    )

    $py = Get-ClifiedPython
    Add-PythonUserScriptsToPath -PythonExe $py | Out-Null
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
