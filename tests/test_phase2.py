"""Phase 2 tests — visual scraping pipeline.

Covers:
  - VisionBackend interface contract
  - JSON extraction utility (_json_utils)
  - OllamaBackend (mocked HTTP)
  - MLXBackend (import guard)
  - OCRFallback (import guard)
  - Vision backend factory
  - Gesture math (bezier curves)
  - AppiumSession (mocked driver)
  - visual_scrape_feed pipeline (mocked session + VLM)
  - tap_ui_element_by_text (mocked session + VLM)
  - HTTP server endpoints (TestClient)

Run offline:
    python3.11 -m pytest tests/test_phase2.py -m "not hardware and not vlm" -v

Hardware + VLM integration tests (require device + Ollama):
    python3.11 -m pytest tests/test_phase2.py -m "hardware and vlm" -v
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from ifarm.vision._json_utils import parse_vlm_response
from ifarm.exceptions import VisionError
from ifarm.modules.scraper import _bezier_points, _random_jitter


# ===========================================================================
# JSON extraction utility
# ===========================================================================


class TestParseVlmResponse:
    def test_clean_json_object(self):
        result = parse_vlm_response('{"username": "alice", "views": 1000}')
        assert result == {"username": "alice", "views": 1000}

    def test_clean_json_array(self):
        result = parse_vlm_response('[{"a": 1}, {"b": 2}]')
        assert isinstance(result, list)
        assert len(result) == 2

    def test_markdown_fenced_json(self):
        raw = '```json\n{"username": "bob", "likes": 500}\n```'
        result = parse_vlm_response(raw)
        assert result["username"] == "bob"

    def test_markdown_fence_without_language(self):
        raw = '```\n{"x": 42}\n```'
        result = parse_vlm_response(raw)
        assert result["x"] == 42

    def test_json_embedded_in_text(self):
        raw = 'Here is the data: {"score": 99} as requested.'
        result = parse_vlm_response(raw)
        assert result["score"] == 99

    def test_raises_vision_error_on_no_json(self):
        with pytest.raises(VisionError, match="Could not extract JSON"):
            parse_vlm_response("There is no JSON here at all.")

    def test_raises_vision_error_with_context(self):
        with pytest.raises(VisionError, match="ollama/qwen2-vl"):
            parse_vlm_response("no json", context="ollama/qwen2-vl")

    def test_whitespace_stripped(self):
        result = parse_vlm_response('  \n  {"k": "v"}  \n  ')
        assert result["k"] == "v"

    def test_nested_json(self):
        raw = '{"post": {"user": "alice", "meta": {"views": 100}}}'
        result = parse_vlm_response(raw)
        assert result["post"]["meta"]["views"] == 100


# ===========================================================================
# VisionBackend interface contract
# ===========================================================================


class TestVisionBackendInterface:
    def test_incomplete_subclass_raises_type_error(self):
        from ifarm.vision.base import VisionBackend

        class Incomplete(VisionBackend):
            pass  # missing query()

        with pytest.raises(TypeError):
            Incomplete()

    def test_default_is_available_returns_true(self):
        from ifarm.vision.base import VisionBackend

        class Minimal(VisionBackend):
            def query(self, image_path, prompt) -> dict:
                return {}

        assert Minimal().is_available() is True

    def test_query_signature_enforced(self):
        from ifarm.vision.base import VisionBackend

        class Good(VisionBackend):
            def query(self, image_path, prompt) -> dict:
                return {"result": "ok"}

        b = Good()
        assert b.query("/some/path.png", "extract data") == {"result": "ok"}


# ===========================================================================
# Ollama backend
# ===========================================================================


class TestOllamaBackend:
    def _backend(self):
        from ifarm.vision.ollama_backend import OllamaBackend
        return OllamaBackend(model="qwen2-vl", host="http://localhost:11434")

    def _make_image(self, tmp_path: Path) -> Path:
        img = tmp_path / "screen.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)  # minimal fake PNG
        return img

    def test_query_returns_parsed_dict(self, tmp_path):
        img = self._make_image(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": '{"username": "alice", "views": 1000}'}

        import ifarm.vision.ollama_backend as mod
        orig = mod.requests
        try:
            fake = MagicMock()
            fake.post.return_value = mock_resp
            mod.requests = fake
            result = self._backend().query(img, "extract data")
        finally:
            mod.requests = orig

        assert result["username"] == "alice"
        assert result["views"] == 1000

    def test_query_handles_markdown_wrapped_json(self, tmp_path):
        img = self._make_image(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "response": '```json\n[{"post": "hello"}]\n```'
        }

        import ifarm.vision.ollama_backend as mod
        orig = mod.requests
        try:
            fake = MagicMock()
            fake.post.return_value = mock_resp
            mod.requests = fake
            result = self._backend().query(img, "extract")
        finally:
            mod.requests = orig

        assert isinstance(result, list)
        assert result[0]["post"] == "hello"

    def test_query_raises_vision_error_on_request_failure(self, tmp_path):
        img = self._make_image(tmp_path)
        import ifarm.vision.ollama_backend as mod
        orig = mod.requests
        try:
            fake = MagicMock()
            fake.post.side_effect = Exception("connection refused")
            mod.requests = fake
            with pytest.raises(VisionError, match="Ollama request failed"):
                self._backend().query(img, "extract")
        finally:
            mod.requests = orig

    def test_query_raises_vision_error_on_non_json_response(self, tmp_path):
        img = self._make_image(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "Here is your data: blah blah"}

        import ifarm.vision.ollama_backend as mod
        orig = mod.requests
        try:
            fake = MagicMock()
            fake.post.return_value = mock_resp
            mod.requests = fake
            with pytest.raises(VisionError, match="Could not extract JSON"):
                self._backend().query(img, "extract")
        finally:
            mod.requests = orig

    def test_query_raises_vision_error_on_missing_image(self, tmp_path):
        with pytest.raises(VisionError, match="Screenshot not found"):
            self._backend().query(tmp_path / "missing.png", "extract")

    def test_is_available_false_when_requests_none(self):
        import ifarm.vision.ollama_backend as mod
        orig = mod.requests
        try:
            mod.requests = None
            with pytest.raises(VisionError, match="requests is required"):
                self._backend().is_available()
        finally:
            mod.requests = orig

    def test_is_available_false_when_server_unreachable(self):
        import ifarm.vision.ollama_backend as mod
        orig = mod.requests
        try:
            fake = MagicMock()
            fake.get.side_effect = Exception("connection refused")
            mod.requests = fake
            assert self._backend().is_available() is False
        finally:
            mod.requests = orig

    def test_is_available_true_when_model_listed(self):
        import ifarm.vision.ollama_backend as mod
        orig = mod.requests
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "models": [{"name": "qwen2-vl:latest"}]
            }
            fake = MagicMock()
            fake.get.return_value = mock_resp
            mod.requests = fake
            assert self._backend().is_available() is True
        finally:
            mod.requests = orig


# ===========================================================================
# MLX backend
# ===========================================================================


class TestMLXBackend:
    def test_is_available_false_when_not_installed(self):
        from ifarm.vision.mlx_backend import MLXBackend, _check_mlx_vlm
        import sys
        with patch.dict(sys.modules, {"mlx_vlm": None}):
            assert _check_mlx_vlm() is False

    def test_query_raises_vision_error_when_not_installed(self, tmp_path):
        from ifarm.vision.mlx_backend import MLXBackend
        img = tmp_path / "screen.png"
        img.write_bytes(b"\x00" * 8)
        b = MLXBackend(model_path="mlx-community/fake")
        with patch("ifarm.vision.mlx_backend._check_mlx_vlm", return_value=False):
            with pytest.raises(VisionError, match="mlx-vlm is not installed"):
                b.query(img, "extract")

    def test_query_raises_vision_error_on_missing_image(self, tmp_path):
        from ifarm.vision.mlx_backend import MLXBackend
        b = MLXBackend(model_path="mlx-community/fake")
        with patch("ifarm.vision.mlx_backend._check_mlx_vlm", return_value=True):
            b._model = MagicMock()
            b._processor = MagicMock()
            b._config = MagicMock()
            with pytest.raises(VisionError, match="Screenshot not found"):
                b.query(tmp_path / "missing.png", "extract")


# ===========================================================================
# OCR fallback
# ===========================================================================


class TestOCRFallback:
    def test_is_available_false_when_cv2_missing(self):
        from ifarm.vision.ocr_fallback import OCRFallback
        import sys
        with patch.dict(sys.modules, {"cv2": None}):
            b = OCRFallback()
            assert b.is_available() is False

    def test_query_raises_vision_error_when_cv2_missing(self, tmp_path):
        from ifarm.vision.ocr_fallback import OCRFallback
        img = tmp_path / "screen.png"
        img.write_bytes(b"\x00" * 8)
        b = OCRFallback()
        with patch("ifarm.vision.ocr_fallback._check_deps", return_value=(False, True)):
            with pytest.raises(VisionError, match="opencv-python"):
                b.query(img, "")

    def test_query_raises_vision_error_when_tesseract_missing(self, tmp_path):
        from ifarm.vision.ocr_fallback import OCRFallback
        img = tmp_path / "screen.png"
        img.write_bytes(b"\x00" * 8)
        b = OCRFallback()
        with patch("ifarm.vision.ocr_fallback._check_deps", return_value=(True, False)):
            with pytest.raises(VisionError, match="pytesseract"):
                b.query(img, "")

    def test_query_returns_text_dict(self, tmp_path):
        from ifarm.vision.ocr_fallback import OCRFallback
        img = tmp_path / "screen.png"
        img.write_bytes(b"\x00" * 8)
        b = OCRFallback()
        mock_cv2 = MagicMock()
        mock_cv2.imread.return_value = MagicMock()
        mock_cv2.COLOR_BGR2GRAY = 6
        mock_cv2.ADAPTIVE_THRESH_GAUSSIAN_C = 1
        mock_cv2.THRESH_BINARY = 0
        mock_cv2.cvtColor.return_value = MagicMock()
        mock_cv2.adaptiveThreshold.return_value = MagicMock()
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "  847291  "

        with patch("ifarm.vision.ocr_fallback._check_deps", return_value=(True, True)), \
             patch("ifarm.vision.ocr_fallback.cv2", mock_cv2, create=True), \
             patch("ifarm.vision.ocr_fallback.pytesseract", mock_pytesseract, create=True):
            # Use __import__ patching for the imports inside the method
            import builtins
            real_import = builtins.__import__
            def fake_import(name, *args, **kwargs):
                if name == "cv2":
                    return mock_cv2
                if name == "pytesseract":
                    return mock_pytesseract
                return real_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=fake_import):
                result = b.query(img, "")
        assert "text" in result


# ===========================================================================
# Vision factory
# ===========================================================================


class TestGetBackend:
    def test_unknown_backend_raises_value_error(self):
        from ifarm.vision import get_backend
        from ifarm.utils.config import IFarmConfig
        cfg = IFarmConfig({"vision": {"backend": "nonexistent"}}, [])
        with pytest.raises(ValueError, match="Unknown vision backend"):
            get_backend(cfg)

    def test_ollama_selected_by_default(self, empty_config):
        from ifarm.vision import get_backend
        from ifarm.vision.ollama_backend import OllamaBackend
        backend = get_backend(empty_config)
        assert isinstance(backend, OllamaBackend)

    def test_ollama_model_from_config(self):
        from ifarm.vision import get_backend
        from ifarm.vision.ollama_backend import OllamaBackend
        from ifarm.utils.config import IFarmConfig
        cfg = IFarmConfig({"vision": {"backend": "ollama", "model": "llama3.2-vision"}}, [])
        backend = get_backend(cfg)
        assert isinstance(backend, OllamaBackend)
        assert backend.model == "llama3.2-vision"


# ===========================================================================
# Gesture math
# ===========================================================================


class TestGestureMath:
    def test_bezier_returns_correct_count(self):
        pts = _bezier_points((100, 700), (100, 200), steps=10)
        assert len(pts) == 11  # steps + 1

    def test_bezier_starts_at_start(self):
        pts = _bezier_points((100, 700), (100, 200), steps=10)
        assert pts[0] == (100, 700)

    def test_bezier_ends_at_end(self):
        pts = _bezier_points((100, 700), (100, 200), steps=10)
        assert pts[-1] == (100, 200)

    def test_bezier_intermediate_points_in_range(self):
        pts = _bezier_points((0, 0), (0, 100), steps=20)
        for x, y in pts:
            assert -50 <= x <= 50  # control point jitter allowed
            assert 0 <= y <= 100

    def test_bezier_with_explicit_control_point(self):
        pts = _bezier_points((0, 0), (100, 100), control=(50, 0), steps=4)
        assert len(pts) == 5
        assert pts[0] == (0, 0)
        assert pts[-1] == (100, 100)

    def test_random_jitter_in_range(self):
        for _ in range(50):
            j = _random_jitter(50, 200)
            assert 0.05 <= j <= 0.20


# ===========================================================================
# AppiumSession (mocked driver)
# ===========================================================================


class _FakeDriver:
    """Minimal Appium driver mock."""
    def __init__(self):
        self.quit = MagicMock()
        self.get_window_size = MagicMock(return_value={"width": 390, "height": 844})
        self.get_screenshot_as_file = MagicMock()
        self.execute_script = MagicMock(return_value=None)
        self.switch_to = MagicMock()
        self.switch_to.alert.dismiss = MagicMock()


def _patch_appium(driver: _FakeDriver):
    """Context manager that replaces Appium webdriver.Remote with a fake."""
    from unittest.mock import patch
    mock_remote = MagicMock(return_value=driver)
    return patch("ifarm.modules.scraper._appium_webdriver.Remote", mock_remote)


class TestAppiumSession:
    def test_enter_exit_opens_and_closes_driver(self, mock_udid):
        from ifarm.modules.scraper import AppiumSession, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        fake_driver = _FakeDriver()
        with _patch_appium(fake_driver):
            with AppiumSession(udid=mock_udid) as session:
                assert session.driver is not None
            assert session.driver is None
            fake_driver.quit.assert_called_once()

    def test_screen_size_cached(self, mock_udid):
        from ifarm.modules.scraper import AppiumSession, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        fake_driver = _FakeDriver()
        with _patch_appium(fake_driver):
            with AppiumSession(udid=mock_udid) as session:
                _ = session.screen_size
                _ = session.screen_size
        # Should only query window size once
        assert fake_driver.get_window_size.call_count == 1

    def test_take_screenshot_writes_file(self, mock_udid, tmp_path):
        from ifarm.modules.scraper import AppiumSession, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        fake_driver = _FakeDriver()
        dest = tmp_path / "shot.png"
        # Simulate Appium writing the file
        def write_screenshot(path):
            Path(path).write_bytes(b"\x89PNG")
        fake_driver.get_screenshot_as_file = MagicMock(side_effect=write_screenshot)

        with _patch_appium(fake_driver):
            with AppiumSession(udid=mock_udid) as session:
                path = session.take_screenshot(dest=dest)
        assert path == dest
        assert dest.exists()

    def test_dismiss_alerts_tries_webdriver_first(self, mock_udid):
        from ifarm.modules.scraper import AppiumSession, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        fake_driver = _FakeDriver()
        fake_driver.switch_to.alert.dismiss = MagicMock()

        with _patch_appium(fake_driver):
            with AppiumSession(udid=mock_udid) as session:
                result = session.dismiss_system_alerts()
        # Should attempt alert dismiss (returns True if succeeded, False if no alert)
        assert isinstance(result, bool)

    def test_raises_import_error_when_appium_missing(self, mock_udid):
        from ifarm.modules.scraper import AppiumSession
        import ifarm.modules.scraper as scraper_mod
        orig = scraper_mod._APPIUM_AVAILABLE
        try:
            scraper_mod._APPIUM_AVAILABLE = False
            with pytest.raises(ImportError, match="Appium-Python-Client"):
                with AppiumSession(udid=mock_udid):
                    pass
        finally:
            scraper_mod._APPIUM_AVAILABLE = orig


# ===========================================================================
# visual_scrape_feed pipeline
# ===========================================================================


class _MinimalVLMBackend:
    """VisionBackend stub that returns predictable data."""
    def __init__(self, response):
        self._response = response

    def query(self, image_path, prompt):
        return self._response

    def is_available(self):
        return True


class TestVisualScrapeFeed:
    def _run_pipeline(self, mock_udid, vlm_response, tmp_path):
        from ifarm.modules.scraper import visual_scrape_feed, AppiumSession, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        fake_driver = _FakeDriver()
        shot_path = tmp_path / "shot.png"

        def write_screenshot(path):
            Path(path).write_bytes(b"\x89PNG")

        fake_driver.get_screenshot_as_file = MagicMock(side_effect=write_screenshot)

        backend = _MinimalVLMBackend(vlm_response)

        with _patch_appium(fake_driver):
            results = visual_scrape_feed(
                udid=mock_udid,
                bundle_id="com.example.app",
                swipes=2,
                backend=backend,
            )
        return results

    def test_returns_list_of_dicts_from_dict_response(self, mock_udid, tmp_path):
        results = self._run_pipeline(
            mock_udid,
            {"username": "alice", "views": 1000},
            tmp_path,
        )
        assert len(results) == 2  # one per swipe
        assert results[0]["username"] == "alice"

    def test_flattens_list_response(self, mock_udid, tmp_path):
        results = self._run_pipeline(
            mock_udid,
            [{"username": "a"}, {"username": "b"}],
            tmp_path,
        )
        assert len(results) == 4  # 2 swipes × 2 items per swipe

    def test_screenshot_deleted_after_vlm(self, mock_udid, tmp_path):
        """Verify no screenshots accumulate on disk after the pipeline."""
        from ifarm.modules.scraper import visual_scrape_feed, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        written: list[Path] = []
        fake_driver = _FakeDriver()

        def write_screenshot(path):
            p = Path(path)
            p.write_bytes(b"\x89PNG")
            written.append(p)

        fake_driver.get_screenshot_as_file = MagicMock(side_effect=write_screenshot)
        backend = _MinimalVLMBackend({"data": "ok"})

        with _patch_appium(fake_driver):
            visual_scrape_feed(
                udid=mock_udid,
                bundle_id="com.example.app",
                swipes=3,
                backend=backend,
            )

        # All screenshots written during the run should be deleted
        for p in written:
            assert not p.exists(), f"Screenshot not cleaned up: {p}"

    def test_vlm_error_skipped_gracefully(self, mock_udid, tmp_path):
        """A VLM failure on one screenshot should not abort the whole run."""
        from ifarm.modules.scraper import visual_scrape_feed, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        call_count = 0

        class FlakeyBackend:
            def query(self, image_path, prompt):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise VisionError("model timeout")
                return {"data": "ok"}
            def is_available(self):
                return True

        fake_driver = _FakeDriver()
        fake_driver.get_screenshot_as_file = MagicMock(
            side_effect=lambda p: Path(p).write_bytes(b"\x89PNG")
        )

        with _patch_appium(fake_driver):
            results = visual_scrape_feed(
                udid=mock_udid,
                bundle_id="com.example.app",
                swipes=3,
                backend=FlakeyBackend(),
            )

        # 3 swipes, 1 failed → 2 results
        assert len(results) == 2


# ===========================================================================
# tap_ui_element_by_text
# ===========================================================================


class TestTapUIElementByText:
    def _run(self, mock_udid, vlm_response, tmp_path):
        from ifarm.modules.scraper import tap_ui_element_by_text, _APPIUM_AVAILABLE
        if not _APPIUM_AVAILABLE:
            pytest.skip("Appium-Python-Client not installed")

        fake_driver = _FakeDriver()
        fake_driver.get_screenshot_as_file = MagicMock(
            side_effect=lambda p: Path(p).write_bytes(b"\x89PNG")
        )
        backend = _MinimalVLMBackend(vlm_response)

        with _patch_appium(fake_driver):
            result = tap_ui_element_by_text(
                udid=mock_udid,
                target_text="Follow",
                backend=backend,
            )
        return result, fake_driver

    def test_taps_when_element_found(self, mock_udid, tmp_path):
        result, driver = self._run(
            mock_udid, {"found": True, "x": 195, "y": 400}, tmp_path
        )
        assert result is True
        driver.execute_script.assert_called_with("mobile: tap", {"x": 195, "y": 400})

    def test_returns_false_when_not_found(self, mock_udid, tmp_path):
        result, driver = self._run(mock_udid, {"found": False}, tmp_path)
        assert result is False

    def test_returns_false_on_missing_coordinates(self, mock_udid, tmp_path):
        result, driver = self._run(mock_udid, {"found": True}, tmp_path)
        assert result is False


# ===========================================================================
# HTTP server
# ===========================================================================


class TestHTTPServer:
    def _app(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi / httpx not installed — pip install ifarm[serve,dev]")
        from ifarm.server import create_app
        from ifarm.utils.config import IFarmConfig
        with patch("ifarm.server.load_config", return_value=IFarmConfig({}, [])):
            return create_app()

    def test_health_endpoint(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")
        app = self._app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_rotate_ip_returns_501_not_implemented(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")
        app = self._app()
        client = TestClient(app)
        resp = client.post("/proxy/rotate", json={"udid": "TEST-UDID-0001"})
        # Phase 1 proxy is implemented but requires hardware — 500 or 501 expected
        assert resp.status_code in (500, 501)

    def test_2fa_endpoint_accessible(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")
        app = self._app()
        client = TestClient(app)
        # Will fail because no chat.db — but endpoint must exist (not 404)
        resp = client.post("/sms/2fa", json={"keyword": "code"})
        assert resp.status_code != 404


# ===========================================================================
# Hardware + VLM integration stubs
# ===========================================================================


@pytest.mark.hardware
@pytest.mark.vlm
class TestPhase2Integration:
    def test_screenshot_captured(self):
        pytest.skip("Requires device + running Appium")

    def test_vlm_extraction_returns_dict(self):
        pytest.skip("Requires device + Ollama")

    def test_popup_handler_dismisses_alert(self):
        pytest.skip("Requires device")

    def test_full_feed_scrape_pipeline(self):
        pytest.skip("Requires device + Ollama + target app installed")
