"""Shared pytest fixtures and configuration.

Hardware-dependent tests are marked @pytest.mark.hardware and skipped
in CI. VLM-dependent tests are marked @pytest.mark.vlm.

Run offline unit tests only:
    pytest -m "not hardware and not vlm"
"""
from __future__ import annotations

import pytest

from ifarm.utils.config import IFarmConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_config() -> IFarmConfig:
    """An IFarmConfig with no data — safe default for unit tests."""
    return IFarmConfig(data={}, devices=[])


@pytest.fixture
def sample_config() -> IFarmConfig:
    """A minimal IFarmConfig with typical values for unit tests."""
    return IFarmConfig(
        data={
            "vision": {"backend": "ollama", "model": "qwen2-vl"},
            "proxy": {"airplane_mode_wait": 8, "ip_probe_url": "https://api.ipify.org"},
            "sms": {"default_window_seconds": 60},
            "appium": {"host": "localhost", "port": 4723},
        },
        devices=[
            {"udid": "TEST-UDID-0001", "role": "general"},
            {"udid": "TEST-UDID-0002", "role": "tiktok_scraper"},
        ],
    )


@pytest.fixture
def mock_udid() -> str:
    return "TEST-UDID-0001"


@pytest.fixture
def sample_messages() -> list[dict]:
    """Fake SMS messages for testing code extraction logic."""
    return [
        {"id": 1, "text": "Your verification code is 847291", "date": 0, "sender": "+15550001111"},
        {"id": 2, "text": "Use code 123456 to verify your account.", "date": 0, "sender": "+15550002222"},
        {"id": 3, "text": "No code here, just a normal message.", "date": 0, "sender": "+15550003333"},
        {"id": 4, "text": "Your OTP is AB1234", "date": 0, "sender": "+15550004444"},
    ]
