"""Azure credentials stored in Windows' credential backend via keyring."""

from __future__ import annotations

import re
from dataclasses import dataclass


SERVICE_NAME = "DeutschOverlay.AzureSpeech"


@dataclass(frozen=True, slots=True, repr=False)
class AzureCredentials:
    region: str
    key: str

    def __repr__(self) -> str:
        return f"AzureCredentials(region={self.region!r}, key=<hidden>)"


class AzureCredentialStore:
    def __init__(self, backend=None) -> None:
        if backend is None:
            import keyring

            backend = keyring
        self.backend = backend

    def save(self, region: str, key: str) -> None:
        if not re.fullmatch(r"[a-z0-9-]{2,40}", region):
            raise ValueError("Azure region must be a short lowercase region name")
        if not 16 <= len(key) <= 256 or any(char.isspace() for char in key):
            raise ValueError("Azure key has an invalid format")
        self.backend.set_password(SERVICE_NAME, "region", region)
        self.backend.set_password(SERVICE_NAME, "key", key)

    def get(self) -> AzureCredentials | None:
        region = self.backend.get_password(SERVICE_NAME, "region")
        key = self.backend.get_password(SERVICE_NAME, "key")
        if not region or not key:
            return None
        return AzureCredentials(region, key)
