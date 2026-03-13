"""iFarm system diagnostics — `ifarm doctor`.

Checks every prerequisite and reports status as a structured dict so both
humans and AI agents can parse the output and know exactly what to install.

Used by:
  - `ifarm doctor` CLI subcommand
  - `GET /api/status` HTTP endpoint
  - Any script that needs to verify the environment before running tasks

Output schema:
  {
    "version": "0.1.0",
    "timestamp": "...",
    "platform": "darwin",
    "python": "3.11.x",
    "overall": "network_ready" | "automation_ready" | "fully_ready" | "not_ready",
    "phases": {"foundation": "ready"|"missing_deps", "network": ..., "automation": ..., "hardware": ...},
    "checks": [
      {
        "id": "libimobiledevice",
        "name": "libimobiledevice",
        "phase": 1,
        "status": "ok" | "missing" | "error",
        "detail": "version string or error message",
        "fix": "exact shell command to resolve the issue"
      },
      ...
    ]
  }
"""
from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from ifarm import __version__


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    """Run a subprocess command, return (returncode, stdout+stderr)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return -1, f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return -1, f"{cmd[0]}: timed out after {timeout}s"
    except Exception as e:
        return -1, str(e)


def _check_python() -> dict:
    v = sys.version_info
    ok = v >= (3, 11)
    return {
        "id": "python",
        "name": "Python ≥ 3.11",
        "phase": 0,
        "status": "ok" if ok else "error",
        "detail": f"{v.major}.{v.minor}.{v.micro}",
        "fix": "Install Python 3.11+: brew install python@3.11" if not ok else None,
    }


def _check_libimobiledevice() -> dict:
    code, out = _run(["idevice_id", "--version"])
    if code == 0:
        return {"id": "libimobiledevice", "name": "libimobiledevice", "phase": 1,
                "status": "ok", "detail": out.splitlines()[0] if out else "installed", "fix": None}
    return {"id": "libimobiledevice", "name": "libimobiledevice", "phase": 1,
            "status": "missing", "detail": out,
            "fix": "brew install libimobiledevice"}


def _check_connected_devices() -> dict:
    code, out = _run(["idevice_id", "-l"])
    if code != 0:
        return {"id": "devices", "name": "Connected iOS devices", "phase": 1,
                "status": "error", "detail": "libimobiledevice not installed", "fix": None}
    udids = [l.strip() for l in out.splitlines() if l.strip()]
    if udids:
        return {"id": "devices", "name": "Connected iOS devices", "phase": 1,
                "status": "ok", "detail": f"{len(udids)} device(s): {', '.join(udids)}", "fix": None}
    return {"id": "devices", "name": "Connected iOS devices", "phase": 1,
            "status": "missing",
            "detail": "No devices connected",
            "fix": "Connect iPhone via USB, tap Trust on the device, then retry"}


def _check_networksetup() -> dict:
    code, out = _run(["networksetup", "-version"])
    # networksetup doesn't have --version, but it always exists on macOS
    from pathlib import Path
    exists = Path("/usr/sbin/networksetup").exists()
    return {"id": "networksetup", "name": "networksetup (macOS built-in)", "phase": 1,
            "status": "ok" if exists else "missing",
            "detail": "present" if exists else "not found — are you on macOS?",
            "fix": None if exists else "iFarm requires macOS"}


def _check_appium_server() -> dict:
    code, out = _run(["appium", "--version"])
    if code == 0:
        return {"id": "appium", "name": "Appium server", "phase": 2,
                "status": "ok", "detail": out.strip(), "fix": None}
    return {"id": "appium", "name": "Appium server", "phase": 2,
            "status": "missing", "detail": out,
            "fix": "npm install -g appium"}


def _check_appium_xcuitest() -> dict:
    code, out = _run(["appium", "driver", "list", "--installed", "--json"], timeout=15)
    if code == 0:
        try:
            import json
            drivers = json.loads(out)
            if "xcuitest" in drivers:
                v = drivers["xcuitest"].get("version", "installed")
                return {"id": "appium_xcuitest", "name": "Appium XCUITest driver", "phase": 2,
                        "status": "ok", "detail": f"xcuitest@{v}", "fix": None}
        except Exception:
            # If parsing fails, fall through to string check
            if "xcuitest" in out.lower():
                return {"id": "appium_xcuitest", "name": "Appium XCUITest driver", "phase": 2,
                        "status": "ok", "detail": "installed", "fix": None}
    # appium not installed or driver missing
    if code == -1:  # appium not found
        return {"id": "appium_xcuitest", "name": "Appium XCUITest driver", "phase": 2,
                "status": "missing", "detail": "Appium not installed",
                "fix": "npm install -g appium && appium driver install xcuitest"}
    return {"id": "appium_xcuitest", "name": "Appium XCUITest driver", "phase": 2,
            "status": "missing", "detail": "driver not installed",
            "fix": "appium driver install xcuitest"}


def _check_appium_client() -> dict:
    try:
        import appium  # noqa: F401
        from importlib.metadata import version
        v = version("Appium-Python-Client")
        return {"id": "appium_client", "name": "Appium-Python-Client", "phase": 2,
                "status": "ok", "detail": v, "fix": None}
    except Exception:
        return {"id": "appium_client", "name": "Appium-Python-Client", "phase": 2,
                "status": "missing", "detail": "not installed",
                "fix": "pip install ifarm[automation]"}


def _check_opencv() -> dict:
    try:
        import cv2
        return {"id": "opencv", "name": "opencv-python", "phase": 2,
                "status": "ok", "detail": cv2.__version__, "fix": None}
    except ImportError:
        return {"id": "opencv", "name": "opencv-python", "phase": 2,
                "status": "missing", "detail": "not installed",
                "fix": "pip install ifarm[automation]"}


def _check_tesseract() -> dict:
    try:
        import pytesseract
        code, out = _run(["tesseract", "--version"])
        if code == 0:
            v = out.splitlines()[0] if out else "installed"
            return {"id": "tesseract", "name": "Tesseract OCR", "phase": 2,
                    "status": "ok", "detail": v, "fix": None}
        return {"id": "tesseract", "name": "Tesseract OCR", "phase": 2,
                "status": "missing", "detail": "binary not found",
                "fix": "brew install tesseract"}
    except ImportError:
        return {"id": "tesseract", "name": "Tesseract OCR", "phase": 2,
                "status": "missing", "detail": "pytesseract not installed",
                "fix": "pip install ifarm[automation] && brew install tesseract"}


def _check_ollama() -> dict:
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            detail = f"running, {len(models)} model(s) loaded"
            return {"id": "ollama", "name": "Ollama server", "phase": 2,
                    "status": "ok", "detail": detail, "fix": None}
    except Exception:
        pass
    return {"id": "ollama", "name": "Ollama server", "phase": 2,
            "status": "missing",
            "detail": "not reachable at http://localhost:11434",
            "fix": "brew install ollama && ollama serve"}


def _check_ollama_model(model: str = "qwen2-vl") -> dict:
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            if any(m == model or m.startswith(model + ":") for m in models):
                return {"id": "ollama_model", "name": f"Ollama model ({model})", "phase": 2,
                        "status": "ok", "detail": f"{model} available", "fix": None}
            return {"id": "ollama_model", "name": f"Ollama model ({model})", "phase": 2,
                    "status": "missing",
                    "detail": f"{model} not pulled (available: {models or 'none'})",
                    "fix": f"ollama pull {model}"}
    except Exception:
        pass
    return {"id": "ollama_model", "name": f"Ollama model ({model})", "phase": 2,
            "status": "error", "detail": "Ollama not reachable",
            "fix": "ollama serve  # start Ollama first, then retry"}


def _check_idevicelocation() -> dict:
    code, out = _run(["idevicelocation", "--help"])
    # idevicelocation exits non-zero on --help but prints usage
    if code != -1:
        return {"id": "idevicelocation", "name": "idevicelocation (GPS spoof)", "phase": 3,
                "status": "ok", "detail": "installed", "fix": None}
    return {"id": "idevicelocation", "name": "idevicelocation (GPS spoof)", "phase": 3,
            "status": "missing", "detail": out,
            "fix": "brew install idevicelocation"}


def _check_config_files() -> list[dict]:
    from pathlib import Path
    results = []

    toml = Path.cwd() / "ifarm.toml"
    if toml.exists():
        results.append({"id": "config_toml", "name": "ifarm.toml", "phase": 0,
                         "status": "ok", "detail": str(toml), "fix": None})
    else:
        results.append({"id": "config_toml", "name": "ifarm.toml", "phase": 0,
                         "status": "missing", "detail": "not found in current directory",
                         "fix": "cp config/ifarm.example.toml ifarm.toml"})

    devices = Path.cwd() / "config" / "devices.json"
    if devices.exists():
        import json
        try:
            data = json.loads(devices.read_text())
            n = len(data)
            has_real = any(
                d.get("udid", "").startswith("TEST") is False
                and "000000000000001E" not in d.get("udid", "")
                for d in data
            )
            detail = f"{n} device(s) configured" + ("" if has_real else " (example UDIDs — replace with real ones)")
            results.append({"id": "config_devices", "name": "config/devices.json", "phase": 0,
                             "status": "ok", "detail": detail, "fix": None})
        except Exception as e:
            results.append({"id": "config_devices", "name": "config/devices.json", "phase": 0,
                             "status": "error", "detail": str(e),
                             "fix": "cp config/devices.example.json config/devices.json"})
    else:
        results.append({"id": "config_devices", "name": "config/devices.json", "phase": 0,
                         "status": "missing", "detail": "not found",
                         "fix": "cp config/devices.example.json config/devices.json  # then edit with real UDIDs"})

    return results


def _check_full_disk_access() -> dict:
    """Best-effort check: try opening chat.db."""
    from pathlib import Path
    db = Path.home() / "Library" / "Messages" / "chat.db"
    if not db.exists():
        return {"id": "full_disk_access", "name": "Full Disk Access (chat.db)", "phase": 1,
                "status": "missing",
                "detail": "chat.db does not exist — Messages may not be set up",
                "fix": "Enable Messages in System Settings and pair your iPhone"}
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.execute("SELECT count(*) FROM sqlite_master")
        conn.close()
        return {"id": "full_disk_access", "name": "Full Disk Access (chat.db)", "phase": 1,
                "status": "ok", "detail": str(db), "fix": None}
    except Exception as e:
        return {"id": "full_disk_access", "name": "Full Disk Access (chat.db)", "phase": 1,
                "status": "error",
                "detail": str(e),
                "fix": "System Settings → Privacy & Security → Full Disk Access → add Terminal (or your Python binary)"}


def run_checks(ollama_model: str = "qwen2-vl") -> dict[str, Any]:
    """Run all system checks and return a structured status report.

    Args:
        ollama_model: VLM model name to check availability for.

    Returns:
        Structured dict suitable for JSON serialisation.
    """
    checks: list[dict] = []
    checks.append(_check_python())
    checks.append(_check_networksetup())
    checks.append(_check_libimobiledevice())
    checks.append(_check_connected_devices())
    checks.append(_check_full_disk_access())
    checks.append(_check_appium_server())
    checks.append(_check_appium_xcuitest())
    checks.append(_check_appium_client())
    checks.append(_check_opencv())
    checks.append(_check_tesseract())
    checks.append(_check_ollama())
    checks.append(_check_ollama_model(ollama_model))
    checks.append(_check_idevicelocation())
    checks.extend(_check_config_files())

    def phase_ready(phase: int) -> bool:
        return all(
            c["status"] == "ok"
            for c in checks
            if c["phase"] == phase
        )

    p0 = phase_ready(0)
    p1 = phase_ready(1)
    p2 = phase_ready(2)
    p3 = phase_ready(3)

    if p0 and p1 and p2 and p3:
        overall = "fully_ready"
    elif p0 and p1 and p2:
        overall = "automation_ready"
    elif p0 and p1:
        overall = "network_ready"
    else:
        overall = "not_ready"

    return {
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "overall": overall,
        "phases": {
            "foundation": "ready" if p0 else "missing_deps",
            "network":    "ready" if p1 else "missing_deps",
            "automation": "ready" if p2 else "missing_deps",
            "hardware":   "ready" if p3 else "missing_deps",
        },
        "checks": checks,
        "missing": [c for c in checks if c["status"] != "ok"],
    }
