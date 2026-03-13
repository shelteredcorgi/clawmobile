"""Abstract VisionBackend interface.

Implement this class to plug any VLM or OCR engine into iFarm's
visual scraping pipeline without touching any other code.

Example — minimal custom backend:

    from ifarm.vision.base import VisionBackend

    class MyBackend(VisionBackend):
        def is_available(self) -> bool:
            return True  # or check subprocess / HTTP

        def query(self, image_path, prompt) -> dict:
            raw = my_model.infer(image_path, prompt)
            return json.loads(raw)

Then register it in the factory in ifarm/vision/__init__.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class VisionBackend(ABC):
    """Contract that all vision/language backends must satisfy.

    iFarm passes a screenshot path and a plain-English extraction prompt.
    The backend is responsible for calling its underlying model and returning
    a parsed Python dict. JSON parsing, error handling, and retries are the
    backend's responsibility — callers expect a dict or a VisionError.
    """

    @abstractmethod
    def query(self, image_path: Path | str, prompt: str) -> dict:
        """Extract structured data from an image.

        Args:
            image_path: Path to the screenshot to analyze.
            prompt: Plain-English instruction. For reliable downstream parsing,
                prompts should request strict JSON output, e.g.:
                "Extract username and view count. Output JSON only."

        Returns:
            Parsed dict from the model response.

        Raises:
            VisionError: If the backend fails, times out, or returns
                non-parseable output.
        """
        ...

    def is_available(self) -> bool:
        """Return True if this backend's dependencies are installed and reachable.

        Override to add a lightweight health check (e.g. HTTP ping to Ollama).
        Default returns True — subclasses should override when possible.
        """
        return True
