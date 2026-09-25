# 0.1.0 Windows installer release note

## Artifact

- Installer: `dist/installer/PGAM-0.1.0-x64.exe`
- Size: `47,783,943` bytes (`45.53 MB`)
- SHA-256: `A7EEF8D71E178E68484F276627BE2C48D8100FC2ED782FF94067B5CC08FABD1D`
- Installer framework: Inno Setup 6.7.3
- Application framework: PyInstaller one-dir
- Target system: Windows 10 or later, 64-bit
- Install scope: current user
- Auto-start: enabled by default for the current user; configurable in Settings
- Task list: creation order, with newly created tasks appended at the end
- Time display: user-configurable timezone for tasks, messages, diagnostics, and emails; UTC remains the storage format
- Diagnostics: run table and detail text are read-only
- Secret fields: length-equal masks with reveal/hide, copy, and replace controls
- Runtime hardening: packaged trafilatura settings/data, extractor fallback, and Markdown URL normalization
- Scheduler: background asyncio loop remains alive, so interval checks and pending notifications run without a manual GUI action

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
  "auto_start_enabled_by_default": true,
  "auto_start_command_is_hidden": true,
  "hidden_launch_stayed_alive": true,
  "auto_start_preference_persisted": true,
  "auto_start_removed_on_uninstall": true,
  "user_data_preserved_after_uninstall": true,
  "all_passed": true
}
```

The verifier performs:

1. Silent installation into an isolated test directory.
2. Windows uninstall registry validation.
3. Installed executable launch with `--hidden`.
4. Persistent SQLite database creation under an isolated `%APPDATA%`.
5. Application restart.
6. Task persistence validation.
7. Silent uninstallation.
8. Program removal validation.
9. Auto-start default, command, persistence, and uninstall cleanup validation.
10. User data retention validation.

## User data

The installer does not place user data in the installation directory. Application data is stored under:

```text
%APPDATA%\PostGraduateAdmissionMonitor
```

Uninstalling the application preserves this directory and the SQLite database inside it.

## Regression

The release build was validated with:

```text
47 passed
Ruff: all checks passed
Python compileall: passed
```
