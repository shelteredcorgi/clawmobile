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

    def get_current_ip(self) -> str:
        """Return the current public IP address of the device's cellular link.

        Used by the swarm health monitor as a lightweight device probe.

        Returns:
            Public IP address string.

        Raises:
            ProxyError: If the IP probe fails or requests is not installed.
        """
        from ifarm.modules.proxy import get_current_ip
        probe_url = self.config.proxy.get("ip_probe_url", "https://api.ipify.org")
        return get_current_ip(probe_url=probe_url)

    # ------------------------------------------------------------------
    # Hardware Emulation
    # ------------------------------------------------------------------

    def spoof_gps(self, lat: float, lon: float) -> bool:
        """Inject GPS coordinates into the device's CoreLocation daemon.

        Args:
            lat: Target latitude (-90.0 to 90.0).
            lon: Target longitude (-180.0 to 180.0).

        Returns:
            True on success.

        Raises:
            CapabilityNotAvailable: If idevicelocation is not installed.
            ValueError: If lat/lon are out of range.
            IFarmError: If the command fails.
        """
        from ifarm.modules.hardware import spoof_gps
        return spoof_gps(self.udid, lat, lon)

    def spoof_gps_preset(self, preset_name: str) -> bool:
        """Spoof GPS using a named location preset from ifarm.toml [locations].

        Args:
            preset_name: Key under [locations] in ifarm.toml
                (e.g. "dallas", "miami", "tokyo").

        Returns:
            True on success.

        Raises:
            KeyError: If preset_name is not in the config.
            CapabilityNotAvailable: If idevicelocation is not installed.
        """
        from ifarm.modules.hardware import spoof_gps_preset
        return spoof_gps_preset(self.udid, preset_name, self.config.locations)

    def clear_gps_spoof(self) -> bool:
        """Remove GPS spoof and restore the device's real location.

        Returns:
            True on success.

        Raises:
            CapabilityNotAvailable: If idevicelocation is not installed.
            IFarmError: If the command fails.
        """
        from ifarm.modules.hardware import clear_gps_spoof
        return clear_gps_spoof(self.udid)

    def inject_camera_frame(self, image_path: Path | str, bundle_id: str) -> bool:
        """Inject a static image into the device's camera feed via Appium.

        Args:
            image_path: Path to the PNG/JPEG image to inject.
            bundle_id: Bundle ID of the app that will access the camera.

        Returns:
            True on success.

        Raises:
            CapabilityNotAvailable: If Appium is not installed.
            FileNotFoundError: If image_path does not exist.
            IFarmError: If injection fails.
        """
        from ifarm.modules.hardware import inject_camera_frame
        appium_port = self.config.appium.get("port", 4723)
        return inject_camera_frame(self.udid, image_path, bundle_id, port=appium_port)

    def inject_camera_video(self, video_path: Path | str, bundle_id: str) -> bool:
        """Inject a looping video into the device's camera feed via Appium.

        Args:
            video_path: Path to the MP4/MOV file to inject.
            bundle_id: Bundle ID of the app that will access the camera.

        Returns:
            True on success.

        Raises:
            CapabilityNotAvailable: If Appium is not installed.
            FileNotFoundError: If video_path does not exist.
            IFarmError: If injection fails.
        """
        from ifarm.modules.hardware import inject_camera_video
        appium_port = self.config.appium.get("port", 4723)
        return inject_camera_video(self.udid, video_path, bundle_id, port=appium_port)

    def stop_camera_injection(self, bundle_id: str) -> bool:
        """Stop an active camera injection and restore the live feed.

        Args:
            bundle_id: Bundle ID of the app (must match injection session).

        Returns:
            True on success.

        Raises:
            CapabilityNotAvailable: If Appium is not installed.
            IFarmError: If the stop command fails.
        """
        from ifarm.modules.hardware import stop_camera_injection
        appium_port = self.config.appium.get("port", 4723)
        return stop_camera_injection(self.udid, bundle_id, port=appium_port)
