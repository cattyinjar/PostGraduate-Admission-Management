param(
    [string]$Python = "D:\anaconda\envs\pgam\python.exe",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
    & $Python -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not $SkipTests) {
        $env:QT_QPA_PLATFORM = "offscreen"
        & $Python -m pytest pgam\tests -q
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -m ruff check pgam
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    & $Python -m PyInstaller --noconfirm --clean installer\pgam.spec
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $candidates = @(
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        )
        $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) { $iscc = @{ Source = $found } }
    }
    if (-not $iscc) { throw "ISCC.exe not found. Install Inno Setup 6 or add it to PATH." }

    New-Item -ItemType Directory -Force -Path dist\installer | Out-Null
    & $iscc.Source installer\pgam.iss
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $installer = Join-Path $root "dist\installer\PGAM-0.1.0-x64.exe"
    if (-not (Test-Path $installer)) { throw "Installer was not generated: $installer" }
    $sizeMb = [math]::Round((Get-Item $installer).Length / 1MB, 2)
    Write-Host "Installer: $installer"
    Write-Host ("Installer size: {0} MB" -f $sizeMb)
} finally {
    Pop-Location
}
