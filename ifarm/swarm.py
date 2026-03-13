"""IFarmSwarmController — multi-device fleet orchestration.

Manages a pool of USB-tethered devices, distributes task queues across
them, and monitors health — automatically rotating IPs and requeuing
work when a device gets blocked.

Status: Phase 3 — not yet implemented.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifarm.utils.logger import get_logger

_log = get_logger(__name__)


@dataclass
class DeviceRecord:
    """Metadata for a single device in the pool.

    Attributes:
        udid: Device UDID (unique identifier).
        role: Logical role for task routing (e.g. "tiktok_scraper", "general").
        status: Current health state — "healthy" | "blocked" | "offline".
        assigned_task: ID or description of the currently running task, or None.
        extra: Arbitrary additional metadata from devices.json.
    """

    udid: str
    role: str = "general"
    status: str = "unknown"
    assigned_task: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "udid": self.udid,
            "role": self.role,
            "status": self.status,
            "assigned_task": self.assigned_task,
            **self.extra,
        }


class DevicePool:
    """Represents the collection of connected iOS devices.

    Build from config (static) or from live USB discovery (dynamic).
    """

    def __init__(self, devices: list[DeviceRecord]):
        self._devices: dict[str, DeviceRecord] = {d.udid: d for d in devices}

    @classmethod
    def discover(cls) -> "DevicePool":
        """Enumerate all connected devices via idevice_id (libimobiledevice).

        Returns:
            DevicePool containing all currently connected devices.

        Raises:
            NotImplementedError: Phase 3 — not yet implemented.
        """
        # TODO Phase 3: subprocess idevice_id -l, parse UDIDs, create DeviceRecords
        raise NotImplementedError("Phase 3")

    @classmethod
    def from_config(cls, path: Path | str) -> "DevicePool":
        """Load device pool from a devices.json file.

        Args:
            path: Path to devices.json.

        Returns:
            DevicePool with records from the config file.

        Raises:
            FileNotFoundError: If the path does not exist.
            NotImplementedError: Phase 3 — not yet implemented.
        """
        # TODO Phase 3: json.loads, map to DeviceRecord list
        raise NotImplementedError("Phase 3")

    @property
    def udids(self) -> list[str]:
        return list(self._devices.keys())

    @property
    def healthy(self) -> list[DeviceRecord]:
        return [d for d in self._devices.values() if d.status == "healthy"]

    def get(self, udid: str) -> DeviceRecord | None:
        return self._devices.get(udid)

    def update_status(self, udid: str, status: str) -> None:
        if udid in self._devices:
            self._devices[udid].status = status


class IFarmSwarmController:
    """Orchestrates tasks across a pool of IFarmController instances.

    Args:
        pool: DevicePool describing the available devices.

    Status: Phase 3 — not yet implemented.
    """

    def __init__(self, pool: DevicePool):
        self.pool = pool
        self.log = get_logger(__name__)
        self._controllers: dict[str, Any] = {}  # udid → IFarmController (lazy)

    def _get_controller(self, udid: str):
        """Lazy-load an IFarmController for the given UDID."""
        if udid not in self._controllers:
            from ifarm.controller import IFarmController
            self._controllers[udid] = IFarmController(udid)
        return self._controllers[udid]

    def distribute_tasks(self, task_queue: list[Any]) -> dict[str, list[Any]]:
        """Distribute a task list across healthy devices.

        Uses round-robin by default; role-based routing if tasks carry a
        "role" attribute matching device roles in the pool.

        Args:
            task_queue: List of task objects (dicts, strings, or callables).

        Returns:
            Dict mapping UDID → list of assigned tasks.

        Raises:
            NotImplementedError: Phase 3 — not yet implemented.
        """
        raise NotImplementedError("Phase 3")

    def run_health_monitor(self, interval_seconds: int = 30) -> None:
        """Poll device health, rotate IPs on blocked devices, requeue tasks.

        Runs in a loop (call in a background thread or async task).

        Args:
            interval_seconds: Seconds between health checks.

        Raises:
            NotImplementedError: Phase 3 — not yet implemented.
        """
        raise NotImplementedError("Phase 3")

    def get_swarm_status(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all device statuses.

        Returns:
            Dict with keys: total, healthy, blocked, offline, devices[].

        Raises:
            NotImplementedError: Phase 3 — not yet implemented.
        """
        raise NotImplementedError("Phase 3")
