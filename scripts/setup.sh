#!/usr/bin/env bash
# iFarm setup script — installs all dependencies and verifies the environment.
#
# Usage:
#   bash scripts/setup.sh              # network only (IP rotation + SMS)
#   bash scripts/setup.sh --network    # same as default
#   bash scripts/setup.sh --automation # adds Appium + VLM
#   bash scripts/setup.sh --full       # everything + HTTP server
#
# This script is safe to re-run. Idempotent — skips already-installed items.

set -euo pipefail

EXTRAS="network"
INSTALL_SERVE=false

for arg in "$@"; do
  case "$arg" in
    --network)    EXTRAS="network" ;;
    --automation) EXTRAS="automation" ;;
    --full)       EXTRAS="automation"; INSTALL_SERVE=true ;;
    --serve)      INSTALL_SERVE=true ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()    { echo "  [+] $*"; }
warn()    { echo "  [!] $*" >&2; }
ok()      { echo "  [✓] $*"; }
fail()    { echo "  [✗] $*" >&2; exit 1; }
section() { echo; echo "── $* ──"; }

require_macos() {
  [[ "$(uname)" == "Darwin" ]] || fail "iFarm requires macOS."
}

require_python311() {
  if ! python3 -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
    fail "Python 3.11+ required. Install: brew install python@3.11"
  fi
  ok "Python $(python3 --version)"
}

brew_install() {
  local pkg="$1"
  if brew list "$pkg" &>/dev/null 2>&1; then
    ok "$pkg (already installed)"
  else
    info "Installing $pkg via Homebrew..."
    brew install "$pkg"
    ok "$pkg installed"
  fi
}

npm_global_install() {
  local pkg="$1"
  if npm list -g --depth=0 "$pkg" &>/dev/null 2>&1; then
    ok "$pkg (already installed)"
  else
    info "Installing $pkg via npm..."
    npm install -g "$pkg"
    ok "$pkg installed"
  fi
}

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

require_macos
section "System checks"
require_python311

# Homebrew
if ! command -v brew &>/dev/null; then
  fail "Homebrew not found. Install from https://brew.sh then re-run this script."
fi
ok "Homebrew $(brew --version | head -1)"

# ---------------------------------------------------------------------------
# Phase 1 — libimobiledevice
# ---------------------------------------------------------------------------

section "Network — libimobiledevice"
brew_install libimobiledevice

# ---------------------------------------------------------------------------
# Automation — Appium + VLM (optional)
# ---------------------------------------------------------------------------

if [[ "$EXTRAS" == "automation" ]]; then
  section "Automation — Node.js + Appium"

  if ! command -v node &>/dev/null; then
    brew_install node
  else
    ok "Node.js $(node --version)"
  fi

  npm_global_install appium

  # Install XCUITest driver if not already present
  if appium driver list --installed --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if 'xcuitest' in d else 1)" 2>/dev/null; then
    ok "Appium XCUITest driver (already installed)"
  else
    info "Installing Appium XCUITest driver..."
    appium driver install xcuitest
    ok "Appium XCUITest driver installed"
  fi

  section "Automation — Ollama VLM"
  brew_install ollama

  if ollama list 2>/dev/null | grep -q "qwen2-vl"; then
    ok "qwen2-vl model (already pulled)"
  else
    info "Pulling qwen2-vl model (this may take several minutes)..."
    ollama pull qwen2-vl
    ok "qwen2-vl model pulled"
  fi

  # Start Ollama if not running
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    info "Starting Ollama server..."
    brew services start ollama 2>/dev/null || ollama serve &
    sleep 3
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
      ok "Ollama server running"
    else
      warn "Ollama server may not have started yet. Run: ollama serve"
    fi
  else
    ok "Ollama server already running"
  fi
fi

# ---------------------------------------------------------------------------
# Python package
# ---------------------------------------------------------------------------

section "Python package"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

if $INSTALL_SERVE; then
  EXTRAS="${EXTRAS},serve"
fi

info "Installing ifarm[${EXTRAS}]..."
pip install -e ".[${EXTRAS}]" --quiet
ok "ifarm installed"

# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------

section "Configuration"

if [[ ! -f "$REPO_ROOT/ifarm.toml" ]]; then
  cp "$REPO_ROOT/config/ifarm.example.toml" "$REPO_ROOT/ifarm.toml"
  ok "Created ifarm.toml from example template"
else
  ok "ifarm.toml already exists"
fi

if [[ ! -f "$REPO_ROOT/config/devices.json" ]]; then
  cp "$REPO_ROOT/config/devices.example.json" "$REPO_ROOT/config/devices.json"
  warn "Created config/devices.json — EDIT THIS FILE with your real device UDIDs"
  warn "Run: idevice_id -l   to list connected devices"
else
  ok "config/devices.json already exists"
fi

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

section "Environment check"
echo ""
ifarm doctor || true   # don't fail if some optional deps are missing

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "════════════════════════════════════════"
echo " iFarm setup complete"
echo ""
echo " Next steps:"
echo "   1. Edit config/devices.json with real UDIDs (idevice_id -l)"
echo "   2. Connect iPhone via USB and tap 'Trust' on the device"
echo "   3. Run: ifarm doctor"
if $INSTALL_SERVE; then
  echo "   4. Run: ifarm serve --port 7420"
  echo "   5. Copy OpenClaw skill: cp -r skills/openclaw-ifarm ~/.openclaw/workspace/skills/ifarm"
fi
echo "════════════════════════════════════════"
