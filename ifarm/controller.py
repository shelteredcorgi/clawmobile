"""IFarmController — single-device coordinator.

Thin facade that delegates to the capability modules. Callers (agents,
scripts, tests) interact only with this class and never import modules
directly unless they need lower-level access.

Methods are grouped by capability:
    Network    — establish_cellular_route, cycle_airplane_mode, fetch_recent_2fa
    Automation — visual_scrape_feed, tap_ui_element_by_text
    Hardware   — spoof_gps, inject_camera_frame
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ifarm.exceptions import IFarmError, ProxyError, SMSError, CapabilityNotAvailable
from ifarm.utils.config import IFarmConfig, load_config
from ifarm.utils.logger import get_logger

if TYPE_CHECKING:
    from ifarm.vision.base import VisionBackend


class IFarmController:
    """Orchestrates a single USB-tethered iOS device.

    Agent-agnostic — works with OpenClaw, any other agent, or plain scripts.

    Args:
        udid: The UDID of the target iOS device.
        config_path: Optional path to ifarm.toml. Falls back to default
            search locations if not provided.

    Example:
        farm = IFarmController(udid="00008101-000000000000001E")
        farm.establish_cellular_route()
        code = farm.fetch_recent_2fa()
    """

    def __init__(self, udid: str, config_path: Path | str | None = None):
        self.udid = udid
        self.config: IFarmConfig = load_config(config_path)
        self.log = get_logger(__name__, device_udid=udid)

    # ------------------------------------------------------------------
    # Network & SMS
    # ------------------------------------------------------------------

    def establish_cellular_route(self) -> bool:
        """Force macOS to route traffic through the USB-tethered iPhone.

        Detects the device's USB network interface and elevates it in
        the macOS service priority list via networksetup.

        Returns:
            True on success.

        Raises:
            ProxyError: If the USB interface cannot be detected or routing fails.
        """
        from ifarm.modules.proxy import detect_usb_interface, establish_cellular_route
        interface = detect_usb_interface(self.udid)
        return establish_cellular_route(interface)

    def cycle_airplane_mode(self) -> str:
        """Rotate the cellular IP by bouncing the USB hotspot interface.

        Current strategy: brings the iPhone's USB network interface down
        on the Mac side, waits, then brings it back up. For a true airplane
        mode toggle, set proxy.rotation_strategy = "appium" in ifarm.toml
        when Appium is available.

        Returns:
            New public IP address string.

        Raises:
            ProxyError: If the interface bounce or IP probe fails.
        """
        from ifarm.modules.proxy import cycle_airplane_mode
        wait = self.config.proxy.get("airplane_mode_wait", 8)
        return cycle_airplane_mode(self.udid, wait_seconds=wait)

    def fetch_recent_2fa(
        self,
        keyword: str = "code",
        since_seconds: int = 60,
    ) -> str | None:
        """Return the most recent 2FA/OTP code from synced SMS messages.

        Args:
            keyword: Filter messages containing this word. Empty string skips filter.
            since_seconds: Only consider messages received in this window.

        Returns:
            Extracted code string, or None if not found.

        Raises:
            SMSError: If chat.db cannot be accessed or parsed.
        """
        from ifarm.modules.sms import fetch_recent_2fa
        db_path = self.config.sms.get("db_path")
        window = self.config.sms.get("default_window_seconds", since_seconds)
        return fetch_recent_2fa(
            keyword=keyword,
            since_seconds=window,
            db_path=db_path,
        )

    # ------------------------------------------------------------------
    # Automation — Visual Scraping
    # ------------------------------------------------------------------

    def visual_scrape_feed(
        self,
        bundle_id: str,
        swipes: int,
        backend: "VisionBackend | None" = None,
    ) -> list[dict]:
        """Launch an iOS app, scroll, and return VLM-extracted structured data.

        Args:
            bundle_id: iOS app bundle ID (e.g. "com.zhiliaoapp.musically").
            swipes: Number of feed swipes to perform.
            backend: VisionBackend to use. If None, selects from config.

        Returns:
            List of dicts with VLM-extracted fields per screenshot.

        Raises:
            NotImplementedError: Requires ifarm[automation] and Appium.
        """
        from ifarm.modules.scraper import visual_scrape_feed
        from ifarm.vision import get_backend
        b = backend or get_backend(self.config)
        return visual_scrape_feed(self.udid, bundle_id, swipes, b)

    def tap_ui_element_by_text(
        self,
        target_text: str,
        backend: "VisionBackend | None" = None,
    ) -> bool:
        """Tap a UI element identified by visible text using VLM bounding box.

        Args:
            target_text: Visible label of the element to tap.
            backend: VisionBackend to use. If None, selects from config.

        Returns:
            True if the element was found and tapped.

        Raises:
            NotImplementedError: Requires ifarm[automation] and Appium.
        """
        from ifarm.modules.scraper import tap_ui_element_by_text
        from ifarm.vision import get_backend
        b = backend or get_backend(self.config)
        return tap_ui_element_by_text(self.udid, target_text, b)

    # ------------------------------------------------------------------
    # Hardware Emulation
    # ------------------------------------------------------------------

    def spoof_gps(self, lat: float, lon: float) -> bool:
        """Inject GPS coordinates into the device's CoreLocation daemon.

        Args:
            lat: Target latitude.
            lon: Target longitude.

        Returns:
            True on success.

        Raises:
            CapabilityNotAvailable: If idevicelocation is not installed.
            NotImplementedError: Requires ifarm[hardware] and idevicelocation.
        """
        from ifarm.modules.hardware import spoof_gps
        return spoof_gps(self.udid, lat, lon)

    def inject_camera_frame(self, image_path: Path | str) -> bool:
        """Inject a static image into the device's camera buffer.

        Args:
            image_path: Path to the image file to inject.

        Returns:
            True on success.

        Raises:
            CapabilityNotAvailable: If camera injection is not supported.
            NotImplementedError: Requires ifarm[hardware] and idevicelocation.
        """
        from ifarm.modules.hardware import inject_camera_frame
        return inject_camera_frame(self.udid, image_path)
