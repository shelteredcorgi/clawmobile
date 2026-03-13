"""Visual scraping pipeline — Appium + VLM extraction.

Launches iOS apps via Appium/XCUITest, performs human-simulated navigation,
captures screenshots, and passes them to a VisionBackend for structured
JSON extraction.

Setup:
    xcode-select --install
    npm install -g appium
    appium driver install xcuitest
    pip install ifarm[automation]

Design notes:
  - AppiumSession is a context manager — always use via `with` to guarantee
    driver teardown even on exceptions.
  - Swipe gestures use quadratic bezier curves with random jitter to avoid
    fingerprinting by behavioral anti-bot systems.
  - Screenshots are written to a temp dir and deleted after VLM processing
    to prevent memory accumulation during long scraping runs.
  - System alert interrupts (popups, permission dialogs) are detected before
    each screenshot and dismissed automatically.
"""
from __future__ import annotations

import math
import random
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ifarm.exceptions import VisionError
from ifarm.utils.logger import get_logger

if TYPE_CHECKING:
    from ifarm.vision.base import VisionBackend

_log = get_logger(__name__)

# ---- Appium import (optional dep) ----------------------------------------

try:
    from appium import webdriver as _appium_webdriver
    from appium.options import XCUITestOptions
    _APPIUM_AVAILABLE = True
except ImportError:
    _appium_webdriver = None  # type: ignore[assignment]
    XCUITestOptions = None    # type: ignore[assignment]
    _APPIUM_AVAILABLE = False

try:
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput
    from selenium.webdriver.common.actions import interaction as _interaction
    _SELENIUM_AVAILABLE = True
except ImportError:
    _SELENIUM_AVAILABLE = False

# ---- Default extraction prompt -------------------------------------------

_DEFAULT_FEED_PROMPT = (
    "Analyze this screenshot of a social media or app feed. "
    "For each distinct post or item visible, extract all available fields: "
    "username, content/text, like_count, view_count, comment_count, timestamp, "
    "and any other relevant metadata. "
    "Output ONLY a valid JSON array. Each element is one post. "
    "Use null for fields that are not visible."
)

_LOCATE_ELEMENT_PROMPT = (
    'Locate the UI element with the text "{target_text}". '
    "Return ONLY valid JSON: "
    '{{ "found": true, "x": <center_x_px>, "y": <center_y_px> }} '
    "or {{ \"found\": false }} if not visible. "
    "Coordinates are in pixels from the top-left corner of the screen "
    "(screen size: {width}x{height})."
)


# ===========================================================================
# Gesture helpers
# ===========================================================================


def _bezier_points(
    start: tuple[int, int],
    end: tuple[int, int],
    control: tuple[int, int] | None = None,
    steps: int = 12,
) -> list[tuple[int, int]]:
    """Generate points along a quadratic bezier curve.

    Args:
        start: (x, y) start coordinate.
        end: (x, y) end coordinate.
        control: (x, y) control point. If None, auto-generates a slightly
            offset midpoint for a natural-looking curve.
        steps: Number of intermediate points.

    Returns:
        List of (x, y) integer coordinates from start to end.
    """
    if control is None:
        mid_x = (start[0] + end[0]) // 2 + random.randint(-30, 30)
        mid_y = (start[1] + end[1]) // 2 + random.randint(-20, 20)
        control = (mid_x, mid_y)

    points = []
    for i in range(steps + 1):
        t = i / steps
        x = int((1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t ** 2 * end[0])
        y = int((1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t ** 2 * end[1])
        points.append((x, y))
    return points


def _random_jitter(min_ms: int, max_ms: int) -> float:
    """Return a random delay in seconds within [min_ms, max_ms]."""
    return random.randint(min_ms, max_ms) / 1000.0


# ===========================================================================
# AppiumSession
# ===========================================================================


class AppiumSession:
    """Context manager for an Appium/XCUITest session on a single iOS device.

    Handles Appium server connection, WebDriverAgent provisioning, and
    guaranteed teardown on exit (including on exceptions).

    Args:
        udid: Device UDID.
        port: Appium server port. Default 4723.
        host: Appium server host. Default localhost.
        wda_bundle_id: WebDriverAgent bundle ID.
        new_command_timeout: Seconds before Appium auto-quits idle session.

    Usage:
        with AppiumSession(udid="abc123") as session:
            session.launch_app("com.example.app")
            path = session.take_screenshot()
    """

    def __init__(
        self,
        udid: str,
        port: int = 4723,
        host: str = "localhost",
        wda_bundle_id: str = "com.facebook.WebDriverAgentRunner",
        new_command_timeout: int = 300,
    ):
        self.udid = udid
        self.port = port
        self.host = host
        self.wda_bundle_id = wda_bundle_id
        self.new_command_timeout = new_command_timeout
        self.driver: Any = None
        self._screen_size: tuple[int, int] | None = None

    def __enter__(self) -> "AppiumSession":
        if not _APPIUM_AVAILABLE:
            raise ImportError(
                "Appium-Python-Client is not installed. "
                "Install with: pip install ifarm[automation]"
            )

        options = XCUITestOptions()
        options.udid = self.udid
        options.wda_bundle_id = self.wda_bundle_id
        options.new_command_timeout = self.new_command_timeout
        # Prevent Appium from resetting app state between commands
        options.no_reset = True
        # Don't terminate the app when session ends (leave it open)
        options.should_terminate_app = False

        server_url = f"http://{self.host}:{self.port}"
        _log.info("Starting Appium session", extra={"udid": self.udid, "server": server_url})

        try:
            self.driver = _appium_webdriver.Remote(
                command_executor=server_url,
                options=options,
            )
        except Exception as e:
            raise VisionError(
                f"Failed to start Appium session for {self.udid}: {e}\n"
                "Ensure Appium server is running: appium --port 4723"
            ) from e

        _log.info("Appium session started", extra={"udid": self.udid})
        return self

    def __exit__(self, *_) -> None:
        if self.driver:
            try:
                self.driver.quit()
                _log.info("Appium session closed", extra={"udid": self.udid})
            except Exception as e:
                _log.warning("Error closing Appium session", extra={"error": str(e)})
            finally:
                self.driver = None

    # ------------------------------------------------------------------
    # Device info
    # ------------------------------------------------------------------

    @property
    def screen_size(self) -> tuple[int, int]:
        """Return (width, height) of the device screen in logical pixels."""
        if self._screen_size is None:
            size = self.driver.get_window_size()
            self._screen_size = (size["width"], size["height"])
        return self._screen_size

    # ------------------------------------------------------------------
    # App control
    # ------------------------------------------------------------------

    def launch_app(self, bundle_id: str) -> bool:
        """Launch an iOS app by bundle ID, activating it if already running.

        Args:
            bundle_id: iOS app bundle ID (e.g. "com.zhiliaoapp.musically").

        Returns:
            True on success.
        """
        try:
            self.driver.execute_script("mobile: activateApp", {"bundleId": bundle_id})
            _log.info("App activated", extra={"bundle_id": bundle_id})
            time.sleep(1.5)  # brief settle time after launch
            return True
        except Exception as e:
            raise VisionError(f"Failed to launch {bundle_id}: {e}") from e

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def take_screenshot(self, dest: Path | None = None) -> Path:
        """Capture the current screen to a PNG file.

        Args:
            dest: Destination path. If None, writes to a temp file.

        Returns:
            Path to the saved PNG.
        """
        if dest is None:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".png", prefix="ifarm_", delete=False
            )
            dest = Path(tmp.name)
            tmp.close()

        self.driver.get_screenshot_as_file(str(dest))
        return dest

    # ------------------------------------------------------------------
    # Gestures
    # ------------------------------------------------------------------

    def swipe_feed(
        self,
        n: int = 1,
        direction: str = "up",
        curve: str = "bezier",
        jitter_ms: tuple[int, int] = (50, 200),
    ) -> None:
        """Perform N swipes to scroll a feed.

        Args:
            n: Number of swipes.
            direction: "up" to scroll down (next content), "down" to scroll up.
            curve: "bezier" (default) or "linear".
            jitter_ms: Random inter-swipe delay range (min_ms, max_ms).
        """
        width, height = self.screen_size
        center_x = width // 2

        for i in range(n):
            if direction == "up":
                # Swipe up = finger moves from bottom toward top
                start_y = int(height * 0.75)
                end_y = int(height * 0.25)
            else:
                start_y = int(height * 0.25)
                end_y = int(height * 0.75)

            # Add slight horizontal randomness to look less robotic
            start_x = center_x + random.randint(-20, 20)
            end_x = center_x + random.randint(-20, 20)

            start = (start_x, start_y)
            end = (end_x, end_y)

            if curve == "bezier":
                self._perform_bezier_swipe(start, end)
            else:
                self._perform_linear_swipe(start, end)

            if i < n - 1:
                time.sleep(_random_jitter(*jitter_ms))

    def _perform_bezier_swipe(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> None:
        """Execute a bezier-curve swipe gesture via W3C Actions."""
        if not _SELENIUM_AVAILABLE:
            # Fallback: simple mobile: swipe command
            self.driver.execute_script("mobile: swipe", {
                "direction": "up",
                "velocity": random.randint(800, 1500),
            })
            return

        points = _bezier_points(start, end)
        finger = PointerInput(_interaction.POINTER_TOUCH, "finger")
        actions = ActionBuilder(self.driver, mouse=finger)

        actions.pointer_action.move_to_location(*points[0])
        actions.pointer_action.pointer_down()

        for point in points[1:]:
            actions.pointer_action.move_to_location(*point)
            actions.pointer_action.pause(_random_jitter(5, 20))

        actions.pointer_action.pointer_up()
        actions.perform()

    def _perform_linear_swipe(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> None:
        """Execute a straight-line swipe via mobile: swipe command."""
        self.driver.execute_script("mobile: dragFromToForDuration", {
            "fromX": start[0], "fromY": start[1],
            "toX": end[0], "toY": end[1],
            "duration": random.uniform(0.3, 0.7),
        })

    def tap_at(self, x: int, y: int) -> None:
        """Tap at specific screen coordinates.

        Args:
            x: Horizontal coordinate in logical pixels.
            y: Vertical coordinate in logical pixels.
        """
        self.driver.execute_script("mobile: tap", {"x": x, "y": y})

    # ------------------------------------------------------------------
    # Alert handling
    # ------------------------------------------------------------------

    def dismiss_system_alerts(self) -> bool:
        """Detect and dismiss iOS modal overlays.

        Handles: rate-the-app dialogs, permission requests, low battery,
        OS update prompts, notification permission dialogs.

        Returns:
            True if an alert was found and dismissed.
        """
        try:
            # Try WebDriver alert protocol first
            alert = self.driver.switch_to.alert
            alert.dismiss()
            _log.info("Dismissed WebDriver alert", extra={"udid": self.udid})
            return True
        except Exception:
            pass

        # Try XCUITest mobile: alert command
        try:
            result = self.driver.execute_script("mobile: alert", {"action": "dismiss"})
            if result is not None:
                _log.info("Dismissed XCUITest alert", extra={"udid": self.udid})
                return True
        except Exception:
            pass

        return False


# ===========================================================================
# High-level pipeline functions
# ===========================================================================


def visual_scrape_feed(
    udid: str,
    bundle_id: str,
    swipes: int,
    backend: "VisionBackend",
    extraction_prompt: str | None = None,
    port: int = 4723,
    jitter_ms: tuple[int, int] = (50, 200),
) -> list[dict]:
    """Full pipeline: launch app → swipe → screenshot → VLM → cleanup.

    Screenshots are deleted from disk immediately after the VLM processes
    each one to prevent memory accumulation.

    Args:
        udid: Device UDID.
        bundle_id: iOS app bundle ID.
        swipes: Number of feed swipes to perform.
        backend: VisionBackend instance for extraction.
        extraction_prompt: Custom VLM prompt. Defaults to _DEFAULT_FEED_PROMPT.
        port: Appium server port.
        jitter_ms: Random inter-swipe delay range (min_ms, max_ms).

    Returns:
        List of dicts, one per successful screenshot extraction.
        Entries where the VLM returns a list are flattened into individual items.

    Raises:
        VisionError: If Appium cannot start or the VLM consistently fails.
    """
    prompt = extraction_prompt or _DEFAULT_FEED_PROMPT
    results: list[dict] = []
    screenshot: Path | None = None

    with AppiumSession(udid=udid, port=port) as session:
        session.launch_app(bundle_id)

        for i in range(swipes):
            session.dismiss_system_alerts()

            screenshot = session.take_screenshot()
            try:
                raw = backend.query(screenshot, prompt)
                if isinstance(raw, list):
                    results.extend(raw)
                elif isinstance(raw, dict):
                    results.append(raw)
            except VisionError as e:
                _log.warning(
                    "VLM extraction failed for screenshot",
                    extra={"swipe": i, "error": str(e)},
                )
            finally:
                # Always delete screenshot — never accumulate on disk
                if screenshot and screenshot.exists():
                    screenshot.unlink()
                    screenshot = None

            if i < swipes - 1:
                session.swipe_feed(n=1, jitter_ms=jitter_ms)
                session.dismiss_system_alerts()

    _log.info(
        "Feed scrape complete",
        extra={"bundle_id": bundle_id, "items": len(results)},
    )
    return results


def tap_ui_element_by_text(
    udid: str,
    target_text: str,
    backend: "VisionBackend",
    port: int = 4723,
) -> bool:
    """Find a UI element by visible text using VLM bounding box and tap it.

    Args:
        udid: Device UDID.
        target_text: Visible label of the element to tap.
        backend: VisionBackend to use for coordinate extraction.
        port: Appium server port.

    Returns:
        True if element was found and tapped, False if not found.

    Raises:
        VisionError: If screenshot or VLM call fails.
    """
    screenshot: Path | None = None

    with AppiumSession(udid=udid, port=port) as session:
        session.dismiss_system_alerts()
        screenshot = session.take_screenshot()

        width, height = session.screen_size
        prompt = _LOCATE_ELEMENT_PROMPT.format(
            target_text=target_text,
            width=width,
            height=height,
        )

        try:
            result = backend.query(screenshot, prompt)
        finally:
            if screenshot and screenshot.exists():
                screenshot.unlink()

        if not isinstance(result, dict) or not result.get("found"):
            _log.info(
                "Element not found by VLM",
                extra={"target": target_text},
            )
            return False

        x = result.get("x")
        y = result.get("y")
        if x is None or y is None:
            _log.warning(
                "VLM returned found=true but missing coordinates",
                extra={"result": result},
            )
            return False

        session.tap_at(int(x), int(y))
        _log.info(
            "Tapped element",
            extra={"target": target_text, "x": x, "y": y},
        )
        return True
