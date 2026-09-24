param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [string]$Python = "D:\anaconda\envs\pgam\python.exe"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path $root "build\installer-verification"
if (Test-Path $tempRoot) { Remove-Item -LiteralPath $tempRoot -Force -Recurse }
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$installDir = Join-Path $tempRoot "App"
$dataRoot = Join-Path $tempRoot "AppData"
$dataDir = Join-Path $dataRoot "PostGraduateAdmissionMonitor"
$logDir = Join-Path $tempRoot "logs"
New-Item -ItemType Directory -Force -Path $dataRoot, $logDir | Out-Null
$installer = Resolve-Path $InstallerPath
$exe = $null
$process = $null

function Stop-InstalledApp {
    param($Process)
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
        Start-Sleep -Seconds 2
    }
}

try {
    if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
    Write-Host "Installing: $installer"
    $setupLog = Join-Path $logDir "setup.log"
    $setup = Start-Process -FilePath $installer -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
        "/DIR=`"$installDir`"", "/LOG=`"$setupLog`""
    ) -Wait -PassThru -WindowStyle Hidden
    if ($setup.ExitCode -ne 0) { throw "Installer failed with exit code $($setup.ExitCode)" }

    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C6CFF91-55AE-4F16-BD5D-93D99F2742B3}_is1"
    if (-not (Test-Path $uninstallKey)) { throw "Installed uninstall registry key was not found" }
    $registry = Get-ItemProperty $uninstallKey
    if (-not $registry.DisplayName.StartsWith("PostGraduate Admission Monitor", [StringComparison]::OrdinalIgnoreCase)) { throw "Installed DisplayName is incorrect: $($registry.DisplayName)" }
    if ($registry.DisplayVersion -ne "0.1.0") { throw "Installed DisplayVersion is incorrect" }
    if (-not $registry.InstallLocation.StartsWith($installDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Installed InstallLocation is incorrect: $($registry.InstallLocation)"
    }

    $exe = Join-Path $installDir "PostGraduateAdmissionMonitor.exe"
    if (-not (Test-Path $exe)) { throw "Installed executable is missing" }

    $env:QT_QPA_PLATFORM = "offscreen"
    $env:APPDATA = $dataRoot
    $process = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path (Join-Path $dataDir "app.db")) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path (Join-Path $dataDir "app.db"))) { throw "Installed app did not create persistent app.db" }
    Stop-InstalledApp $process
    $process = $null

    $seedScript = @"
from pgam.core.models import MonitorTask, SourceType
from pgam.storage.database import Database, utc_now
from pgam.storage.repositories import TaskRepository
db = Database(r'$dataDir\app.db')
repo = TaskRepository(db)
now = utc_now()
repo.create(MonitorTask(
    id=0,
    name='Installer persistence test',
    url='https://example.edu.cn/list.htm',
    source_type=SourceType.STATIC_LIST,
    adapter_config_json='{}',
    check_interval_minutes=30,
    enabled=False,
    last_checked_at=None,
    last_success_at=None,
    next_check_at=None,
    failure_count=0,
    created_at=now,
    updated_at=now,
))
db.close_all_connections()
print('seeded=1')
"@
   $seedScript | & $Python -
    if ($LASTEXITCODE -ne 0) { throw "Failed to seed persistent task" }

    $process = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 5
    Stop-InstalledApp $process
    $process = $null

    $queryScript = @"
import sqlite3
conn = sqlite3.connect(r'$dataDir\app.db')
row = conn.execute("select count(*) from monitor_task where name='Installer persistence test'").fetchone()
print('task_count=' + str(row[0]))
conn.close()
"@
    $output = $queryScript | & $Python
    if ($LASTEXITCODE -ne 0) { throw "Failed to query persistent task" }
    if ($output -notcontains "task_count=1") { throw "Persistent task was not retained. Output: $($output -join ', ')" }

    $uninstaller = Join-Path $installDir "unins000.exe"
    if (-not (Test-Path $uninstaller)) { throw "Uninstaller is missing" }
    $uninstallLog = Join-Path $logDir "uninstall.log"
    $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$uninstallLog`""
    ) -Wait -PassThru -WindowStyle Hidden
    if ($uninstall.ExitCode -ne 0) { throw "Uninstaller failed with exit code $($uninstall.ExitCode)" }
    Start-Sleep -Seconds 2

    if (Test-Path $exe) { throw "Uninstall did not remove installed executable" }
    if (-not (Test-Path (Join-Path $dataDir "app.db"))) { throw "Uninstall removed user data unexpectedly" }

    [pscustomobject]@{
        installer = $installer.Path
        install_dir = $installDir
        executable_launched = $true
        persistent_database = (Join-Path $dataDir "app.db")
        persistent_task_retained = $true
        uninstall_registry_removed = -not (Test-Path $uninstallKey)
        user_data_preserved_after_uninstall = $true
        all_passed = $true
    } | ConvertTo-Json
} catch {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    throw
}
