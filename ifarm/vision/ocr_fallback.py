"""OpenCV + Tesseract OCR fallback backend.

No GPU or Ollama server required. Best for lightweight numeric/text extraction
(prices, counts, codes) when full VLM inference is overkill.

Setup:
    brew install tesseract
    pip install opencv-python pytesseract

Note: The prompt parameter is ignored by this backend — OCR extracts all
visible text from the image. For structured/selective extraction use
OllamaBackend or MLXBackend.
"""
from __future__ import annotations

from pathlib import Path

from ifarm.exceptions import VisionError
from ifarm.vision.base import VisionBackend
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)


def _check_deps() -> tuple[bool, bool]:
    """Return (cv2_available, tesseract_available)."""
    try:
        import cv2  # noqa: F401
        cv2_ok = True
    except ImportError:
        cv2_ok = False

    try:
        import pytesseract  # noqa: F401
        tess_ok = True
    except ImportError:
        tess_ok = False

    return cv2_ok, tess_ok


class OCRFallback(VisionBackend):
    """Extract text from screenshots using Tesseract OCR.

    Args:
        lang: Tesseract language code (default "eng").
        config: Extra Tesseract config flags (e.g. "--psm 6").
        preprocess: If True, apply adaptive thresholding before OCR to improve
            accuracy on low-contrast screenshots.
    """

    def __init__(
        self,
        lang: str = "eng",
        config: str = "--psm 6",
        preprocess: bool = True,
    ):
        self.lang = lang
        self.config = config
        self.preprocess = preprocess

    def is_available(self) -> bool:
        """Return True if both opencv-python and pytesseract are installed."""
        cv2_ok, tess_ok = _check_deps()
        return cv2_ok and tess_ok

    def query(self, image_path: Path | str, prompt: str) -> dict:
        """Run Tesseract OCR and return all extracted text.

        The prompt parameter is ignored — use a VLM backend for selective
        structured extraction.

        Args:
            image_path: Path to a PNG/JPEG screenshot.
            prompt: Ignored by this backend.

        Returns:
            {"text": "<raw extracted string>"}

        Raises:
            VisionError: If dependencies are missing or OCR fails.
        """
        cv2_ok, tess_ok = _check_deps()
        if not cv2_ok:
            raise VisionError(
                "opencv-python is not installed. "
                "Install with: pip install opencv-python"
            )
        if not tess_ok:
            raise VisionError(
                "pytesseract is not installed. "
                "Install with: pip install pytesseract\n"
                "Also install Tesseract: brew install tesseract"
            )

        import cv2
        import pytesseract

        image_path = Path(image_path)
        if not image_path.exists():
            raise VisionError(f"Screenshot not found: {image_path}")

        try:
            img = cv2.imread(str(image_path))
            if img is None:
                raise VisionError(f"cv2 could not read image: {image_path}")

            if self.preprocess:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                img = cv2.adaptiveThreshold(
                    gray, 255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY, 11, 2,
                )

            text = pytesseract.image_to_string(
                img,
                lang=self.lang,
                config=self.config,
            ).strip()

            _log.info("OCR complete", extra={"chars": len(text)})
            return {"text": text}

        except VisionError:
            raise
        except Exception as e:
            raise VisionError(f"OCR failed: {e}") from e

    def extract_numbers(self, image_path: Path | str) -> list[str]:
        """Extract only numeric tokens from an image.

        Args:
            image_path: Path to the image.

        Returns:
            List of numeric strings found in the image (may include decimals).

        Raises:
            VisionError: If dependencies are missing or OCR fails.
        """
        import re

        result = self.query(image_path, prompt="")
        tokens = re.findall(r"\d[\d,\.]*", result["text"])
        return tokens
