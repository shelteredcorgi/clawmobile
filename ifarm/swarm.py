"""IFarmSwarmController — multi-device fleet orchestration.

Manages a pool of USB-tethered devices, distributes task queues across
them, and monitors health — automatically rotating IPs and requeuing
work when a device is blocked or goes offline.

Typical usage:

    # Build pool from config
    pool = DevicePool.from_config("config/devices.json")

    # Or discover whatever is plugged in right now
    pool = DevicePool.discover()

    swarm = IFarmSwarmController(pool)

    # Hand out work
    assignments = swarm.distribute_tasks([
        {"action": "scrape", "role": "tiktok_scraper"},
        {"action": "scrape", "role": "tiktok_scraper"},
        {"action": "rotate_ip"},
    ])

    # Monitor in background
    import threading
    stop = threading.Event()
    t = threading.Thread(target=swarm.run_health_monitor,
                         kwargs={"stop_event": stop}, daemon=True)
    t.start()

    # Check status at any time
    print(swarm.get_swarm_status())

    stop.set()
    t.join()
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifarm.utils.logger import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# DeviceRecord
# ---------------------------------------------------------------------------


@dataclass
class DeviceRecord:
    """Metadata for a single device in the pool.

    Attributes:
        udid: Device UDID (unique identifier from idevice_id).
        role: Logical role for task routing (e.g. "tiktok_scraper",
            "general"). Matched against task "role" keys in distribute_tasks.
        status: Current health state.
            "healthy"  — connected and passing probe
            "blocked"  — connected but IP probe failing (rate-limited)
            "offline"  — not detected by idevice_id
            "unknown"  — not yet probed
        assigned_task: Description/ID of the currently running task, or None.
        extra: Arbitrary additional metadata from devices.json (notes, etc.).
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


# ---------------------------------------------------------------------------
# DevicePool
# ---------------------------------------------------------------------------


class DevicePool:
    """Represents the collection of connected iOS devices.

    Build from config (static) or from live USB discovery (dynamic).
    In production you usually want from_config() so roles are preserved,
    calling discover() only to check what is actually plugged in.
    """

    def __init__(self, devices: list[DeviceRecord]):
        self._devices: dict[str, DeviceRecord] = {d.udid: d for d in devices}

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def discover(cls) -> "DevicePool":
        """Enumerate all connected devices via idevice_id (libimobiledevice).

        All discovered devices start with role="general" and status="unknown".
        Use from_config() to preserve role assignments from devices.json.

        Returns:
            DevicePool containing all currently connected devices.

        Raises:
            FileNotFoundError: If idevice_id (libimobiledevice) is not
                installed.
        """
        from ifarm.utils.device import list_connected_udids

        udids = list_connected_udids()
        devices = [DeviceRecord(udid=u, status="unknown") for u in udids]
        _log.info("Device discovery complete", extra={"count": len(devices)})
        return cls(devices)

    @classmethod
    def from_config(cls, path: Path | str) -> "DevicePool":
        """Load device pool from a devices.json file.

        Preserves role and notes from the config. Status starts as "unknown"
        until the health monitor runs its first probe.

        Args:
            path: Path to devices.json (see config/devices.example.json).

        Returns:
            DevicePool with records from the config file.

        Raises:
            FileNotFoundError: If path does not exist.
            ValueError: If the JSON is malformed or missing required fields.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"devices.json not found at {p}. "
                "Copy config/devices.example.json → config/devices.json "
                "and edit with real UDIDs."
            )

        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {p}: {e}") from e

        if not isinstance(data, list):
            raise ValueError(f"{p} must contain a JSON array of device objects.")

        devices = []
        for i, entry in enumerate(data):
            if "udid" not in entry:
                raise ValueError(
                    f"Device entry {i} in {p} is missing required 'udid' field."
                )
            extra = {k: v for k, v in entry.items() if k not in ("udid", "role")}
            devices.append(
                DeviceRecord(
                    udid=entry["udid"],
                    role=entry.get("role", "general"),
                    status="unknown",
                    extra=extra,
                )
            )

        _log.info("Pool loaded from config", extra={"path": str(p), "count": len(devices)})
        return cls(devices)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def udids(self) -> list[str]:
        """List of all UDIDs in the pool (any status)."""
        return list(self._devices.keys())

    @property
    def healthy(self) -> list[DeviceRecord]:
        """Devices currently in 'healthy' status."""
        return [d for d in self._devices.values() if d.status == "healthy"]

    @property
    def all_devices(self) -> list[DeviceRecord]:
        """All devices regardless of status."""
        return list(self._devices.values())

    def get(self, udid: str) -> DeviceRecord | None:
        """Return the DeviceRecord for a UDID, or None if not in pool."""
        return self._devices.get(udid)

    def update_status(self, udid: str, status: str) -> None:
        """Update a device's status in place.

        Args:
            udid: Device UDID.
            status: New status string ("healthy", "blocked", "offline",
                "unknown").
        """
        if udid in self._devices:
            old = self._devices[udid].status
            self._devices[udid].status = status
            if old != status:
                _log.info(
                    "Device status changed",
                    extra={"udid": udid, "old": old, "new": status},
                )


# ---------------------------------------------------------------------------
# IFarmSwarmController
# ---------------------------------------------------------------------------


class IFarmSwarmController:
    """Orchestrates tasks across a pool of IFarmController instances.

    Task routing:
        Tasks that are plain strings or dicts without a "role" key are
        distributed round-robin across healthy devices.

        Tasks that are dicts with a "role" key are preferentially routed
        to a healthy device whose role matches. If no matching device is
        available, they fall back to round-robin.

    Args:
        pool: DevicePool describing the available devices.
    """

    def __init__(self, pool: DevicePool):
        self.pool = pool
        self.log = get_logger(__name__)
        self._controllers: dict[str, Any] = {}  # udid → IFarmController (lazy)
        self._task_assignments: dict[str, list[Any]] = {}  # udid → task list
        self._requeue_buffer: list[Any] = []  # tasks waiting for a healthy device
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Controller access
    # ------------------------------------------------------------------

    def _get_controller(self, udid: str):
        """Lazy-load an IFarmController for a given UDID."""
        if udid not in self._controllers:
            from ifarm.controller import IFarmController
            self._controllers[udid] = IFarmController(udid)
        return self._controllers[udid]

    # ------------------------------------------------------------------
    # Task distribution
    # ------------------------------------------------------------------

    def distribute_tasks(self, task_queue: list[Any]) -> dict[str, list[Any]]:
        """Distribute a task list across healthy devices.

        Role-based routing: tasks with a "role" key (dicts) or a `.role`
        attribute are first offered to a healthy device with a matching role.
        If no matching device is available the task falls back to round-robin.

        Args:
            task_queue: List of tasks. Each task may be:
                - A string (identifier, round-robin routed)
                - A dict (optionally with "role" key for preferred routing)
                - Any object with a .role attribute

        Returns:
            Dict mapping UDID → list of assigned tasks. Devices with no
            assigned tasks are omitted. Tasks that cannot be assigned
            (no healthy devices) are stored in self._requeue_buffer.

        Raises:
            RuntimeError: If there are no healthy devices at all.
        """
        healthy = self.pool.healthy
        if not healthy:
            raise RuntimeError(
                "No healthy devices in the pool. "
                "Run `ifarm doctor` and check device connections."
            )

        assignments: dict[str, list[Any]] = {d.udid: [] for d in healthy}
        rr_index = 0  # round-robin cursor

        for task in task_queue:
            role = _get_task_role(task)
            target_udid: str | None = None

            # Try to find a healthy device matching the task's role
            if role:
                for device in healthy:
                    if device.role == role:
                        target_udid = device.udid
                        break

            # Fall back to round-robin if no role match
            if target_udid is None:
                target_udid = healthy[rr_index % len(healthy)].udid
                rr_index += 1

            assignments[target_udid].append(task)

        # Remove empty assignments
        assignments = {u: tasks for u, tasks in assignments.items() if tasks}

        with self._lock:
            self._task_assignments = dict(assignments)

        self.log.info(
            "Tasks distributed",
            extra={
                "total_tasks": len(task_queue),
                "devices": len(assignments),
            },
        )
        return assignments

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    def run_health_monitor(
        self,
        interval_seconds: int = 30,
        stop_event: threading.Event | None = None,
        auto_rotate: bool = True,
    ) -> None:
        """Poll device health, rotate IPs on blocked devices, requeue tasks.

        Designed to run in a background thread. Pass a threading.Event to
        stop_event for graceful shutdown.

        Args:
            interval_seconds: Seconds between health check cycles.
            stop_event: Set this event to stop the monitor loop cleanly.
                If None, the monitor runs until the process exits.
            auto_rotate: If True, attempt IP rotation on blocked devices.
        """
        stop_event = stop_event or threading.Event()

        self.log.info(
            "Health monitor started",
            extra={"interval_seconds": interval_seconds},
        )

        while not stop_event.is_set():
            self._run_health_cycle(auto_rotate=auto_rotate)
            stop_event.wait(timeout=interval_seconds)

        self.log.info("Health monitor stopped")

    def _run_health_cycle(self, auto_rotate: bool = True) -> None:
        """Execute one health check pass across all devices in the pool."""
        try:
            from ifarm.utils.device import list_connected_udids
            connected = set(list_connected_udids())
        except FileNotFoundError:
            self.log.warning("idevice_id not found — skipping connectivity check")
            connected = set(self.pool.udids)

        requeued: list[Any] = []

        for device in self.pool.all_devices:
            if device.udid not in connected:
                if device.status != "offline":
                    self.pool.update_status(device.udid, "offline")
                    requeued.extend(self._drain_device_tasks(device.udid))
                continue

            # Device is connected — probe with a lightweight IP check
            try:
                ctrl = self._get_controller(device.udid)
                ctrl.get_current_ip()
                self.pool.update_status(device.udid, "healthy")
            except Exception as e:
                self.log.warning(
                    "Device probe failed",
                    extra={"udid": device.udid, "error": str(e)},
                )
                self.pool.update_status(device.udid, "blocked")
                requeued.extend(self._drain_device_tasks(device.udid))

                if auto_rotate:
                    try:
                        ctrl = self._get_controller(device.udid)
                        new_ip = ctrl.cycle_airplane_mode()
                        self.pool.update_status(device.udid, "healthy")
                        self.log.info(
                            "IP rotated on blocked device",
                            extra={"udid": device.udid, "new_ip": new_ip},
                        )
                    except Exception as rot_e:
                        self.log.warning(
                            "IP rotation failed",
                            extra={"udid": device.udid, "error": str(rot_e)},
                        )

        if requeued:
            with self._lock:
                self._requeue_buffer.extend(requeued)
            self.log.info(
                "Tasks requeued from unavailable devices",
                extra={"count": len(requeued)},
            )

    def _drain_device_tasks(self, udid: str) -> list[Any]:
        """Remove and return all tasks assigned to a device (for requeuing)."""
        with self._lock:
            tasks = self._task_assignments.pop(udid, [])
        return tasks

    def flush_requeue_buffer(self) -> list[Any]:
        """Return and clear all tasks that were requeued due to device failure.

        Call this after a health cycle to redistribute orphaned tasks.

        Returns:
            List of task objects that need to be re-assigned.
        """
        with self._lock:
            tasks = list(self._requeue_buffer)
            self._requeue_buffer.clear()
        return tasks

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_swarm_status(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all device statuses.

        Returns:
            Dict with structure:
            {
                "total": int,
                "healthy": int,
                "blocked": int,
                "offline": int,
                "unknown": int,
                "pending_requeue": int,
                "devices": [DeviceRecord.to_dict(), ...]
            }
        """
        devices = self.pool.all_devices
        counts: dict[str, int] = {"healthy": 0, "blocked": 0, "offline": 0, "unknown": 0}
        for d in devices:
            counts[d.status] = counts.get(d.status, 0) + 1

        with self._lock:
            pending = len(self._requeue_buffer)

        return {
            "total": len(devices),
            **counts,
            "pending_requeue": pending,
            "devices": [d.to_dict() for d in devices],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_task_role(task: Any) -> str | None:
    """Extract the role from a task object, or None if not specified."""
    if isinstance(task, dict):
        return task.get("role")
    return getattr(task, "role", None)
