"""Ollama VLM backend.

Queries a locally running Ollama server with a multimodal model.

Setup:
    brew install ollama
    ollama serve                    # start server (or: brew services start ollama)
    ollama pull qwen2-vl            # or: ollama pull llama3.2-vision

Requires: pip install ifarm[automation]  (pulls in requests)
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

try:
    import requests as requests
except ImportError:
    requests = None  # type: ignore[assignment]

from ifarm.exceptions import VisionError
from ifarm.vision._json_utils import parse_vlm_response
from ifarm.vision.base import VisionBackend
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)


class OllamaBackend(VisionBackend):
    """Send screenshots to a local Ollama multimodal model.

    Args:
        model: Ollama model name (e.g. "qwen2-vl", "llama3.2-vision").
        host: Ollama server base URL. Default http://localhost:11434.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        model: str = "qwen2-vl",
        host: str = "http://localhost:11434",
        timeout: int = 60,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def is_available(self) -> bool:
        """Return True if the Ollama server is reachable and the model is loaded.

        Raises:
            VisionError: If requests is not installed.
        """
        if requests is None:
            raise VisionError(
                "requests is required for OllamaBackend. "
                "Install with: pip install ifarm[automation]"
            )
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            # Check for exact match or model:tag prefix match
            return any(m == self.model or m.startswith(self.model + ":") for m in models)
        except Exception:
            return False

    def query(self, image_path: Path | str, prompt: str) -> dict | list:
        """Send image + prompt to Ollama and return parsed JSON.

        Args:
            image_path: Path to a PNG/JPEG screenshot.
            prompt: Extraction instruction requesting JSON output.

        Returns:
            Parsed dict or list from model response.

        Raises:
            VisionError: If the request fails, times out, or returns
                non-parseable output.
        """
        if requests is None:
            raise VisionError(
                "requests is required. Install with: pip install ifarm[automation]"
            )

        image_path = Path(image_path)
        if not image_path.exists():
            raise VisionError(f"Screenshot not found: {image_path}")

        image_b64 = base64.b64encode(image_path.read_bytes()).decode()

        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "format": "json",  # instructs Ollama to enforce JSON output mode
        }

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.host}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                break
            except Exception as e:
                last_exc = e
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    _log.warning(
                        "Ollama request failed, retrying",
                        extra={"attempt": attempt, "delay": delay, "error": str(e)},
                    )
                    time.sleep(delay)
        else:
            raise VisionError(
                f"Ollama request failed after {self.max_retries} attempts ({self.host}): {last_exc}"
            ) from last_exc

        raw = resp.json().get("response", "")
        _log.info(
            "Ollama response received",
            extra={"model": self.model, "chars": len(raw)},
        )

        return parse_vlm_response(raw, context=f"ollama/{self.model}")
