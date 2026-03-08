param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "Installing Palimpsest as a user-local editable CLI..."
& $Python -m pip install --user --editable $repoRoot
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$scriptDir = & $Python -c "from pathlib import Path; import site; print(Path(site.getusersitepackages()).parent / 'Scripts')"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$scriptDir = $scriptDir.Trim()
$shimDir = Join-Path $env:APPDATA "npm"
$shimCreated = $false

if (Test-Path $shimDir) {
    $cmdShim = Join-Path $shimDir "palimpsest.cmd"
    $psShim = Join-Path $shimDir "palimpsest.ps1"

    @"
@echo off
"$scriptDir\palimpsest.exe" %*
"@ | Set-Content -Path $cmdShim -Encoding ASCII

    @"
& "$scriptDir\palimpsest.exe" @args
"@ | Set-Content -Path $psShim -Encoding ASCII

    $shimCreated = $true
}

Write-Host ""
Write-Host "Installed command: palimpsest"
Write-Host "Scripts directory: $scriptDir"
if ($shimCreated) {
    Write-Host "Shim directory: $shimDir"
}

$pathEntries = $env:PATH -split ';' | Where-Object { $_ }
if (-not $shimCreated -and $pathEntries -notcontains $scriptDir) {
    Write-Warning "That Scripts directory is not on PATH for this shell. Add it to use 'palimpsest' directly."
}
