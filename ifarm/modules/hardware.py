"""Hardware emulation — GPS spoofing and camera injection.

Module 3.1: GPS spoofing via idevicelocation (libimobiledevice).
Module 3.2: Camera frame injection via AVFoundation instrumentation.

Requires (Phase 3):
    brew install idevicelocation

Status: Phase 3 — not yet implemented.
"""
from __future__ import annotations

from pathlib import Path

from ifarm.exceptions import CapabilityNotAvailable
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module 3.1 — GPS Spoofing
# ---------------------------------------------------------------------------


def spoof_gps(udid: str, lat: float, lon: float) -> bool:
    """Inject GPS coordinates into the device's CoreLocation daemon.

    The device will report the spoofed location to all apps until
    clear_gps_spoof() is called or the device is restarted.

    Args:
        udid: Device UDID.
        lat: Target latitude (e.g. 32.7767 for Dallas).
        lon: Target longitude (e.g. -96.7970 for Dallas).

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If idevicelocation is not installed.
        NotImplementedError: Phase 3 — not yet implemented.
    """
    # TODO Phase 3: subprocess idevicelocation -u <udid> <lat> <lon>
    raise NotImplementedError("Phase 3")


def clear_gps_spoof(udid: str) -> bool:
    """Remove the GPS spoof and restore the device's real location.

    Args:
        udid: Device UDID.

    Returns:
        True on success.

    Raises:
        NotImplementedError: Phase 3 — not yet implemented.
    """
    # TODO Phase 3: idevicelocation -u <udid> -s (stop spoofing)
    raise NotImplementedError("Phase 3")


# ---------------------------------------------------------------------------
# Module 3.2 — Camera Injection
# ---------------------------------------------------------------------------


def inject_camera_frame(udid: str, image_path: Path | str) -> bool:
    """Inject a static image into the device's camera buffer.

    Intercepts the AVFoundation camera pipeline and replaces the live feed
    with a static image for the duration of the session.

    Args:
        udid: Device UDID.
        image_path: Path to the PNG/JPEG image to inject.

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If camera injection is not supported on this
            device/OS configuration (requires app instrumentation).
        NotImplementedError: Phase 3 — implementation pending research spike.
    """
    # TODO Phase 3: requires research spike — WebDriverAgent hook vs custom
    #   instrumentation. See plan notes on Module 3.2.
    raise NotImplementedError("Phase 3 — needs research spike first")


def inject_camera_video(udid: str, video_path: Path | str) -> bool:
    """Inject a looping video into the device's camera buffer.

    Args:
        udid: Device UDID.
        video_path: Path to the MP4/MOV file to inject.

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If camera injection is not supported.
        NotImplementedError: Phase 3 — implementation pending research spike.
    """
    raise NotImplementedError("Phase 3 — needs research spike first")
