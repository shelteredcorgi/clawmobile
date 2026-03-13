"""Hardware emulation — GPS spoofing and camera injection.

Module 3.1 — GPS Spoofing via idevicelocation
  Injects CoreLocation coordinates into a connected device. The device
  reports the spoofed location to every app until the spoof is cleared
  or the device restarts.

  Setup:
      brew install idevicelocation

Module 3.2 — Camera Frame/Video Injection via Appium XCUITest
  Replaces the device's live camera feed with a static image or looping
  video using Appium's mobile:startCameraImageInjection command.

  Requirements:
      - Appium with XCUITest driver (pip install ifarm[automation])
      - iOS 17+ (earlier versions have limited simulator-only support)
      - The target app must be signed with the entitlement:
          com.apple.developer.debugging.allow-simulated-media
        or launched via Xcode / Developer Mode with proper provisioning.
      - Run `ifarm doctor` to confirm Appium is available.
"""
from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path

from ifarm.exceptions import CapabilityNotAvailable, IFarmError
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)

_IDEVICELOCATION_HINT = "Install idevicelocation: brew install idevicelocation"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a subprocess command, return (returncode, combined output)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return -1, f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return -1, f"{cmd[0]}: timed out after {timeout}s"
    except Exception as e:
        return -1, str(e)


def _require_idevicelocation() -> None:
    """Raise CapabilityNotAvailable if idevicelocation is not installed."""
    code, _ = _run(["idevicelocation", "--help"])
    if code == -1:
        raise CapabilityNotAvailable(
            f"idevicelocation is not installed. {_IDEVICELOCATION_HINT}"
        )


# ---------------------------------------------------------------------------
# Module 3.1 — GPS Spoofing
# ---------------------------------------------------------------------------


def spoof_gps(udid: str, lat: float, lon: float) -> bool:
    """Inject GPS coordinates into the device's CoreLocation daemon.

    The device reports the spoofed location to all apps until
    clear_gps_spoof() is called or the device restarts.

    Args:
        udid: Device UDID.
        lat: Target latitude (-90.0 to 90.0).
        lon: Target longitude (-180.0 to 180.0).

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If idevicelocation is not installed.
        ValueError: If lat/lon are out of valid range.
        IFarmError: If the command fails.
    """
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude must be between -180 and 180, got {lon}")

    _require_idevicelocation()

    code, out = _run(["idevicelocation", "-u", udid, str(lat), str(lon)])
    if code != 0:
        raise IFarmError(f"GPS spoof failed for {udid}: {out}")

    _log.info("GPS spoofed", extra={"udid": udid, "lat": lat, "lon": lon})
    return True


def clear_gps_spoof(udid: str) -> bool:
    """Remove the GPS spoof and restore the device's real location.

    Args:
        udid: Device UDID.

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If idevicelocation is not installed.
        IFarmError: If the command fails.
    """
    _require_idevicelocation()

    code, out = _run(["idevicelocation", "-u", udid, "-s"])
    if code != 0:
        raise IFarmError(f"Failed to clear GPS spoof for {udid}: {out}")

    _log.info("GPS spoof cleared", extra={"udid": udid})
    return True


def spoof_gps_preset(udid: str, preset_name: str, locations: dict) -> bool:
    """Spoof GPS using a named location preset from ifarm.toml [locations].

    Args:
        udid: Device UDID.
        preset_name: Key under [locations] in ifarm.toml
            (e.g. "dallas", "miami").
        locations: The config.locations dict loaded from ifarm.toml.

    Returns:
        True on success.

    Raises:
        KeyError: If preset_name is not found in locations.
        CapabilityNotAvailable: If idevicelocation is not installed.
        IFarmError: If the command fails.
    """
    if preset_name not in locations:
        available = list(locations.keys())
        raise KeyError(
            f"Location preset '{preset_name}' not found. "
            f"Available presets: {available}. "
            "Add presets to [locations] in ifarm.toml."
        )
    loc = locations[preset_name]
    lat = float(loc["lat"])
    lon = float(loc["lon"])
    _log.info(
        "GPS preset spoof",
        extra={"udid": udid, "preset": preset_name, "lat": lat, "lon": lon},
    )
    return spoof_gps(udid, lat, lon)


# ---------------------------------------------------------------------------
# Module 3.2 — Camera Injection
# ---------------------------------------------------------------------------


def inject_camera_frame(
    udid: str,
    image_path: Path | str,
    bundle_id: str,
    port: int = 4723,
) -> bool:
    """Inject a static image into the device's camera feed via Appium.

    Uses Appium's `mobile: startCameraImageInjection` XCUITest command to
    replace the live camera with a static PNG/JPEG. The injection persists
    for the life of the Appium session or until stop_camera_injection() is
    called.

    Requirements:
        - Appium with XCUITest driver running (npm install -g appium)
        - iOS 17+ recommended (earlier versions: simulator only)
        - Target app signed with
          com.apple.developer.debugging.allow-simulated-media entitlement

    Args:
        udid: Device UDID.
        image_path: Path to PNG/JPEG image to inject.
        bundle_id: Bundle ID of the app that will access the camera
            (e.g. "com.example.kycapp").
        port: Appium server port. Default 4723.

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If Appium is not installed.
        FileNotFoundError: If image_path does not exist.
        IFarmError: If injection fails.
    """
    try:
        from appium.options import XCUITestOptions
        import appium.webdriver as _appium_webdriver
    except ImportError:
        raise CapabilityNotAvailable(
            "Appium-Python-Client is required for camera injection. "
            "Install with: pip install ifarm[automation]"
        )

    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image_b64 = base64.b64encode(image_path.read_bytes()).decode()

    opts = XCUITestOptions()
    opts.udid = udid
    opts.bundle_id = bundle_id
    opts.no_reset = True
    opts.should_terminate_app = False

    try:
        driver = _appium_webdriver.Remote(
            f"http://localhost:{port}", options=opts
        )
    except Exception as e:
        raise IFarmError(
            f"Failed to connect to Appium at port {port}: {e}. "
            "Ensure Appium is running: appium --port 4723"
        ) from e

    try:
        driver.execute_script(
            "mobile: startCameraImageInjection",
            {"payload": image_b64},
        )
        _log.info(
            "Camera frame injected",
            extra={"udid": udid, "image": str(image_path)},
        )
        return True
    except Exception as e:
        raise IFarmError(
            f"Camera injection failed: {e}. "
            "Ensure the app has the 'com.apple.developer.debugging"
            ".allow-simulated-media' entitlement and iOS 17+ is in use."
        ) from e
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def inject_camera_video(
    udid: str,
    video_path: Path | str,
    bundle_id: str,
    port: int = 4723,
) -> bool:
    """Inject a looping video into the device's camera feed via Appium.

    Uses Appium's `mobile: startCameraVideoInjection` XCUITest command.
    The same requirements as inject_camera_frame() apply.

    Args:
        udid: Device UDID.
        video_path: Path to MP4/MOV file to inject.
        bundle_id: Bundle ID of the app accessing the camera.
        port: Appium server port. Default 4723.

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If Appium is not installed.
        FileNotFoundError: If video_path does not exist.
        IFarmError: If injection fails.
    """
    try:
        from appium.options import XCUITestOptions
        import appium.webdriver as _appium_webdriver
    except ImportError:
        raise CapabilityNotAvailable(
            "Appium-Python-Client is required for camera injection. "
            "Install with: pip install ifarm[automation]"
        )

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    video_b64 = base64.b64encode(video_path.read_bytes()).decode()

    opts = XCUITestOptions()
    opts.udid = udid
    opts.bundle_id = bundle_id
    opts.no_reset = True
    opts.should_terminate_app = False

    try:
        driver = _appium_webdriver.Remote(
            f"http://localhost:{port}", options=opts
        )
    except Exception as e:
        raise IFarmError(
            f"Failed to connect to Appium at port {port}: {e}"
        ) from e

    try:
        driver.execute_script(
            "mobile: startCameraVideoInjection",
            {"payload": video_b64},
        )
        _log.info(
            "Camera video injected",
            extra={"udid": udid, "video": str(video_path)},
        )
        return True
    except Exception as e:
        raise IFarmError(f"Camera video injection failed: {e}") from e
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def stop_camera_injection(udid: str, bundle_id: str, port: int = 4723) -> bool:
    """Stop an active camera injection and restore the live camera feed.

    Args:
        udid: Device UDID.
        bundle_id: Bundle ID of the app (must match the injection session).
        port: Appium server port. Default 4723.

    Returns:
        True on success.

    Raises:
        CapabilityNotAvailable: If Appium is not installed.
        IFarmError: If the stop command fails.
    """
    try:
        from appium.options import XCUITestOptions
        import appium.webdriver as _appium_webdriver
    except ImportError:
        raise CapabilityNotAvailable(
            "Appium-Python-Client is required. "
            "Install with: pip install ifarm[automation]"
        )

    opts = XCUITestOptions()
    opts.udid = udid
    opts.bundle_id = bundle_id
    opts.no_reset = True
    opts.should_terminate_app = False

    try:
        driver = _appium_webdriver.Remote(
            f"http://localhost:{port}", options=opts
        )
    except Exception as e:
        raise IFarmError(
            f"Failed to connect to Appium at port {port}: {e}"
        ) from e

    try:
        driver.execute_script("mobile: stopCameraInjection", {})
        _log.info("Camera injection stopped", extra={"udid": udid})
        return True
    except Exception as e:
        raise IFarmError(f"Failed to stop camera injection: {e}") from e
    finally:
        try:
            driver.quit()
        except Exception:
            pass
