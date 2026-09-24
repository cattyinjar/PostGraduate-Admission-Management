# 0.1.0 Windows installer release note

## Artifact

- Installer: `dist/installer/PGAM-0.1.0-x64.exe`
- Size: `47,742,475` bytes (`45.53 MB`)
- SHA-256: `3D738F3BBCD1EA97C81867CCE4EE22D03D1A295084D9E62D193E06B201897138`
- Installer framework: Inno Setup 6.7.3
- Application framework: PyInstaller one-dir
- Target system: Windows 10 or later, 64-bit
- Install scope: current user

## Build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-installer.ps1
```

The build script installs pinned dependencies, runs pytest and Ruff, builds the PyInstaller one-dir application, and compiles the Inno Setup installer.

## Install verification

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-installer.ps1 -InstallerPath .\dist\installer\PGAM-0.1.0-x64.exe
```

Latest result:

```json
{
  "executable_launched": true,
  "persistent_task_retained": true,
  "uninstall_registry_removed": true,
  "user_data_preserved_after_uninstall": true,
  "all_passed": true
}
```

The verifier performs:

1. Silent installation into an isolated test directory.
2. Windows uninstall registry validation.
3. Installed executable launch.
4. Persistent SQLite database creation under an isolated `%APPDATA%`.
5. Application restart.
6. Task persistence validation.
7. Silent uninstallation.
8. Program removal validation.
9. User data retention validation.

## User data

The installer does not place user data in the installation directory. Application data is stored under:

```text
%APPDATA%\PostGraduateAdmissionMonitor
```

Uninstalling the application preserves this directory and the SQLite database inside it.

## Regression

The release build was validated with:

```text
32 passed
Ruff: all checks passed
Python compileall: passed
```
