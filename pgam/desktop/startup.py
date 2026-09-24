from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "PostGraduateAdmissionMonitor"


def default_startup_command() -> str:
    """Return a user-scope startup command that launches PGAM hidden in the tray."""
    if getattr(sys, "frozen", False):
        arguments = [sys.executable, "--hidden"]
    else:
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        executable = str(pythonw) if pythonw.exists() else sys.executable
        script = Path(__file__).resolve().parents[2] / "run_app.py"
        arguments = [executable, str(script), "--hidden"]
    return subprocess.list2cmdline(arguments)


class WindowsStartupManager:
    """Manage the current user's HKCU Run entry without administrator rights."""

    def __init__(
        self,
        *,
        value_name: str = VALUE_NAME,
        command: str | None = None,
        hive=winreg.HKEY_CURRENT_USER,
    ):
        self.value_name = value_name
        self.command = command or default_startup_command()
        self.hive = hive

    def is_enabled(self) -> bool:
        try:
            with winreg.OpenKey(self.hive, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, self.value_name)
                return isinstance(value, str) and bool(value.strip())
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def enable(self) -> None:
        with winreg.CreateKeyEx(self.hive, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.value_name, 0, winreg.REG_SZ, self.command)

    def disable(self) -> None:
        try:
            with winreg.OpenKey(self.hive, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.value_name)
        except FileNotFoundError:
            return

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.enable()
        else:
            self.disable()


class MemoryStartupManager:
    """Test double used by GUI tests; it never touches the real registry."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self.command = ""

    def is_enabled(self) -> bool:
        return self.enabled

    def enable(self) -> None:
        self.enabled = True
        self.command = "memory --hidden"

    def disable(self) -> None:
        self.enabled = False
        self.command = ""

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.enable()
        else:
            self.disable()
