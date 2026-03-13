# AGENTS.md — iFarm Integration Guide for AI Agents

This file is for AI agents and automated installers. It tells you exactly what
iFarm is, how to install it, and how to call it. No assumptions are made about
what you already know.

---

## What is iFarm?

iFarm is a Python library + HTTP server that gives AI agents control over
physical iPhones connected via USB to a Mac. It runs locally — no cloud, no
SaaS, no external accounts required.

**Capabilities by phase:**

| Phase | Capability | Key dependency |
|-------|-----------|----------------|
| 1 | IP rotation via cellular (iPhone as proxy) | `libimobiledevice` |
| 1 | SMS / 2FA code extraction | macOS Full Disk Access |
| 2 | Visual scraping of iOS apps (screenshot → VLM → JSON) | Appium + Ollama or MLX |
| 3 | GPS spoofing, camera injection | `idevicelocation` |

---

## System Requirements

- macOS 12+ (Monterey or later)
- Python 3.11+
- iPhone connected via USB with "Trust" approved
- `brew` (Homebrew) installed

---

## Quickstart — One Command

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/ifarm/main/scripts/setup.sh | bash
```

Or manually:

```bash
git clone <repo-url> ifarm && cd ifarm
bash scripts/setup.sh
```

The setup script installs all dependencies, runs `ifarm doctor`, and prints
the status. If everything is green, iFarm is ready.

---

## Manual Installation

### Step 1 — Install system dependencies

```bash
# Homebrew packages
brew install libimobiledevice node

# Appium server (for automation/visual scraping)
npm install -g appium
appium driver install xcuitest

# VLM backend (choose one)
brew install ollama
ollama pull qwen2-vl          # recommended default
ollama serve &                # or: brew services start ollama
```

### Step 2 — Install iFarm Python package

```bash
# Network only (IP rotation + SMS)
pip install -e ".[network]"

# Network + Automation (adds Appium client, OpenCV, Tesseract)
pip install -e ".[automation]"

# Network + Automation + HTTP server
pip install -e ".[automation,serve]"
```

### Step 3 — Configure

```bash
cp config/ifarm.example.toml ifarm.toml
cp config/devices.example.json config/devices.json
```

Edit `config/devices.json` — replace example UDIDs with real ones:

```bash
idevice_id -l   # prints UDIDs of connected devices
```

### Step 4 — Verify

```bash
ifarm doctor            # human-readable output
ifarm doctor --json     # machine-readable JSON
```

The `--json` output has this schema:

```json
{
  "overall": "network_ready",
  "phases": {
    "foundation": "ready",
    "network": "ready",
    "automation": "missing_deps",
    "hardware": "missing_deps"
  },
  "checks": [
    {
      "id": "libimobiledevice",
      "name": "libimobiledevice",
      "phase": 1,
      "status": "ok",
      "detail": "installed",
      "fix": null
    }
  ],
  "missing": []
}
```

`status` is one of: `"ok"` | `"missing"` | `"error"`. If not `"ok"`, the
`"fix"` field contains the exact shell command to resolve the issue.

**Decision logic for agents:**

```
if overall == "fully_ready"       → all capabilities available
if overall == "automation_ready"  → network + automation available (hardware not ready)
if overall == "network_ready"     → only network (IP rotation + SMS) available
if overall == "not_ready"         → run the "fix" command for each item in "missing"
```

---

## Starting the HTTP Server

```bash
ifarm serve                     # port 7420 (default)
ifarm serve --port 8080
ifarm serve --config ./ifarm.toml
```

The server is now reachable at `http://127.0.0.1:7420`.

Interactive API docs: `http://127.0.0.1:7420/docs`

---

## HTTP API Reference

All endpoints accept and return JSON. Errors return structured objects:

```json
{
  "detail": {
    "error_code": "SMSError",
    "detail": "human-readable message",
    "retryable": false
  }
}
```

### `GET /health`

Quick liveness check. Always returns 200 if the server is running.

```json
{"status": "ok", "version": "0.1.0"}
```

### `GET /api/status`

Full environment diagnostics (same as `ifarm doctor --json`). Call this on
startup to know which capabilities are available.

```
GET http://127.0.0.1:7420/api/status
```

### `POST /proxy/establish`

Establish the cellular route through the tethered iPhone.

```json
{ "udid": "DEVICE-UDID-HERE" }
```

Response: `{"success": true}`

### `POST /proxy/rotate`

Bounce the USB interface to obtain a fresh cellular IP address.

```json
{ "udid": "DEVICE-UDID-HERE" }
```

Response: `{"new_ip": "104.28.x.x"}`

### `POST /sms/2fa`

Fetch the most recent 2FA code from the macOS Messages database.

```json
{
  "keyword": "code",
  "since_seconds": 60
}
```

Response: `{"code": "483921"}` or `{"code": null}` if none found.

### `POST /scrape/feed`

Launch an iOS app, scroll through the feed, and return extracted items as JSON.

```json
{
  "udid": "DEVICE-UDID-HERE",
  "bundle_id": "com.example.app",
  "swipes": 5,
  "extraction_prompt": null
}
```

Response:

```json
{
  "items": [
    {"username": "alice", "content": "Hello", "like_count": 12}
  ],
  "count": 1
}
```

### `POST /scrape/tap`

Tap a UI element identified by its visible text label.

```json
{
  "udid": "DEVICE-UDID-HERE",
  "target_text": "Sign In"
}
```

Response: `{"tapped": true}`

### `POST /hardware/gps`  _(hardware — not yet implemented)_

Inject GPS coordinates into the device.

```json
{"udid": "DEVICE-UDID-HERE", "lat": 32.7767, "lon": -96.7970}
```

Response: `{"success": true}` — returns HTTP 501 until hardware support is installed.

### `POST /hardware/camera`  _(hardware — not yet implemented)_

Inject a static image into the device camera buffer.

```json
{"udid": "DEVICE-UDID-HERE", "image_path": "/abs/path/to/image.png"}
```

Response: `{"success": true}` — returns HTTP 501 until hardware support is installed.

---

## Python API (direct, no HTTP)

If you are running Python and prefer to skip the HTTP layer:

```python
from ifarm.controller import IFarmController

farm = IFarmController(udid="YOUR-DEVICE-UDID")

# Network
interface = farm.detect_usb_interface()
farm.establish_cellular_route()
new_ip = farm.cycle_airplane_mode()
code = farm.fetch_recent_2fa(keyword="code")

# Automation (requires Appium running — pip install ifarm[automation])
from ifarm.vision import get_backend
from ifarm.utils.config import load_config

config = load_config()
backend = get_backend(config)
items = farm.visual_scrape_feed(bundle_id="com.example.app", backend=backend)
```

---

## OpenClaw Integration

iFarm ships a ready-made OpenClaw workspace skill.

```bash
# Copy the skill into OpenClaw
cp -r skills/openclaw-ifarm ~/.openclaw/workspace/skills/ifarm

# Start iFarm server in background
ifarm serve --port 7420 &

# OpenClaw now has an "ifarm" skill available
```

The skill file is at `skills/openclaw-ifarm/SKILL.md`. It contains the
complete prompt-engineering context OpenClaw needs to call the HTTP API.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `idevice_id: command not found` | `brew install libimobiledevice` |
| `No devices connected` | Check USB cable; tap "Trust" on iPhone |
| `Cannot open chat.db` | Add Terminal to Full Disk Access in System Settings |
| `Appium request failed` | `appium --version` — ensure server is running |
| `Ollama not reachable` | `ollama serve` or `brew services start ollama` |
| `qwen2-vl not pulled` | `ollama pull qwen2-vl` |
| HTTP 500 with `SMSError` | See `fix` field in error JSON |
| HTTP 501 | Hardware feature not yet implemented |

Run `ifarm doctor --json` first — the `missing` array contains exact fix commands.
