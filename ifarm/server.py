"""iFarm local HTTP server — OpenClaw integration bridge.

Exposes IFarmController methods as a lightweight REST API so any
language runtime (TypeScript/Node OpenClaw, shell scripts, etc.) can
call iFarm without embedding a Python interpreter.

Start the server:
    ifarm serve                     # default port 7420
    ifarm serve --port 8080
    ifarm serve --config ./ifarm.toml

Requires: pip install ifarm[serve]  (adds fastapi + uvicorn)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore[misc, assignment]
    BaseModel = object  # type: ignore[assignment, misc]

from ifarm.controller import IFarmController
from ifarm.exceptions import IFarmError
from ifarm.utils.config import load_config
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)


def _require_fastapi() -> None:
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError(
            "fastapi and uvicorn are required to run the server. "
            "Install with: pip install ifarm[serve]"
        )


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

if _FASTAPI_AVAILABLE:
    class RotateRequest(BaseModel):
        udid: str

    class TwoFARequest(BaseModel):
        keyword: str = "code"
        since_seconds: int = 60

    class ScrapeRequest(BaseModel):
        udid: str
        bundle_id: str
        swipes: int = 5
        extraction_prompt: str | None = None

    class TapRequest(BaseModel):
        udid: str
        target_text: str

    class GPSRequest(BaseModel):
        udid: str
        lat: float
        lon: float

    class CameraRequest(BaseModel):
        udid: str
        image_path: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(config_path: Path | str | None = None) -> Any:
    """Create and return the FastAPI application.

    Args:
        config_path: Path to ifarm.toml. Falls back to default search.

    Returns:
        FastAPI app instance.
    """
    _require_fastapi()

    config = load_config(config_path)
    app = FastAPI(
        title="iFarm",
        description="iOS device orchestration API for AI agents",
        version="0.1.0",
    )

    def _farm(udid: str) -> IFarmController:
        return IFarmController(udid=udid, config_path=config_path)

    def _handle(fn):
        """Wrap IFarmError into a structured HTTPException."""
        try:
            return fn()
        except IFarmError as e:
            raise HTTPException(
                status_code=500,
                detail={
                    "error_code": type(e).__name__,
                    "detail": str(e),
                    "retryable": False,
                },
            )
        except NotImplementedError as e:
            raise HTTPException(
                status_code=501,
                detail={
                    "error_code": "NotImplemented",
                    "detail": str(e),
                    "retryable": False,
                },
            )

    # ------------------------------------------------------------------
    # Health & diagnostics
    # ------------------------------------------------------------------

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/status")
    def api_status():
        """Full environment diagnostics — same as `ifarm doctor --json`.

        AI agents should call this endpoint first to determine which
        capabilities are available before issuing other requests.
        """
        from ifarm.diagnostics import run_checks
        return run_checks()

    # ------------------------------------------------------------------
    # Network — Proxy
    # ------------------------------------------------------------------

    @app.post("/proxy/establish")
    def establish_route(req: RotateRequest):
        """Establish cellular route through the tethered iPhone."""
        return _handle(lambda: {
            "success": _farm(req.udid).establish_cellular_route()
        })

    @app.post("/proxy/rotate")
    def rotate_ip(req: RotateRequest):
        """Bounce the USB interface to obtain a fresh cellular IP."""
        return _handle(lambda: {
            "new_ip": _farm(req.udid).cycle_airplane_mode()
        })

    # ------------------------------------------------------------------
    # Network — SMS
    # ------------------------------------------------------------------

    @app.post("/sms/2fa")
    def fetch_2fa(req: TwoFARequest):
        """Fetch the most recent 2FA code from the macOS Messages database."""
        # SMS module is device-agnostic (reads local chat.db) so no udid needed
        from ifarm.modules.sms import fetch_recent_2fa
        db_path = config.sms.get("db_path")
        code = _handle(lambda: fetch_recent_2fa(
            keyword=req.keyword,
            since_seconds=req.since_seconds,
            db_path=db_path,
        ))
        return {"code": code}

    # ------------------------------------------------------------------
    # Automation — Visual scraping
    # ------------------------------------------------------------------

    @app.post("/scrape/feed")
    def scrape_feed(req: ScrapeRequest):
        """Launch iOS app, scroll, and return VLM-extracted feed data."""
        from ifarm.vision import get_backend
        backend = _handle(lambda: get_backend(config))
        results = _handle(lambda: _farm(req.udid).visual_scrape_feed(
            bundle_id=req.bundle_id,
            swipes=req.swipes,
            backend=backend,
        ))
        return {"items": results, "count": len(results)}

    @app.post("/scrape/tap")
    def tap_element(req: TapRequest):
        """Tap a UI element identified by visible text."""
        from ifarm.vision import get_backend
        backend = _handle(lambda: get_backend(config))
        tapped = _handle(lambda: _farm(req.udid).tap_ui_element_by_text(
            target_text=req.target_text,
            backend=backend,
        ))
        return {"tapped": tapped}

    # ------------------------------------------------------------------
    # Hardware Emulation
    # ------------------------------------------------------------------

    @app.post("/hardware/gps")
    def spoof_gps(req: GPSRequest):
        """Inject GPS coordinates into the device's CoreLocation daemon."""
        return _handle(lambda: {
            "success": _farm(req.udid).spoof_gps(req.lat, req.lon)
        })

    @app.post("/hardware/camera")
    def inject_camera(req: CameraRequest):
        """Inject a static image into the device's camera buffer."""
        return _handle(lambda: {
            "success": _farm(req.udid).inject_camera_frame(req.image_path)
        })

    _log.info("iFarm HTTP server created")
    return app
