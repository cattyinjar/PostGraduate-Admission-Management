# Contributor Build Script
$ErrorActionPreference = "Stop"
python -m pip install -e ".[dev]"
python -m pytest
python -m PyInstaller --noconfirm --clean installer/pgam.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Build output: dist\PGAM"
