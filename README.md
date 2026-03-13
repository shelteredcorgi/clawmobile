# iFarm

**iOS device orchestration for macOS.** Control USB-tethered iPhones from a
single host machine — route traffic through cellular networks, extract SMS
verification codes, automate native app UI, and emulate GPS and camera hardware.

Runs entirely on-premises. No cloud dependencies, no third-party services.

---

## Features

| Capability set | Capabilities | Install extra |
|----------------|-------------|--------------|
| **Network** | Cellular IP rotation via USB hotspot · SMS / OTP extraction | `ifarm[network]` |
| **Automation** | Native app control via XCUITest · Screenshot-to-JSON via local VLM · REST API | `ifarm[automation]` |
| **Hardware** | GPS coordinate injection · Camera frame injection · Multi-device orchestration | `ifarm[hardware]` |

Install only the capabilities you need.

---

## Requirements

**Host machine**
- macOS 12 Monterey or later
- Python 3.11+
- [Homebrew](https://brew.sh)

**Device**
- iPhone SE (2nd gen) or iPhone 11 or newer
- USB data cable (not a charge-only cable)
- Active SIM with cellular data + SMS

**Phase 2 only**
- Xcode Command Line Tools (`xcode-select --install`)
- Node.js + Appium (`brew install node && npm install -g appium`)

---

## Installation

### One-line setup

```bash
bash scripts/setup.sh                # network only (default)
bash scripts/setup.sh --automation   # network + automation
bash scripts/setup.sh --full         # everything including HTTP server
```

### Manual

```bash
# Clone the repo
git clone <repo-url> ifarm
cd ifarm

# Network — cellular routing + SMS
pip install -e ".[network,dev]"

# Automation — adds Appium client, OpenCV, Tesseract
pip install -e ".[automation,dev]"

# HTTP server (for remote / cross-language integrations)
pip install -e ".[serve]"

# Configure
cp config/ifarm.example.toml ifarm.toml
cp config/devices.example.json config/devices.json
# Edit config/devices.json with your device UDIDs: idevice_id -l
```

### Verify

```bash
ifarm doctor
```

```
iFarm Doctor  (v0.1.0)  —  darwin / Python 3.11.11
Overall: network_ready

  [foundation: ready]
    ✓  Python ≥ 3.11
    ✓  ifarm.toml
    ✓  config/devices.json
  [network: ready]
    ✓  networksetup (macOS built-in)
    ✓  libimobiledevice
    ✓  Connected iOS devices
    ✓  Full Disk Access (chat.db)
  [automation: missing_deps]
    ✗  Appium server
       fix: npm install -g appium
    ...
```

Add `--json` to get machine-readable output with `fix` commands for each
missing dependency.

---

## Quick Start

### Python API

```python
from ifarm import IFarmController

farm = IFarmController(udid="YOUR-DEVICE-UDID")

# Phase 1 — network + SMS
farm.establish_cellular_route()
new_ip = farm.cycle_airplane_mode()   # returns new public IP as string
code   = farm.fetch_recent_2fa()      # returns "483921" or None

# Phase 2 — visual automation
items = farm.visual_scrape_feed(
    bundle_id="com.example.app",
    swipes=10,
)
# items → [{"username": "alice", "content": "...", "like_count": 42}, ...]

tapped = farm.tap_ui_element_by_text("Sign In")

# Hardware emulation
farm.spoof_gps(lat=32.7767, lon=-96.7970)   # Dallas
```

### HTTP API

Start the server:

```bash
ifarm serve                    # http://127.0.0.1:7420
ifarm serve --port 8080
```

Browse the interactive docs at **http://127.0.0.1:7420/docs**.

```bash
# Check server health
curl http://127.0.0.1:7420/health

# Full environment diagnostics
curl http://127.0.0.1:7420/api/status

# Rotate IP
curl -X POST http://127.0.0.1:7420/proxy/rotate \
     -H "Content-Type: application/json" \
     -d '{"udid": "YOUR-DEVICE-UDID"}'

# Extract 2FA code
curl -X POST http://127.0.0.1:7420/sms/2fa \
     -H "Content-Type: application/json" \
     -d '{"keyword": "code", "since_seconds": 60}'

# Scrape an iOS app feed
curl -X POST http://127.0.0.1:7420/scrape/feed \
     -H "Content-Type: application/json" \
     -d '{"udid": "YOUR-DEVICE-UDID", "bundle_id": "com.example.app", "swipes": 5}'
```

---

## Configuration

iFarm looks for `ifarm.toml` in the current directory, then
`~/.config/ifarm/ifarm.toml`.

```toml
[vision]
# Options: "ollama" | "mlx" | "ocr" | "auto"
# "auto" probes each backend in order and uses the first available one.
backend = "ollama"
model   = "qwen2-vl"
host    = "http://localhost:11434"

[proxy]
airplane_mode_wait = 8        # seconds to wait after interface bounce
ip_probe_url = "https://api.ipify.org"

[sms]
default_window_seconds = 60

[appium]
host = "localhost"
port = 4723
```

See [`config/ifarm.example.toml`](config/ifarm.example.toml) for the full
annotated template. Device mappings live in `config/devices.json` (gitignored
— never commit real UDIDs).

---

## Vision Backends

iFarm ships three backends and selects among them based on `ifarm.toml`:

| Key | Description | Requires |
|-----|-------------|---------|
| `ollama` | Remote calls to a local Ollama server | `brew install ollama` |
| `mlx` | On-device inference via Apple MLX (Apple Silicon only) | `pip install mlx-vlm` |
| `ocr` | OpenCV + Tesseract fallback (numeric extraction only) | `brew install tesseract` |
| `auto` | Probes the above in order, uses first available | — |

The default model is `qwen2-vl`. Any Ollama-compatible multimodal model works.

---

## Testing

```bash
make test             # offline unit tests (no device required)
make test-hardware    # integration tests (USB device required)
make test-vlm         # VLM tests (Ollama required)
make test-all         # everything
```

Tests that require hardware are marked `@pytest.mark.hardware` and skipped
automatically when no device is connected.

---

## Project Structure

```
ifarm/
├── controller.py         # IFarmController — public API
├── swarm.py              # IFarmSwarmController — multi-device
├── modules/
│   ├── proxy.py          # Cellular routing (Phase 1)
│   ├── sms.py            # SMS extraction (Phase 1)
│   ├── scraper.py        # UI automation pipeline (Phase 2)
│   └── hardware.py       # GPS spoofing + camera injection
├── vision/               # Swappable vision backends
├── utils/                # Config, device discovery, logging
├── diagnostics.py        # ifarm doctor checks
├── server.py             # FastAPI HTTP server
└── cli.py                # CLI entry point
config/
├── ifarm.example.toml    # Config template (commit this)
└── devices.example.json  # Device map template (commit this)
scripts/
└── setup.sh              # One-shot installer
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, how to add a new
vision backend, and commit conventions.

---

## License

[MIT](LICENSE) — see the file for details.
