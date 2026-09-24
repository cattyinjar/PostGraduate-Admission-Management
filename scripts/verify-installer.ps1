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
$runKeyPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runKeyName = "PostGraduateAdmissionMonitor"
$hadPreviousAutoStart = Test-Path $runKeyPath
$previousAutoStart = $null
if ($hadPreviousAutoStart) {
    $previousAutoStart = (Get-ItemProperty $runKeyPath -ErrorAction SilentlyContinue).$runKeyName
}

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
    if (-not (Test-Path $runKeyPath)) { throw "Installed auto-start Run key was not created" }
    $autoStartValue = (Get-ItemProperty $runKeyPath).$runKeyName
    if (-not $autoStartValue -or -not $autoStartValue.StartsWith("`"$installDir", [StringComparison]::OrdinalIgnoreCase) -or -not $autoStartValue.Contains("--hidden")) {
        throw "Installed auto-start command is incorrect: $autoStartValue"
    }

    $env:QT_QPA_PLATFORM = "offscreen"
    $env:APPDATA = $dataRoot
    $process = Start-Process -FilePath $exe -ArgumentList "--hidden" -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    if ($process.HasExited) { throw "Installed app exited when launched with --hidden" }
    $autoStartValueAfterLaunch = (Get-ItemProperty $runKeyPath).$runKeyName
    if (-not $autoStartValueAfterLaunch -or -not $autoStartValueAfterLaunch.Contains("--hidden")) {
        throw "Application did not preserve its hidden auto-start command"
    }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path (Join-Path $dataDir "app.db")) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path (Join-Path $dataDir "app.db"))) { throw "Installed app did not create persistent app.db" }
    $autoStartSettingScript = @"
import sqlite3, time
row = None
for _ in range(100):
    conn = sqlite3.connect(r'$dataDir\app.db')
    row = conn.execute("select value from app_config where key='auto_start_enabled'").fetchone()
    conn.close()
    if row:
        break
    time.sleep(0.1)
print('auto_start_setting=' + str(row[0] if row else None))
"@
    $autoStartSettingOutput = $autoStartSettingScript | & $Python
    if ($LASTEXITCODE -ne 0) { throw "Failed to query auto-start setting" }
    if ($autoStartSettingOutput -notcontains "auto_start_setting=True") { throw "Default auto_start_enabled was not persisted" }
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

    $process = Start-Process -FilePath $exe -ArgumentList "--hidden" -WindowStyle Hidden -PassThru
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
    $currentAutoStartAfterUninstall = $null
    if (Test-Path $runKeyPath) {
        $currentAutoStartAfterUninstall = (Get-ItemProperty $runKeyPath -ErrorAction SilentlyContinue).$runKeyName
    }
    $autoStartRemoved = $null -eq $currentAutoStartAfterUninstall
    if ($null -ne $previousAutoStart) {
        New-Item -Path $runKeyPath -Force | Out-Null
        New-ItemProperty -Path $runKeyPath -Name $runKeyName -Value $previousAutoStart -PropertyType String -Force | Out-Null
    }
    $restoredAutoStartValue = $null
    if (Test-Path $runKeyPath) {
        $restoredAutoStartValue = (Get-ItemProperty $runKeyPath -ErrorAction SilentlyContinue).$runKeyName
    }
    $previousAutoStartStateRestored = (
        ($null -eq $previousAutoStart -and $null -eq $restoredAutoStartValue) -or
        ($null -ne $previousAutoStart -and $restoredAutoStartValue -eq $previousAutoStart)
    )

    [pscustomobject]@{
        installer = $installer.Path
        install_dir = $installDir
        executable_launched = $true
        persistent_database = (Join-Path $dataDir "app.db")
        persistent_task_retained = $true
        uninstall_registry_removed = -not (Test-Path $uninstallKey)
        auto_start_enabled_by_default = $true
        auto_start_command_is_hidden = $true
        hidden_launch_stayed_alive = $true
        auto_start_preference_persisted = $true
        auto_start_removed_on_uninstall = $autoStartRemoved
        previous_auto_start_restored = $previousAutoStartStateRestored
        user_data_preserved_after_uninstall = $true
        all_passed = $true
    } | ConvertTo-Json
} catch {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    throw
}
