"""USB device discovery utilities.

Wraps libimobiledevice CLI tools to enumerate connected iOS devices.

Requires:
    brew install libimobiledevice
"""
from __future__ import annotations

import subprocess

from ifarm.exceptions import DeviceNotFoundError
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)

_LIBIMOBILEDEVICE_HINT = (
    "Install libimobiledevice: brew install libimobiledevice"
)


def list_connected_udids() -> list[str]:
    """Return UDIDs of all iOS devices currently connected via USB.

    Args:
        None

    Returns:
        List of UDID strings. Empty list if no devices are connected.

    Raises:
        FileNotFoundError: If idevice_id is not installed.
    """
    try:
        result = subprocess.run(
            ["idevice_id", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            f"idevice_id not found. {_LIBIMOBILEDEVICE_HINT}"
        )

    if result.returncode != 0:
        _log.warning("idevice_id returned non-zero", extra={"stderr": result.stderr})
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def assert_device_connected(udid: str) -> None:
    """Raise DeviceNotFoundError if the device is not currently connected.

    Args:
        udid: Device UDID to check.

    Raises:
        DeviceNotFoundError: If the device is not found.
        FileNotFoundError: If idevice_id is not installed.
    """
    connected = list_connected_udids()
    if udid not in connected:
        raise DeviceNotFoundError(
            f"Device {udid} not found. Connected: {connected or 'none'}. "
            "Check USB cable and trust prompt on the device."
        )


def get_device_info(udid: str, key: str | None = None) -> str:
    """Return ideviceinfo output for a connected device.

    Args:
        udid: Device UDID.
        key: Specific key to query (e.g. "ProductType"). Returns all info if None.

    Returns:
        Raw string output from ideviceinfo.

    Raises:
        DeviceNotFoundError: If the device is not connected.
        FileNotFoundError: If ideviceinfo is not installed.
    """
    cmd = ["ideviceinfo", "-u", udid]
    if key:
        cmd += ["-k", key]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"ideviceinfo not found. {_LIBIMOBILEDEVICE_HINT}"
        )

    if result.returncode != 0:
        raise DeviceNotFoundError(
            f"ideviceinfo failed for {udid}: {result.stderr.strip()}"
        )

    return result.stdout.strip()
