"""
Small helpers: SHA-256 hashing, file chunking, human-readable formatting,
and persistent device identity (UUID + friendly name).
"""

import hashlib
import json
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Generator

from config import CHUNK_SIZE, DEVICE_FILE


# ── File hashing & chunking ─────────────────────────────────────────────────

def compute_sha256(filepath: str) -> str:
    """Return the lowercase hex SHA-256 digest of *filepath*."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_chunks(filepath: str, chunk_size: int = CHUNK_SIZE) -> Generator[bytes, None, None]:
    """Yield successive byte-chunks of at most *chunk_size* from *filepath*."""
    with open(filepath, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                return
            yield data


# ── Formatting ──────────────────────────────────────────────────────────────

def format_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_speed(bps: float) -> str:
    return format_size(bps) + "/s"


# ── Device identity (persistent UUID + friendly name) ──────────────────────

@dataclass
class Device:
    """This machine's stable identity, persisted to ~/.lan_share_device.json."""

    device_id: str
    name:      str

    @classmethod
    def load_or_create(cls, override_name: str | None = None) -> "Device":
        if os.path.exists(DEVICE_FILE):
            try:
                with open(DEVICE_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                dev = cls(d["device_id"], d["name"])
            except (OSError, json.JSONDecodeError, KeyError):
                dev = cls(uuid.uuid4().hex, socket.gethostname())
        else:
            dev = cls(uuid.uuid4().hex, socket.gethostname())

        if override_name and override_name.strip():
            dev.name = override_name.strip()
        dev.save()
        return dev

    def save(self) -> None:
        with open(DEVICE_FILE, "w", encoding="utf-8") as f:
            json.dump({"device_id": self.device_id, "name": self.name}, f)
