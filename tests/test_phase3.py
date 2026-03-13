"""Phase 3 tests — hardware emulation and swarm orchestration.

Unit tests mock subprocess calls and device pools so they run without hardware.
Hardware tests are marked @pytest.mark.hardware.
"""
from __future__ import annotations

import pytest

from ifarm.swarm import DevicePool, DeviceRecord


# ---------------------------------------------------------------------------
# DeviceRecord
# ---------------------------------------------------------------------------


class TestDeviceRecord:
    def test_to_dict(self):
        r = DeviceRecord(udid="ABC", role="scraper", status="healthy")
        d = r.to_dict()
        assert d["udid"] == "ABC"
        assert d["role"] == "scraper"
        assert d["status"] == "healthy"
        assert d["assigned_task"] is None

    def test_extra_fields_in_dict(self):
        r = DeviceRecord(udid="XYZ", extra={"notes": "iPhone 11"})
        d = r.to_dict()
        assert d["notes"] == "iPhone 11"


# ---------------------------------------------------------------------------
# DevicePool
# ---------------------------------------------------------------------------


class TestDevicePool:
    def _pool(self) -> DevicePool:
        return DevicePool([
            DeviceRecord(udid="DEV-001", role="general", status="healthy"),
            DeviceRecord(udid="DEV-002", role="scraper", status="healthy"),
            DeviceRecord(udid="DEV-003", role="general", status="blocked"),
        ])

    def test_udids(self):
        pool = self._pool()
        assert set(pool.udids) == {"DEV-001", "DEV-002", "DEV-003"}

    def test_healthy_filters_blocked(self):
        pool = self._pool()
        healthy = pool.healthy
        assert len(healthy) == 2
        assert all(d.status == "healthy" for d in healthy)

    def test_get_by_udid(self):
        pool = self._pool()
        record = pool.get("DEV-002")
        assert record is not None
        assert record.role == "scraper"

    def test_get_missing_udid(self):
        pool = self._pool()
        assert pool.get("NONEXISTENT") is None

    def test_update_status(self):
        pool = self._pool()
        pool.update_status("DEV-001", "blocked")
        assert pool.get("DEV-001").status == "blocked"

    def test_discover_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            DevicePool.discover()

    def test_from_config_raises_not_implemented(self, tmp_path):
        with pytest.raises(NotImplementedError):
            DevicePool.from_config(tmp_path / "devices.json")


# ---------------------------------------------------------------------------
# IFarmSwarmController stubs
# ---------------------------------------------------------------------------


class TestSwarmControllerStubs:
    def _swarm(self):
        from ifarm.swarm import IFarmSwarmController
        pool = DevicePool([
            DeviceRecord(udid="DEV-001", status="healthy"),
            DeviceRecord(udid="DEV-002", status="healthy"),
        ])
        return IFarmSwarmController(pool)

    def test_distribute_tasks_raises_not_implemented(self):
        swarm = self._swarm()
        with pytest.raises(NotImplementedError):
            swarm.distribute_tasks(["task1", "task2", "task3"])

    def test_get_swarm_status_raises_not_implemented(self):
        swarm = self._swarm()
        with pytest.raises(NotImplementedError):
            swarm.get_swarm_status()


# ---------------------------------------------------------------------------
# Hardware module stubs
# ---------------------------------------------------------------------------


class TestHardwareStubs:
    def test_spoof_gps_raises_not_implemented(self, mock_udid):
        from ifarm.modules.hardware import spoof_gps
        with pytest.raises(NotImplementedError):
            spoof_gps(mock_udid, 32.7767, -96.7970)

    def test_inject_camera_frame_raises_not_implemented(self, mock_udid, tmp_path):
        from ifarm.modules.hardware import inject_camera_frame
        with pytest.raises(NotImplementedError):
            inject_camera_frame(mock_udid, tmp_path / "image.png")


# ---------------------------------------------------------------------------
# Hardware integration stubs
# ---------------------------------------------------------------------------


@pytest.mark.hardware
class TestPhase3Hardware:
    def test_gps_injection(self):
        pytest.skip("Requires device + idevicelocation — run manually")

    def test_swarm_task_distribution(self):
        pytest.skip("Requires USB hub with multiple devices — run manually")

    def test_health_monitor_failover(self):
        pytest.skip("Requires multiple devices — run manually")
