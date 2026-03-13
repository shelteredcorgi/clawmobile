"""Tests for ifarm.diagnostics — system health checks."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ifarm import diagnostics as diag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check(id, status, phase=1, fix=None):
    return {"id": id, "name": id, "phase": phase, "status": status,
            "detail": "test", "fix": fix}


# ---------------------------------------------------------------------------
# _run()
# ---------------------------------------------------------------------------

class TestRun:
    def test_success_returns_stdout(self):
        code, out = diag._run(["echo", "hello"])
        assert code == 0
        assert "hello" in out

    def test_missing_binary_returns_minus_one(self):
        code, out = diag._run(["__nonexistent_binary__"])
        assert code == -1
        assert "not found" in out

    def test_timeout_returns_minus_one(self):
        code, out = diag._run(["sleep", "10"], timeout=1)
        assert code == -1
        assert "timed out" in out


# ---------------------------------------------------------------------------
# _check_python()
# ---------------------------------------------------------------------------

class TestCheckPython:
    def test_ok_on_311_plus(self):
        result = diag._check_python()
        # We're running on 3.11+ per .python-version
        assert result["status"] == "ok"
        assert result["phase"] == 0

    def test_structure(self):
        result = diag._check_python()
        assert {"id", "name", "phase", "status", "detail", "fix"} <= result.keys()


# ---------------------------------------------------------------------------
# _check_libimobiledevice()
# ---------------------------------------------------------------------------

class TestCheckLibimobiledevice:
    def test_ok_when_idevice_id_succeeds(self):
        with patch.object(diag, "_run", return_value=(0, "1.3.0")):
            result = diag._check_libimobiledevice()
        assert result["status"] == "ok"
        assert result["fix"] is None

    def test_missing_when_not_found(self):
        with patch.object(diag, "_run", return_value=(-1, "idevice_id: command not found")):
            result = diag._check_libimobiledevice()
        assert result["status"] == "missing"
        assert "brew install" in result["fix"]


# ---------------------------------------------------------------------------
# _check_connected_devices()
# ---------------------------------------------------------------------------

class TestCheckConnectedDevices:
    def test_ok_with_devices(self):
        with patch.object(diag, "_run", return_value=(0, "ABCDEF123456\nDEFGHI789012")):
            result = diag._check_connected_devices()
        assert result["status"] == "ok"
        assert "2 device(s)" in result["detail"]

    def test_missing_when_no_devices(self):
        with patch.object(diag, "_run", return_value=(0, "")):
            result = diag._check_connected_devices()
        assert result["status"] == "missing"

    def test_error_when_libimobiledevice_absent(self):
        with patch.object(diag, "_run", return_value=(-1, "not found")):
            result = diag._check_connected_devices()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# _check_appium_server()
# ---------------------------------------------------------------------------

class TestCheckAppiumServer:
    def test_ok_when_appium_present(self):
        with patch.object(diag, "_run", return_value=(0, "2.5.4")):
            result = diag._check_appium_server()
        assert result["status"] == "ok"
        assert result["detail"] == "2.5.4"

    def test_missing_when_not_found(self):
        with patch.object(diag, "_run", return_value=(-1, "appium: command not found")):
            result = diag._check_appium_server()
        assert result["status"] == "missing"
        assert "npm install" in result["fix"]


# ---------------------------------------------------------------------------
# _check_appium_xcuitest()
# ---------------------------------------------------------------------------

class TestCheckAppiumXCUITest:
    def test_ok_when_driver_installed(self):
        drivers_json = json.dumps({"xcuitest": {"version": "7.0.0"}})
        with patch.object(diag, "_run", return_value=(0, drivers_json)):
            result = diag._check_appium_xcuitest()
        assert result["status"] == "ok"
        assert "xcuitest@7.0.0" in result["detail"]

    def test_missing_when_driver_absent(self):
        with patch.object(diag, "_run", return_value=(0, json.dumps({}))):
            result = diag._check_appium_xcuitest()
        assert result["status"] == "missing"
        assert "appium driver install xcuitest" in result["fix"]

    def test_missing_appium_not_installed(self):
        with patch.object(diag, "_run", return_value=(-1, "not found")):
            result = diag._check_appium_xcuitest()
        assert result["status"] == "missing"
        assert "npm install" in result["fix"]


# ---------------------------------------------------------------------------
# _check_appium_client()
# ---------------------------------------------------------------------------

class TestCheckAppiumClient:
    def test_ok_when_importable(self):
        fake_appium = MagicMock()
        with patch.dict("sys.modules", {"appium": fake_appium}):
            with patch("importlib.metadata.version", return_value="3.1.0"):
                result = diag._check_appium_client()
        assert result["status"] == "ok"

    def test_missing_when_not_installed(self):
        with patch.dict("sys.modules", {"appium": None}):
            result = diag._check_appium_client()
        assert result["status"] == "missing"
        assert "pip install" in result["fix"]


# ---------------------------------------------------------------------------
# _check_ollama()
# ---------------------------------------------------------------------------

class TestCheckOllama:
    def test_ok_when_server_reachable(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"models": [{"name": "qwen2-vl"}]}
        fake_requests = MagicMock()
        fake_requests.get.return_value = fake_resp
        with patch.dict("sys.modules", {"requests": fake_requests}):
            result = diag._check_ollama()
        assert result["status"] == "ok"

    def test_missing_when_not_reachable(self):
        fake_requests = MagicMock()
        fake_requests.get.side_effect = ConnectionError("refused")
        with patch.dict("sys.modules", {"requests": fake_requests}):
            result = diag._check_ollama()
        assert result["status"] == "missing"
        assert "ollama serve" in result["fix"]


# ---------------------------------------------------------------------------
# _check_ollama_model()
# ---------------------------------------------------------------------------

class TestCheckOllamaModel:
    def test_ok_when_model_present(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"models": [{"name": "qwen2-vl:latest"}]}
        fake_requests = MagicMock()
        fake_requests.get.return_value = fake_resp
        with patch.dict("sys.modules", {"requests": fake_requests}):
            result = diag._check_ollama_model("qwen2-vl")
        assert result["status"] == "ok"

    def test_missing_when_model_not_pulled(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"models": [{"name": "llama3.2-vision"}]}
        fake_requests = MagicMock()
        fake_requests.get.return_value = fake_resp
        with patch.dict("sys.modules", {"requests": fake_requests}):
            result = diag._check_ollama_model("qwen2-vl")
        assert result["status"] == "missing"
        assert "ollama pull qwen2-vl" in result["fix"]


# ---------------------------------------------------------------------------
# _check_idevicelocation()
# ---------------------------------------------------------------------------

class TestCheckIdevicelocation:
    def test_ok_when_binary_exists(self):
        with patch.object(diag, "_run", return_value=(1, "usage: idevicelocation ...")):
            result = diag._check_idevicelocation()
        assert result["status"] == "ok"

    def test_missing_when_not_installed(self):
        with patch.object(diag, "_run", return_value=(-1, "command not found")):
            result = diag._check_idevicelocation()
        assert result["status"] == "missing"
        assert "brew install" in result["fix"]


# ---------------------------------------------------------------------------
# _check_full_disk_access()
# ---------------------------------------------------------------------------

class TestCheckFullDiskAccess:
    def test_ok_when_chat_db_readable(self, tmp_path):
        db = tmp_path / "chat.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE messages (id INT)")
        conn.close()
        with patch("pathlib.Path.home", return_value=tmp_path / "home"):
            # Reconstruct expected path under the fake home
            fake_home = tmp_path / "home"
            fake_db = fake_home / "Library" / "Messages" / "chat.db"
            fake_db.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(db, fake_db)
            result = diag._check_full_disk_access()
        assert result["status"] == "ok"

    def test_missing_when_no_chat_db(self, tmp_path):
        """If chat.db doesn't exist, status is missing."""
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()
        with patch("pathlib.Path.home", return_value=empty_home):
            result = diag._check_full_disk_access()
        assert result["status"] == "missing"


# ---------------------------------------------------------------------------
# _check_config_files()
# ---------------------------------------------------------------------------

class TestCheckConfigFiles:
    def test_both_present(self, tmp_path, monkeypatch):
        import json as _json

        monkeypatch.chdir(tmp_path)
        toml = tmp_path / "ifarm.toml"
        toml.write_text("[vision]\nbackend = 'ollama'\n")
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        devices = cfg_dir / "devices.json"
        devices.write_text(_json.dumps([{"udid": "REAL-UDID-12345", "role": "general"}]))

        results = diag._check_config_files()
        statuses = {r["id"]: r["status"] for r in results}
        assert statuses["config_toml"] == "ok"
        assert statuses["config_devices"] == "ok"

    def test_both_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        results = diag._check_config_files()
        statuses = {r["id"]: r["status"] for r in results}
        assert statuses["config_toml"] == "missing"
        assert statuses["config_devices"] == "missing"


# ---------------------------------------------------------------------------
# run_checks() — integration
# ---------------------------------------------------------------------------

class TestRunChecks:
    def _mock_all_ok(self):
        """Return a check dict with status=ok for all checks."""
        return [
            _check("python", "ok", 0),
            _check("networksetup", "ok", 1),
            _check("libimobiledevice", "ok", 1),
            _check("devices", "ok", 1),
            _check("full_disk_access", "ok", 1),
            _check("appium", "ok", 2),
            _check("appium_xcuitest", "ok", 2),
            _check("appium_client", "ok", 2),
            _check("opencv", "ok", 2),
            _check("tesseract", "ok", 2),
            _check("ollama", "ok", 2),
            _check("ollama_model", "ok", 2),
            _check("idevicelocation", "ok", 3),
            _check("config_toml", "ok", 0),
            _check("config_devices", "ok", 0),
        ]

    def test_output_schema(self):
        report = diag.run_checks()
        assert "version" in report
        assert "timestamp" in report
        assert "platform" in report
        assert "python" in report
        assert "overall" in report
        assert "phases" in report
        assert "checks" in report
        assert "missing" in report

    def test_overall_fully_ready_when_all_ok(self):
        checks = self._mock_all_ok()
        with patch.object(diag, "_check_python", return_value=checks[0]), \
             patch.object(diag, "_check_networksetup", return_value=checks[1]), \
             patch.object(diag, "_check_libimobiledevice", return_value=checks[2]), \
             patch.object(diag, "_check_connected_devices", return_value=checks[3]), \
             patch.object(diag, "_check_full_disk_access", return_value=checks[4]), \
             patch.object(diag, "_check_appium_server", return_value=checks[5]), \
             patch.object(diag, "_check_appium_xcuitest", return_value=checks[6]), \
             patch.object(diag, "_check_appium_client", return_value=checks[7]), \
             patch.object(diag, "_check_opencv", return_value=checks[8]), \
             patch.object(diag, "_check_tesseract", return_value=checks[9]), \
             patch.object(diag, "_check_ollama", return_value=checks[10]), \
             patch.object(diag, "_check_ollama_model", return_value=checks[11]), \
             patch.object(diag, "_check_idevicelocation", return_value=checks[12]), \
             patch.object(diag, "_check_config_files", return_value=[checks[13], checks[14]]):
            report = diag.run_checks()
        assert report["overall"] == "fully_ready"
        assert report["missing"] == []
        assert report["phases"]["network"] == "ready"
        assert report["phases"]["automation"] == "ready"
        assert report["phases"]["hardware"] == "ready"

    def test_overall_not_ready_when_phase1_missing(self):
        checks = self._mock_all_ok()
        missing = {**checks[2], "status": "missing"}  # libimobiledevice missing
        with patch.object(diag, "_check_python", return_value=checks[0]), \
             patch.object(diag, "_check_networksetup", return_value=checks[1]), \
             patch.object(diag, "_check_libimobiledevice", return_value=missing), \
             patch.object(diag, "_check_connected_devices", return_value=checks[3]), \
             patch.object(diag, "_check_full_disk_access", return_value=checks[4]), \
             patch.object(diag, "_check_appium_server", return_value=checks[5]), \
             patch.object(diag, "_check_appium_xcuitest", return_value=checks[6]), \
             patch.object(diag, "_check_appium_client", return_value=checks[7]), \
             patch.object(diag, "_check_opencv", return_value=checks[8]), \
             patch.object(diag, "_check_tesseract", return_value=checks[9]), \
             patch.object(diag, "_check_ollama", return_value=checks[10]), \
             patch.object(diag, "_check_ollama_model", return_value=checks[11]), \
             patch.object(diag, "_check_idevicelocation", return_value=checks[12]), \
             patch.object(diag, "_check_config_files", return_value=[checks[13], checks[14]]):
            report = diag.run_checks()
        assert report["overall"] == "not_ready"  # foundation not fully ready = not_ready
        assert len(report["missing"]) == 1
        assert report["missing"][0]["id"] == "libimobiledevice"

    def test_missing_list_excludes_ok_checks(self):
        report = diag.run_checks()
        for item in report["missing"]:
            assert item["status"] != "ok"
