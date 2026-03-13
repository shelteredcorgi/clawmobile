"""Cellular proxy and IP rotation module.

Manages the macOS network interface for the USB-tethered iPhone and
rotates the cellular IP.

IP rotation strategy:
  - detect_usb_interface(): parse `networksetup -listallhardwareports`
  - establish_cellular_route(): elevate USB interface via `networksetup -ordernetworkservices`
  - cycle_airplane_mode(): bounce the USB hotspot interface on the Mac side
    (brings the interface down, waits, brings it back up — no Appium required).

NOTE: True airplane mode toggle (more reliable for carrier IP reassignment)
requires Appium's `mobile: setAirplaneMode` command. Set
proxy.rotation_strategy = "appium" in ifarm.toml to enable it when Appium
is installed.

Requires:
    /usr/sbin/networksetup (macOS built-in — always present)
    pip install requests  (for IP probe)
"""
from __future__ import annotations

import subprocess
import time

try:
    import requests as requests
except ImportError:
    requests = None  # type: ignore[assignment]

from ifarm.exceptions import ProxyError
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)

# Default wait after bouncing the interface before probing the new IP.
_DEFAULT_RECONNECT_WAIT = 8  # seconds


def detect_usb_interface(udid: str) -> str:
    """Return the macOS network interface name for the given tethered device.

    Parses `networksetup -listallhardwareports` looking for an entry that
    contains "iPhone" or "Personal Hotspot" in the Hardware Port name.

    Args:
        udid: Device UDID (used for log correlation; interface detection is
            currently Mac-side only and does not query the device directly).

    Returns:
        Interface name string (e.g. "en5").

    Raises:
        ProxyError: If no USB hotspot interface is found.
    """
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise ProxyError(f"networksetup failed: {e.stderr.strip()}") from e
    except FileNotFoundError:
        raise ProxyError("networksetup not found — is this running on macOS?")

    lines = result.stdout.splitlines()
    for i, line in enumerate(lines):
        if any(kw in line for kw in ("iPhone", "Personal Hotspot", "iPhone USB")):
            # The Device: line immediately follows Hardware Port:
            for j in range(i, min(i + 5, len(lines))):
                if lines[j].strip().startswith("Device:"):
                    interface = lines[j].split(":", 1)[1].strip()
                    _log.info(
                        "Found USB hotspot interface",
                        extra={"udid": udid, "interface": interface},
                    )
                    return interface

    raise ProxyError(
        f"No USB hotspot interface found for device {udid}. "
        "Ensure Personal Hotspot → USB Only is enabled on the iPhone."
    )


def establish_cellular_route(interface: str) -> bool:
    """Elevate the USB network interface to the top of macOS service priority.

    Calls `networksetup -ordernetworkservices` to move the iPhone USB
    interface above Wi-Fi and Ethernet so all traffic routes through it.

    Args:
        interface: Interface name returned by detect_usb_interface().

    Returns:
        True on success.

    Raises:
        ProxyError: If the networksetup command fails.
    """
    # Get current ordered service list
    try:
        list_result = subprocess.run(
            ["networksetup", "-listnetworkserviceorder"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise ProxyError(f"Failed to list network services: {e.stderr.strip()}") from e

    # Parse service names from output like: (1) Wi-Fi\n(*) Hardware ...
    services: list[str] = []
    for line in list_result.stdout.splitlines():
        line = line.strip()
        if line.startswith("(") and ")" in line:
            # Skip the (*) Hardware Port lines
            if "Hardware Port:" not in line:
                name = line.split(")", 1)[1].strip()
                if name:
                    services.append(name)

    if not services:
        raise ProxyError("Could not parse network service list.")

    # Find which service corresponds to our interface
    usb_service = _find_service_for_interface(interface)
    if usb_service and usb_service in services:
        services.remove(usb_service)
        services.insert(0, usb_service)
    else:
        _log.warning(
            "Could not map interface to a named service; using interface name directly",
            extra={"interface": interface},
        )
        # Best effort: just log — ordering by interface name isn't supported
        return True

    try:
        subprocess.run(
            ["networksetup", "-ordernetworkservices"] + services,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        _log.info("Cellular route established", extra={"service": usb_service})
        return True
    except subprocess.CalledProcessError as e:
        raise ProxyError(
            f"Failed to order network services: {e.stderr.strip()}"
        ) from e


def _find_service_for_interface(interface: str) -> str | None:
    """Map a device interface name to its network service name."""
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception:
        return None

    lines = result.stdout.splitlines()
    for i, line in enumerate(lines):
        if f"Device: {interface}" in line:
            # Service name is in the Hardware Port: line just above
            for j in range(i - 1, max(i - 5, -1), -1):
                if "Hardware Port:" in lines[j]:
                    return lines[j].split(":", 1)[1].strip()
    return None


def cycle_airplane_mode(
    udid: str,
    wait_seconds: int = _DEFAULT_RECONNECT_WAIT,
) -> str:
    """Bounce the USB hotspot interface to obtain a fresh carrier IP.

    Brings the iPhone's USB network interface down on the Mac side,
    waits for the carrier to release the session, then brings it back up.
    Does not require Appium.

    For guaranteed airplane mode toggle (and stronger IP reassignment),
    set proxy.rotation_strategy = "appium" in ifarm.toml when Appium
    is installed.

    Args:
        udid: Device UDID (used for interface lookup and logging).
        wait_seconds: Seconds to wait between down and up. Longer waits
            increase the chance of a fresh IP assignment by the carrier.

    Returns:
        New public IP address string.

    Raises:
        ProxyError: If the interface bounce or IP probe fails.
    """
    interface = detect_usb_interface(udid)

    _log.info("Bouncing USB hotspot interface", extra={"interface": interface})

    for cmd in [
        ["networksetup", "-setnetworkserviceenabled", interface, "off"],
        # Brief pause — not needed for networksetup but aids some carriers
    ]:
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
        except subprocess.CalledProcessError as e:
            # Try via ifconfig as fallback
            _ifconfig_set(interface, up=False)
            break

    time.sleep(wait_seconds)

    for cmd in [
        ["networksetup", "-setnetworkserviceenabled", interface, "on"],
    ]:
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
        except subprocess.CalledProcessError:
            _ifconfig_set(interface, up=True)
            break

    # Allow the interface to re-establish
    time.sleep(3)

    new_ip = get_current_ip()
    _log.info("IP rotation complete", extra={"new_ip": new_ip})
    return new_ip


def _ifconfig_set(interface: str, up: bool) -> None:
    """Fallback: use ifconfig to bring an interface up or down."""
    flag = "up" if up else "down"
    try:
        subprocess.run(
            ["ifconfig", interface, flag],
            capture_output=True,
            timeout=5,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise ProxyError(
            f"ifconfig {interface} {flag} failed — try running with sudo: {e}"
        ) from e


def get_current_ip(probe_url: str = "https://api.ipify.org") -> str:
    """Return the current public IP address via an external probe.

    Args:
        probe_url: URL that returns the public IP as plain text.
            Defaults to api.ipify.org.

    Returns:
        Public IP address string.

    Raises:
        ProxyError: If the probe request fails.
    """
    if requests is None:
        raise ProxyError(
            "requests is required for IP probing. "
            "Install with: pip install ifarm[network]"
        )

    try:
        resp = requests.get(probe_url, timeout=10)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        raise ProxyError(f"IP probe failed ({probe_url}): {e}") from e
