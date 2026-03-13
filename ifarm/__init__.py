"""iFarm — open-source iOS device orchestration suite for macOS.

Quick start:
    from ifarm import IFarmController

    farm = IFarmController(udid="<your-device-udid>")

    # Phase 1
    farm.establish_cellular_route()
    new_ip = farm.cycle_airplane_mode()
    code = farm.fetch_recent_2fa()

    # Phase 2
    results = farm.visual_scrape_feed("com.zhiliaoapp.musically", swipes=10)

    # Phase 3
    farm.spoof_gps(lat=32.7767, lon=-96.7970)
"""
from ifarm.controller import IFarmController
from ifarm.swarm import IFarmSwarmController, DevicePool
from ifarm.exceptions import (
    IFarmError,
    DeviceNotFoundError,
    CapabilityNotAvailable,
    VisionError,
    ProxyError,
    SMSError,
)

__version__ = "0.1.0"

__all__ = [
    "IFarmController",
    "IFarmSwarmController",
    "DevicePool",
    # Exceptions
    "IFarmError",
    "DeviceNotFoundError",
    "CapabilityNotAvailable",
    "VisionError",
    "ProxyError",
    "SMSError",
]
