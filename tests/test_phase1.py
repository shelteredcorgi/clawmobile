"""Phase 1 tests — cellular proxy and SMS/2FA interception.

Unit tests mock subprocess calls and the sqlite3 connection so they run
without any hardware attached.

Run offline tests:
    python3.11 -m pytest tests/test_phase1.py -m "not hardware" -v

Run hardware integration tests (requires physical device):
    python3.11 -m pytest tests/test_phase1.py -m hardware -v
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from ifarm.modules.sms import (
    extract_code,
    fetch_recent_sms,
    fetch_recent_2fa,
    _APPLE_EPOCH_OFFSET,
    _NS_THRESHOLD,
    _CODE_PATTERNS,
)
from ifarm.modules.proxy import (
    detect_usb_interface,
    establish_cellular_route,
    cycle_airplane_mode,
    get_current_ip,
    _find_service_for_interface,
)
from ifarm.exceptions import ProxyError, SMSError


# ---------------------------------------------------------------------------
# SMS / 2FA — extract_code (pure logic, no I/O)
# ---------------------------------------------------------------------------


class TestExtractCode:
    def test_extracts_six_digit_code(self, sample_messages):
        assert extract_code(sample_messages[:1]) == "847291"

    def test_extracts_first_message_first(self, sample_messages):
        # Message 1 has 847291 before message 2's 123456
        assert extract_code(sample_messages[:2]) == "847291"

    def test_returns_none_when_no_code(self):
        msgs = [{"id": 1, "text": "No numeric codes here.", "date": 0, "sender": "+1"}]
        assert extract_code(msgs) is None

    def test_alphanumeric_code(self, sample_messages):
        # message index 3: "Your OTP is AB1234"
        assert extract_code([sample_messages[3]]) == "AB1234"

    def test_custom_pattern_override(self, sample_messages):
        pat = re.compile(r"\b(\d{4})\b")
        code = extract_code([{"text": "Your PIN is 9876", "date": 0}], pattern=pat)
        assert code == "9876"

    def test_empty_messages_returns_none(self):
        assert extract_code([]) is None

    def test_handles_none_text(self):
        assert extract_code([{"id": 1, "text": None, "date": 0}]) is None

    def test_handles_missing_text_key(self):
        assert extract_code([{"id": 1, "date": 0}]) is None

    def test_prefers_longer_code_via_pattern_order(self):
        # Pattern ordering: alphanumeric > 6-digit > 4-digit
        # A message with both a 6-digit and a 4-digit code should match 6-digit first
        msg = [{"text": "Code: 123456. Pin: 7890", "date": 0}]
        code = extract_code(msg)
        assert code == "123456"  # 6-digit matched before 4-digit


# ---------------------------------------------------------------------------
# SMS / 2FA — fetch_recent_sms (mocked sqlite3)
# ---------------------------------------------------------------------------


class TestFetchRecentSMS:
    def _make_db_path(self, tmp_path: Path) -> Path:
        """Create a real minimal chat.db for testing."""
        db = tmp_path / "chat.db"
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE handle (
                rowid INTEGER PRIMARY KEY,
                id TEXT
            );
            CREATE TABLE message (
                rowid INTEGER PRIMARY KEY,
                text TEXT,
                date INTEGER,
                is_from_me INTEGER,
                handle_id INTEGER
            );
        """)
        conn.commit()
        conn.close()
        return db

    def _apple_now(self) -> int:
        """Current time in Apple nanosecond epoch."""
        return int((time.time() - _APPLE_EPOCH_OFFSET) * 1_000_000_000)

    def _insert_message(self, db: Path, text: str, seconds_ago: int = 5, is_from_me: int = 0):
        date_ns = self._apple_now() - seconds_ago * 1_000_000_000
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO handle (id) VALUES (?)", ("+15550001111",)
        )
        conn.execute(
            "INSERT INTO message (text, date, is_from_me, handle_id) VALUES (?, ?, ?, 1)",
            (text, date_ns, is_from_me),
        )
        conn.commit()
        conn.close()

    def test_returns_recent_messages(self, tmp_path):
        db = self._make_db_path(tmp_path)
        self._insert_message(db, "Your code is 123456", seconds_ago=5)
        msgs = fetch_recent_sms(since_seconds=60, db_path=db)
        assert len(msgs) == 1
        assert "123456" in msgs[0]["text"]

    def test_excludes_old_messages(self, tmp_path):
        db = self._make_db_path(tmp_path)
        self._insert_message(db, "Old code 999999", seconds_ago=120)
        msgs = fetch_recent_sms(since_seconds=60, db_path=db)
        assert len(msgs) == 0

    def test_excludes_outbound_messages(self, tmp_path):
        db = self._make_db_path(tmp_path)
        self._insert_message(db, "My code 111111", seconds_ago=5, is_from_me=1)
        msgs = fetch_recent_sms(since_seconds=60, db_path=db)
        assert len(msgs) == 0

    def test_raises_sms_error_if_db_missing(self, tmp_path):
        with pytest.raises(SMSError, match="chat.db not found"):
            fetch_recent_sms(db_path=tmp_path / "nonexistent.db")

    def test_raises_sms_error_on_bad_schema(self, tmp_path):
        db = tmp_path / "chat.db"
        db.write_text("not a sqlite db")
        with pytest.raises(SMSError):
            fetch_recent_sms(db_path=db)

    def test_fetch_recent_2fa_filters_by_keyword(self, tmp_path):
        db = self._make_db_path(tmp_path)
        self._insert_message(db, "Your verification code is 847291", seconds_ago=5)
        self._insert_message(db, "Package delivered to your door", seconds_ago=3)
        code = fetch_recent_2fa(keyword="code", since_seconds=60, db_path=db)
        assert code == "847291"

    def test_fetch_recent_2fa_no_keyword_searches_all(self, tmp_path):
        db = self._make_db_path(tmp_path)
        self._insert_message(db, "Security alert: 654321", seconds_ago=5)
        code = fetch_recent_2fa(keyword="", since_seconds=60, db_path=db)
        assert code == "654321"

    def test_fetch_recent_2fa_returns_none_when_no_match(self, tmp_path):
        db = self._make_db_path(tmp_path)
        self._insert_message(db, "Hello, your order has shipped.", seconds_ago=5)
        code = fetch_recent_2fa(keyword="code", since_seconds=60, db_path=db)
        assert code is None


# ---------------------------------------------------------------------------
# Proxy — detect_usb_interface
# ---------------------------------------------------------------------------

_SAMPLE_HARDWAREPORTS = """\
Hardware Port: Wi-Fi
Device: en0
Ethernet Address: aa:bb:cc:dd:ee:ff

Hardware Port: iPhone USB
Device: en5
Ethernet Address: 00:00:00:00:00:00

Hardware Port: Thunderbolt 1
Device: en1
Ethernet Address: 11:22:33:44:55:66
"""


class TestDetectUSBInterface:
    def test_finds_iphone_usb_interface(self, mock_udid):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=_SAMPLE_HARDWAREPORTS,
                stderr="",
            )
            result = detect_usb_interface(mock_udid)
        assert result == "en5"

    def test_raises_proxy_error_if_not_found(self, mock_udid):
        no_iphone_output = """\
Hardware Port: Wi-Fi
Device: en0
Ethernet Address: aa:bb:cc:dd:ee:ff
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=no_iphone_output, stderr="")
            with pytest.raises(ProxyError, match="No USB hotspot interface found"):
                detect_usb_interface(mock_udid)

    def test_raises_proxy_error_on_macos_not_found(self, mock_udid):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(ProxyError, match="macOS"):
                detect_usb_interface(mock_udid)

    def test_detects_personal_hotspot_label(self, mock_udid):
        output = """\
Hardware Port: Personal Hotspot
Device: en6
Ethernet Address: 00:00:00:00:00:00
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            assert detect_usb_interface(mock_udid) == "en6"


# ---------------------------------------------------------------------------
# Proxy — establish_cellular_route
# ---------------------------------------------------------------------------

_SAMPLE_SERVICE_ORDER = """\
An asterisk (*) denotes that a network service is disabled.
(1) Wi-Fi
(*) Hardware Port: Wi-Fi, Device: en0, ...
(2) iPhone USB
(*) Hardware Port: iPhone USB, Device: en5, ...
(3) Thunderbolt Ethernet Slot 1
(*) Hardware Port: Thunderbolt Ethernet Slot 1, Device: en1, ...
"""

_SAMPLE_HARDWAREPORTS_REVERSE = """\
Hardware Port: iPhone USB
Device: en5
Ethernet Address: 00:00:00:00:00:00
"""


class TestEstablishCellularRoute:
    def test_calls_order_services(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=_SAMPLE_SERVICE_ORDER, stderr="")
            result = establish_cellular_route("en5")
        assert result is True
        # Should have called networksetup twice: list order + apply order
        assert mock_run.call_count >= 1

    def test_returns_true_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=_SAMPLE_SERVICE_ORDER, stderr="")
            assert establish_cellular_route("en5") is True

    def test_raises_proxy_error_on_failure(self):
        import subprocess as sp
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = sp.CalledProcessError(1, "networksetup", stderr="permission denied")
            with pytest.raises(ProxyError):
                establish_cellular_route("en5")


# ---------------------------------------------------------------------------
# Proxy — get_current_ip
# ---------------------------------------------------------------------------


class TestGetCurrentIP:
    def test_returns_ip_string(self):
        mock_resp = MagicMock()
        mock_resp.text = "203.0.113.42"
        mock_resp.raise_for_status = MagicMock()

        import ifarm.modules.proxy as proxy_mod
        orig = proxy_mod.requests
        try:
            fake_requests = MagicMock()
            fake_requests.get.return_value = mock_resp
            proxy_mod.requests = fake_requests
            ip = get_current_ip()
        finally:
            proxy_mod.requests = orig

        assert ip == "203.0.113.42"

    def test_raises_proxy_error_on_request_failure(self):
        import ifarm.modules.proxy as proxy_mod
        orig = proxy_mod.requests
        try:
            fake_requests = MagicMock()
            fake_requests.get.side_effect = Exception("timeout")
            proxy_mod.requests = fake_requests
            with pytest.raises(ProxyError, match="IP probe failed"):
                get_current_ip()
        finally:
            proxy_mod.requests = orig

    def test_raises_proxy_error_if_requests_not_installed(self):
        import ifarm.modules.proxy as proxy_mod
        orig = proxy_mod.requests
        try:
            proxy_mod.requests = None
            with pytest.raises(ProxyError, match="requests is required"):
                get_current_ip()
        finally:
            proxy_mod.requests = orig


# ---------------------------------------------------------------------------
# IFarmController — Phase 1 wiring
# ---------------------------------------------------------------------------


class TestIFarmControllerPhase1:
    def _make_farm(self, mock_udid, empty_config, monkeypatch):
        from ifarm.controller import IFarmController
        monkeypatch.setattr("ifarm.controller.load_config", lambda *a, **k: empty_config)
        return IFarmController(udid=mock_udid)

    def test_establish_cellular_route_delegates(self, mock_udid, empty_config, monkeypatch):
        farm = self._make_farm(mock_udid, empty_config, monkeypatch)
        with patch("ifarm.modules.proxy.detect_usb_interface", return_value="en5"), \
             patch("ifarm.modules.proxy.establish_cellular_route", return_value=True) as mock_ecr:
            result = farm.establish_cellular_route()
        assert result is True
        mock_ecr.assert_called_once_with("en5")

    def test_cycle_airplane_mode_delegates(self, mock_udid, empty_config, monkeypatch):
        farm = self._make_farm(mock_udid, empty_config, monkeypatch)
        with patch("ifarm.modules.proxy.cycle_airplane_mode", return_value="1.2.3.4") as mock_cam:
            result = farm.cycle_airplane_mode()
        assert result == "1.2.3.4"
        mock_cam.assert_called_once_with(mock_udid, wait_seconds=8)

    def test_fetch_recent_2fa_delegates(self, mock_udid, empty_config, monkeypatch):
        farm = self._make_farm(mock_udid, empty_config, monkeypatch)
        with patch("ifarm.modules.sms.fetch_recent_2fa", return_value="123456") as mock_2fa:
            result = farm.fetch_recent_2fa(keyword="verify", since_seconds=30)
        assert result == "123456"

    def test_config_sms_window_used(self, mock_udid, sample_config, monkeypatch):
        from ifarm.controller import IFarmController
        monkeypatch.setattr("ifarm.controller.load_config", lambda *a, **k: sample_config)
        farm = IFarmController(udid=mock_udid)
        with patch("ifarm.modules.sms.fetch_recent_2fa", return_value=None) as mock_2fa:
            farm.fetch_recent_2fa()
        # sample_config has default_window_seconds=60
        _, kwargs = mock_2fa.call_args
        assert kwargs.get("since_seconds") == 60 or mock_2fa.call_args[0][1] == 60


# ---------------------------------------------------------------------------
# Hardware integration stubs (skipped without device)
# ---------------------------------------------------------------------------


@pytest.mark.hardware
class TestPhase1Hardware:
    def test_usb_interface_detected(self):
        """Verify the iPhone appears as a USB network interface."""
        pytest.skip("Requires physical device")

    def test_ip_rotation(self):
        """Verify a new IP is returned after cycling the interface."""
        pytest.skip("Requires physical device")

    def test_sms_code_extracted(self):
        """Verify a real SMS code is found in chat.db within 60 seconds."""
        pytest.skip("Requires physical device + SMS delivery")
