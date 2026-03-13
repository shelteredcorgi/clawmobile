"""iFarm exception hierarchy.

All public exceptions inherit from IFarmError so callers can catch broadly
or narrowly depending on their needs.
"""


class IFarmError(Exception):
    """Base exception for all iFarm errors."""


class DeviceNotFoundError(IFarmError):
    """Raised when a device with the given UDID is not connected."""


class CapabilityNotAvailable(IFarmError):
    """Raised when a hardware capability is not supported on this configuration.

    Example: camera injection requires app instrumentation that may not be
    present on every device/OS combination.
    """


class VisionError(IFarmError):
    """Raised when a VLM or OCR backend fails to process an image."""


class ProxyError(IFarmError):
    """Raised when cellular routing or IP rotation fails."""


class SMSError(IFarmError):
    """Raised when the SMS/2FA interceptor cannot access or parse chat.db."""
