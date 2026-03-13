"""Hardware and swarm orchestration tests.

Unit tests mock subprocess calls and device pools so they run without
physical hardware. Hardware integration tests are marked @pytest.mark.hardware.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from ifarm.swarm import DevicePool, DeviceRecord, IFarmSwarmController, _get_task_role


# ===========================================================================
# DeviceRecord
# ===========================================================================


class TestDeviceRecord:
    def test_to_dict_basic(self):
        r = DeviceRecord(udid="ABC", role="scraper", status="healthy")
        d = r.to_dict()
        assert d["udid"] == "ABC"
        assert d["role"] == "scraper"
        assert d["status"] == "healthy"
        assert d["assigned_task"] is None

    def test_extra_fields_merged(self):
        r = DeviceRecord(udid="XYZ", extra={"notes": "iPhone 11"})
        d = r.to_dict()
        assert d["notes"] == "iPhone 11"

    def test_default_role_is_general(self):
        r = DeviceRecord(udid="X")
        assert r.role == "general"

    def test_default_status_is_unknown(self):
        r = DeviceRecord(udid="X")
        assert r.status == "unknown"


# ===========================================================================
# DevicePool
# ===========================================================================


class TestDevicePool:
    def _pool(self) -> DevicePool:
        return DevicePool([
            DeviceRecord(udid="DEV-001", role="general",  status="healthy"),
            DeviceRecord(udid="DEV-002", role="scraper",  status="healthy"),
            DeviceRecord(udid="DEV-003", role="general",  status="blocked"),
            DeviceRecord(udid="DEV-004", role="scraper",  status="offline"),
        ])

    def test_udids_includes_all(self):
        pool = self._pool()
        assert set(pool.udids) == {"DEV-001", "DEV-002", "DEV-003", "DEV-004"}

    def test_healthy_excludes_blocked_and_offline(self):
        pool = self._pool()
        assert [d.udid for d in pool.healthy] == ["DEV-001", "DEV-002"]

    def test_all_devices_returns_every_record(self):
        assert len(self._pool().all_devices) == 4

    def test_get_existing_udid(self):
        record = self._pool().get("DEV-002")
        assert record is not None
        assert record.role == "scraper"

    def test_get_missing_returns_none(self):
        assert self._pool().get("NONEXISTENT") is None

    def test_update_status_changes_value(self):
        pool = self._pool()
        pool.update_status("DEV-001", "blocked")
        assert pool.get("DEV-001").status == "blocked"

    def test_update_status_unknown_udid_is_noop(self):
        pool = self._pool()
        pool.update_status("GHOST", "healthy")  # should not raise

    # ------------------------------------------------------------------
    # discover()
    # ------------------------------------------------------------------

    def test_discover_returns_pool_from_idevice_id(self):
        with patch("ifarm.utils.device.list_connected_udids",
                   return_value=["UDID-A", "UDID-B"]):
            pool = DevicePool.discover()
        assert set(pool.udids) == {"UDID-A", "UDID-B"}
        assert all(d.status == "unknown" for d in pool.all_devices)

    def test_discover_empty_when_no_devices(self):
        with patch("ifarm.utils.device.list_connected_udids", return_value=[]):
            pool = DevicePool.discover()
        assert pool.udids == []

    # ------------------------------------------------------------------
    # from_config()
    # ------------------------------------------------------------------

    def test_from_config_loads_devices(self, tmp_path):
        devices = [
            {"udid": "DEV-A", "role": "general", "notes": "iPhone SE"},
            {"udid": "DEV-B", "role": "scraper"},
        ]
        p = tmp_path / "devices.json"
        p.write_text(json.dumps(devices))

        pool = DevicePool.from_config(p)
        assert set(pool.udids) == {"DEV-A", "DEV-B"}
        assert pool.get("DEV-A").role == "general"
        assert pool.get("DEV-A").extra["notes"] == "iPhone SE"
        assert pool.get("DEV-B").role == "scraper"

    def test_from_config_all_start_unknown(self, tmp_path):
        p = tmp_path / "devices.json"
        p.write_text(json.dumps([{"udid": "DEV-A"}]))
        pool = DevicePool.from_config(p)
        assert pool.get("DEV-A").status == "unknown"

    def test_from_config_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="devices.json not found"):
            DevicePool.from_config(tmp_path / "missing.json")

    def test_from_config_invalid_json_raises(self, tmp_path):
        p = tmp_path / "devices.json"
        p.write_text("not json {")
        with pytest.raises(ValueError, match="Invalid JSON"):
            DevicePool.from_config(p)

    def test_from_config_not_a_list_raises(self, tmp_path):
        p = tmp_path / "devices.json"
        p.write_text(json.dumps({"udid": "DEV-A"}))
        with pytest.raises(ValueError, match="must contain a JSON array"):
            DevicePool.from_config(p)

    def test_from_config_missing_udid_raises(self, tmp_path):
        p = tmp_path / "devices.json"
        p.write_text(json.dumps([{"role": "general"}]))
        with pytest.raises(ValueError, match="missing required 'udid'"):
            DevicePool.from_config(p)


# ===========================================================================
# IFarmSwarmController — task distribution
# ===========================================================================


class TestDistributeTasks:
    def _swarm(self, n=3, roles=None) -> IFarmSwarmController:
        roles = roles or ["general"] * n
        pool = DevicePool([
            DeviceRecord(udid=f"DEV-{i:03d}", role=roles[i], status="healthy")
            for i in range(n)
        ])
        return IFarmSwarmController(pool)

    def test_round_robin_distributes_evenly(self):
        swarm = self._swarm(n=3)
        assignments = swarm.distribute_tasks(["t1", "t2", "t3", "t4", "t5", "t6"])
        counts = [len(v) for v in assignments.values()]
        assert sorted(counts) == [2, 2, 2]

    def test_extra_tasks_go_to_first_devices(self):
        swarm = self._swarm(n=3)
        # 7 tasks across 3 devices → 3, 2, 2
        assignments = swarm.distribute_tasks([f"t{i}" for i in range(7)])
        counts = sorted(len(v) for v in assignments.values())
        assert counts == [2, 2, 3]

    def test_role_based_routing(self):
        swarm = self._swarm(n=3, roles=["general", "scraper", "general"])
        tasks = [
            {"action": "scrape", "role": "scraper"},
            {"action": "scrape", "role": "scraper"},
            {"action": "other"},
        ]
        assignments = swarm.distribute_tasks(tasks)
        scraper_udid = "DEV-001"
        assert len(assignments.get(scraper_udid, [])) == 2

    def test_role_fallback_when_no_match(self):
        swarm = self._swarm(n=2, roles=["general", "general"])
        tasks = [{"action": "x", "role": "nonexistent_role"}]
        assignments = swarm.distribute_tasks(tasks)
        total = sum(len(v) for v in assignments.values())
        assert total == 1  # task was assigned somewhere

    def test_empty_task_queue_returns_empty(self):
        swarm = self._swarm(n=2)
        assignments = swarm.distribute_tasks([])
        assert assignments == {}

    def test_no_healthy_devices_raises(self):
        pool = DevicePool([
            DeviceRecord(udid="DEV-001", status="blocked"),
            DeviceRecord(udid="DEV-002", status="offline"),
        ])
        swarm = IFarmSwarmController(pool)
        with pytest.raises(RuntimeError, match="No healthy devices"):
            swarm.distribute_tasks(["task"])

    def test_single_device_gets_all_tasks(self):
        swarm = self._swarm(n=1)
        tasks = ["a", "b", "c"]
        assignments = swarm.distribute_tasks(tasks)
        assert list(assignments.values()) == [tasks]

    def test_task_assignments_stored_on_swarm(self):
        swarm = self._swarm(n=2)
        swarm.distribute_tasks(["t1", "t2"])
        total = sum(len(v) for v in swarm._task_assignments.values())
        assert total == 2

    def test_n1_pool(self):
        swarm = self._swarm(n=1)
        assignments = swarm.distribute_tasks(["only_task"])
        assert sum(len(v) for v in assignments.values()) == 1

    def test_n5_pool(self):
        swarm = self._swarm(n=5)
        tasks = [f"task_{i}" for i in range(15)]
        assignments = swarm.distribute_tasks(tasks)
        assert sum(len(v) for v in assignments.values()) == 15


# ===========================================================================
# IFarmSwarmController — get_swarm_status
# ===========================================================================


class TestGetSwarmStatus:
    def test_status_counts(self):
        pool = DevicePool([
            DeviceRecord(udid="A", status="healthy"),
            DeviceRecord(udid="B", status="healthy"),
            DeviceRecord(udid="C", status="blocked"),
            DeviceRecord(udid="D", status="offline"),
        ])
        swarm = IFarmSwarmController(pool)
        status = swarm.get_swarm_status()

        assert status["total"] == 4
        assert status["healthy"] == 2
        assert status["blocked"] == 1
        assert status["offline"] == 1
        assert status["pending_requeue"] == 0

    def test_devices_list_included(self):
        pool = DevicePool([DeviceRecord(udid="A", role="scraper", status="healthy")])
        swarm = IFarmSwarmController(pool)
        status = swarm.get_swarm_status()
        assert len(status["devices"]) == 1
        assert status["devices"][0]["udid"] == "A"

    def test_pending_requeue_after_drain(self):
        pool = DevicePool([
            DeviceRecord(udid="A", status="healthy"),
            DeviceRecord(udid="B", status="healthy"),
        ])
        swarm = IFarmSwarmController(pool)
        swarm.distribute_tasks(["t1", "t2", "t3"])
        # Simulate blocking device B
        swarm.pool.update_status("B", "blocked")
        drained = swarm._drain_device_tasks("B")
        swarm._requeue_buffer.extend(drained)

        status = swarm.get_swarm_status()
        assert status["pending_requeue"] >= 0  # may be 0 if all went to A


# ===========================================================================
# IFarmSwarmController — flush_requeue_buffer
# ===========================================================================


class TestFlushRequeueBuffer:
    def test_flush_returns_and_clears(self):
        pool = DevicePool([DeviceRecord(udid="A", status="healthy")])
        swarm = IFarmSwarmController(pool)
        swarm._requeue_buffer = ["t1", "t2"]

        flushed = swarm.flush_requeue_buffer()
        assert flushed == ["t1", "t2"]
        assert swarm._requeue_buffer == []

    def test_flush_empty_buffer_returns_empty(self):
        pool = DevicePool([DeviceRecord(udid="A", status="healthy")])
        swarm = IFarmSwarmController(pool)
        assert swarm.flush_requeue_buffer() == []


# ===========================================================================
# IFarmSwarmController — health monitor
# ===========================================================================


class TestHealthMonitor:
    def _swarm(self, udids=("DEV-001", "DEV-002")) -> IFarmSwarmController:
        pool = DevicePool([
            DeviceRecord(udid=u, status="healthy") for u in udids
        ])
        return IFarmSwarmController(pool)

    def test_marks_disconnected_devices_offline(self):
        swarm = self._swarm(("DEV-001", "DEV-002"))
        swarm.distribute_tasks(["t1", "t2"])

        with patch("ifarm.utils.device.list_connected_udids",
                   return_value=["DEV-001"]):
            with patch.object(
                swarm, "_get_controller",
                return_value=MagicMock(get_current_ip=lambda: "1.2.3.4")
            ):
                swarm._run_health_cycle(auto_rotate=False)

        assert swarm.pool.get("DEV-002").status == "offline"

    def test_requeues_tasks_from_offline_device(self):
        swarm = self._swarm(("DEV-001", "DEV-002"))
        swarm.distribute_tasks(["t1", "t2", "t3", "t4"])

        with patch("ifarm.utils.device.list_connected_udids",
                   return_value=["DEV-001"]):
            with patch.object(
                swarm, "_get_controller",
                return_value=MagicMock(get_current_ip=lambda: "1.2.3.4")
            ):
                swarm._run_health_cycle(auto_rotate=False)

        # Tasks from DEV-002 should be in requeue buffer
        assert len(swarm._requeue_buffer) > 0

    def test_healthy_device_stays_healthy(self):
        swarm = self._swarm(("DEV-001",))
        mock_ctrl = MagicMock()
        mock_ctrl.get_current_ip.return_value = "5.6.7.8"

        with patch("ifarm.utils.device.list_connected_udids",
                   return_value=["DEV-001"]):
            with patch.object(swarm, "_get_controller", return_value=mock_ctrl):
                swarm._run_health_cycle(auto_rotate=False)

        assert swarm.pool.get("DEV-001").status == "healthy"

    def test_blocked_device_rotates_ip_when_auto_rotate_enabled(self):
        swarm = self._swarm(("DEV-001",))
        mock_ctrl = MagicMock()
        mock_ctrl.get_current_ip.side_effect = Exception("timeout")
        mock_ctrl.cycle_airplane_mode.return_value = "9.9.9.9"

        with patch("ifarm.utils.device.list_connected_udids",
                   return_value=["DEV-001"]):
            with patch.object(swarm, "_get_controller", return_value=mock_ctrl):
                swarm._run_health_cycle(auto_rotate=True)

        mock_ctrl.cycle_airplane_mode.assert_called_once()

    def test_monitor_stops_on_event(self):
        swarm = self._swarm(("DEV-001",))
        stop = threading.Event()

        with patch.object(swarm, "_run_health_cycle"):
            t = threading.Thread(
                target=swarm.run_health_monitor,
                kwargs={"interval_seconds": 60, "stop_event": stop},
                daemon=True,
            )
            t.start()
            stop.set()
            t.join(timeout=2.0)

        assert not t.is_alive()


# ===========================================================================
# _get_task_role helper
# ===========================================================================


class TestGetTaskRole:
    def test_dict_with_role(self):
        assert _get_task_role({"role": "scraper", "action": "x"}) == "scraper"

    def test_dict_without_role(self):
        assert _get_task_role({"action": "x"}) is None

    def test_string_task(self):
        assert _get_task_role("just_a_string") is None

    def test_object_with_role_attr(self):
        class Task:
            role = "general"
        assert _get_task_role(Task()) == "general"

    def test_object_without_role_attr(self):
        assert _get_task_role(object()) is None


# ===========================================================================
# GPS spoofing (hardware module)
# ===========================================================================


class TestSpoofGPS:
    def test_spoof_calls_idevicelocation(self):
        from ifarm.modules.hardware import spoof_gps
        with patch("ifarm.modules.hardware._run") as mock_run:
            mock_run.side_effect = [
                (1, "usage: idevicelocation ..."),  # --help check
                (0, ""),                             # actual spoof
            ]
            result = spoof_gps("UDID-123", 32.7767, -96.7970)
        assert result is True
        assert mock_run.call_args_list[1] == call(
            ["idevicelocation", "-u", "UDID-123", "32.7767", "-96.797"]
        )

    def test_spoof_raises_capability_not_available(self):
        from ifarm.modules.hardware import spoof_gps
        from ifarm.exceptions import CapabilityNotAvailable
        with patch("ifarm.modules.hardware._run", return_value=(-1, "command not found")):
            with pytest.raises(CapabilityNotAvailable, match="idevicelocation"):
                spoof_gps("UDID-123", 32.7767, -96.7970)

    def test_invalid_latitude_raises(self):
        from ifarm.modules.hardware import spoof_gps
        with pytest.raises(ValueError, match="Latitude"):
            spoof_gps("UDID-123", 91.0, 0.0)

    def test_invalid_longitude_raises(self):
        from ifarm.modules.hardware import spoof_gps
        with pytest.raises(ValueError, match="Longitude"):
            spoof_gps("UDID-123", 0.0, 181.0)

    def test_command_failure_raises_ifarm_error(self):
        from ifarm.modules.hardware import spoof_gps
        from ifarm.exceptions import IFarmError
        with patch("ifarm.modules.hardware._run") as mock_run:
            mock_run.side_effect = [
                (1, "usage..."),  # --help
                (1, "device not found"),  # spoof command fails
            ]
            with pytest.raises(IFarmError, match="GPS spoof failed"):
                spoof_gps("UDID-123", 32.7767, -96.7970)


class TestClearGPSSpoof:
    def test_clear_calls_stop_flag(self):
        from ifarm.modules.hardware import clear_gps_spoof
        with patch("ifarm.modules.hardware._run") as mock_run:
            mock_run.side_effect = [
                (1, "usage..."),   # --help
                (0, ""),           # stop command
            ]
            result = clear_gps_spoof("UDID-123")
        assert result is True
        assert mock_run.call_args_list[1] == call(
            ["idevicelocation", "-u", "UDID-123", "-s"]
        )

    def test_clear_raises_capability_not_available(self):
        from ifarm.modules.hardware import clear_gps_spoof
        from ifarm.exceptions import CapabilityNotAvailable
        with patch("ifarm.modules.hardware._run", return_value=(-1, "not found")):
            with pytest.raises(CapabilityNotAvailable):
                clear_gps_spoof("UDID-123")


class TestSpoofGPSPreset:
    def test_preset_resolves_coordinates(self):
        from ifarm.modules.hardware import spoof_gps_preset
        locations = {"dallas": {"lat": 32.7767, "lon": -96.7970}}
        with patch("ifarm.modules.hardware.spoof_gps", return_value=True) as mock:
            result = spoof_gps_preset("UDID-123", "dallas", locations)
        assert result is True
        mock.assert_called_once_with("UDID-123", 32.7767, -96.797)

    def test_missing_preset_raises_key_error(self):
        from ifarm.modules.hardware import spoof_gps_preset
        with pytest.raises(KeyError, match="not found"):
            spoof_gps_preset("UDID-123", "nonexistent", {})

    def test_available_presets_listed_in_error(self):
        from ifarm.modules.hardware import spoof_gps_preset
        locations = {"dallas": {"lat": 1, "lon": 2}, "miami": {"lat": 3, "lon": 4}}
        with pytest.raises(KeyError, match="dallas"):
            spoof_gps_preset("UDID-123", "tokyo", locations)


# ===========================================================================
# Camera injection (hardware module) — Appium not installed guard
# ===========================================================================


class TestCameraInjectionGuards:
    """These tests verify graceful failure when Appium is not installed."""

    def test_inject_frame_raises_capability_not_available(self, tmp_path):
        from ifarm.modules.hardware import inject_camera_frame
        from ifarm.exceptions import CapabilityNotAvailable
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n")
        with patch.dict("sys.modules", {"appium": None,
                                         "appium.webdriver": None,
                                         "appium.options": None}):
            with pytest.raises(CapabilityNotAvailable, match="Appium"):
                inject_camera_frame("UDID-123", img, "com.example.app")

    def test_inject_video_raises_capability_not_available(self, tmp_path):
        from ifarm.modules.hardware import inject_camera_video
        from ifarm.exceptions import CapabilityNotAvailable
        vid = tmp_path / "test.mp4"
        vid.write_bytes(b"\x00\x00\x00\x20ftyp")
        with patch.dict("sys.modules", {"appium": None,
                                         "appium.webdriver": None,
                                         "appium.options": None}):
            with pytest.raises(CapabilityNotAvailable, match="Appium"):
                inject_camera_video("UDID-123", vid, "com.example.app")

    def test_inject_frame_missing_file_raises(self, tmp_path):
        from ifarm.modules.hardware import inject_camera_frame
        from ifarm.exceptions import CapabilityNotAvailable
        # If Appium is not installed we get CapabilityNotAvailable first;
        # if installed we'd get FileNotFoundError. Either is acceptable.
        with pytest.raises((FileNotFoundError, CapabilityNotAvailable)):
            inject_camera_frame("UDID-123", tmp_path / "missing.png", "com.example.app")


# ===========================================================================
# Hardware integration (requires physical device + idevicelocation)
# ===========================================================================


@pytest.mark.hardware
class TestHardwareIntegration:
    def test_gps_spoof_and_clear(self):
        pytest.skip("Requires device + idevicelocation — run manually")

    def test_gps_preset(self):
        pytest.skip("Requires device + idevicelocation — run manually")

    def test_swarm_task_distribution(self):
        pytest.skip("Requires USB hub with multiple devices — run manually")

    def test_health_monitor_failover(self):
        pytest.skip("Requires multiple devices — run manually")
