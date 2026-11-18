$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$bundledPython = Join-Path $PSScriptRoot 'python\python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython -m app.server
} else {
    python -m app.server
}
