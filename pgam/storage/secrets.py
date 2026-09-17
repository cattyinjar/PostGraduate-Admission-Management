from __future__ import annotations

import threading

import keyring
from keyring.errors import KeyringError


class KeyringSecretStore:
    SERVICE = "PostGraduateAdmissionMonitor"

    def get(self, service: str, name: str) -> str | None:
        try:
            value = keyring.get_password(self.SERVICE, f"{service}:{name}")
            return value
        except KeyringError:
            return None

    def set(self, service: str, name: str, value: str) -> None:
        keyring.set_password(self.SERVICE, f"{service}:{name}", value)

    def delete(self, service: str, name: str) -> None:
        try:
            keyring.delete_password(self.SERVICE, f"{service}:{name}")
        except KeyringError:
            return


class InMemorySecretStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, service: str, name: str) -> str | None:
        with self._lock:
            return self._data.get(f"{service}:{name}")

    def set(self, service: str, name: str, value: str) -> None:
        with self._lock:
            self._data[f"{service}:{name}"] = value

    def delete(self, service: str, name: str) -> None:
        with self._lock:
            self._data.pop(f"{service}:{name}", None)
