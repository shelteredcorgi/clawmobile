"""Config loader for iFarm.

Loads settings from ifarm.toml and an optional devices.json.
All paths are resolved relative to the config file location or $HOME —
never hardcoded.

Search order for ifarm.toml:
  1. Explicit path passed to load_config()
  2. ./ifarm.toml  (current working directory)
  3. ~/.config/ifarm/ifarm.toml

Search order for devices.json:
  1. Explicit path passed to load_config()
  2. ./config/devices.json
  3. ~/.config/ifarm/devices.json
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

_DEFAULT_TOML_PATHS = [
    Path.cwd() / "ifarm.toml",
    Path.home() / ".config" / "ifarm" / "ifarm.toml",
]

_DEFAULT_DEVICES_PATHS = [
    Path.cwd() / "config" / "devices.json",
    Path.home() / ".config" / "ifarm" / "devices.json",
]


class IFarmConfig:
    """Holds merged configuration from ifarm.toml and devices.json.

    Access top-level TOML sections as properties; fall through to .get()
    for anything not explicitly surfaced.
    """

    def __init__(self, data: dict[str, Any], devices: list[dict[str, Any]]):
        self._data = data
        self.devices: list[dict[str, Any]] = devices

    @property
    def vision(self) -> dict[str, Any]:
        """[vision] section — VLM backend selection and model names."""
        return self._data.get("vision", {})

    @property
    def proxy(self) -> dict[str, Any]:
        """[proxy] section — network routing settings."""
        return self._data.get("proxy", {})

    @property
    def sms(self) -> dict[str, Any]:
        """[sms] section — chat.db path overrides and code patterns."""
        return self._data.get("sms", {})

    @property
    def appium(self) -> dict[str, Any]:
        """[appium] section — server host, port, timeouts."""
        return self._data.get("appium", {})

    @property
    def swarm(self) -> dict[str, Any]:
        """[swarm] section — health check intervals, retry limits."""
        return self._data.get("swarm", {})

    def get(self, key: str, default: Any = None) -> Any:
        """Fetch an arbitrary top-level key from the TOML data."""
        return self._data.get(key, default)

    def device_by_udid(self, udid: str) -> dict[str, Any] | None:
        """Return the device record matching the given UDID, or None."""
        return next((d for d in self.devices if d.get("udid") == udid), None)


def load_config(
    config_path: Path | str | None = None,
    devices_path: Path | str | None = None,
) -> IFarmConfig:
    """Load iFarm configuration from disk.

    Args:
        config_path: Explicit path to ifarm.toml. If None, searches default locations.
        devices_path: Explicit path to devices.json. If None, searches default locations.

    Returns:
        Populated IFarmConfig instance. Missing files return empty defaults
        rather than raising, so the library works without any config file.

    Raises:
        FileNotFoundError: Only if an *explicit* path is given and does not exist.
    """
    toml_data = _load_toml(config_path)
    devices = _load_devices(devices_path)
    return IFarmConfig(toml_data, devices)


def _load_toml(path: Path | str | None) -> dict[str, Any]:
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        with p.open("rb") as f:
            return tomllib.load(f)

    for candidate in _DEFAULT_TOML_PATHS:
        if candidate.exists():
            with candidate.open("rb") as f:
                return tomllib.load(f)

    return {}


def _load_devices(path: Path | str | None) -> list[dict[str, Any]]:
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Devices file not found: {p}")
        return json.loads(p.read_text())

    for candidate in _DEFAULT_DEVICES_PATHS:
        if candidate.exists():
            return json.loads(candidate.read_text())

    return []
