"""Apple MLX vision backend.

Runs multimodal inference directly on Apple Silicon via the MLX framework —
no Ollama server required. Latency is typically lower than Ollama on M-series
chips for smaller models.

Setup:
    pip install mlx-vlm
    # Model downloads automatically on first use.

Requires: pip install mlx-vlm  (not included in ifarm[automation] because
it is Apple Silicon only and pulls in large model weights)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ifarm.exceptions import VisionError
from ifarm.vision._json_utils import parse_vlm_response
from ifarm.vision.base import VisionBackend
from ifarm.utils.logger import get_logger

_log = get_logger(__name__)


def _check_mlx_vlm() -> bool:
    """Return True if mlx_vlm is importable."""
    try:
        import mlx_vlm  # noqa: F401
        return True
    except ImportError:
        return False


class MLXBackend(VisionBackend):
    """Run a multimodal model locally via Apple's MLX framework.

    Lazy-loads the model on first call to avoid startup overhead when
    other backends are preferred.

    Args:
        model_path: Local directory or HuggingFace repo ID in MLX format
            (e.g. "mlx-community/Qwen2-VL-7B-Instruct-4bit").
        max_tokens: Maximum tokens to generate per response.
        resize_shape: Optional (width, height) to resize images before inference.
            Reduces VRAM usage for high-res screenshots.
    """

    def __init__(
        self,
        model_path: str = "mlx-community/Qwen2-VL-7B-Instruct-4bit",
        max_tokens: int = 512,
        resize_shape: tuple[int, int] | None = (768, 768),
    ):
        self.model_path = model_path
        self.max_tokens = max_tokens
        self.resize_shape = resize_shape
        self._model: Any = None
        self._processor: Any = None
        self._config: Any = None
        self._generate: Any = None
        self._apply_chat_template: Any = None

    def is_available(self) -> bool:
        """Return True if mlx_vlm is importable (Apple Silicon assumed)."""
        return _check_mlx_vlm()

    def _load(self) -> None:
        """Lazy-load model and processor on first use."""
        if self._model is not None:
            return
        if not _check_mlx_vlm():
            raise VisionError(
                "mlx-vlm is not installed. Install with: pip install mlx-vlm\n"
                "Note: requires Apple Silicon."
            )
        from mlx_vlm import load, generate
        from mlx_vlm.utils import load_config
        from mlx_vlm.prompt_utils import apply_chat_template

        _log.info("Loading MLX model", extra={"model_path": self.model_path})
        self._model, self._processor = load(self.model_path)
        self._config = load_config(self.model_path)
        self._generate = generate
        self._apply_chat_template = apply_chat_template
        _log.info("MLX model loaded")

    def query(self, image_path: Path | str, prompt: str) -> dict | list:
        """Run on-device inference and return parsed JSON.

        Args:
            image_path: Path to a PNG/JPEG screenshot.
            prompt: Extraction instruction requesting JSON output.

        Returns:
            Parsed dict or list.

        Raises:
            VisionError: If mlx-vlm is not installed, inference fails,
                or the response is not parseable JSON.
        """
        self._load()

        image_path = Path(image_path)
        if not image_path.exists():
            raise VisionError(f"Screenshot not found: {image_path}")

        try:
            formatted_prompt = self._apply_chat_template(
                self._processor,
                self._config,
                prompt,
                num_images=1,
            )
            output = self._generate(
                self._model,
                self._processor,
                str(image_path),
                formatted_prompt,
                max_tokens=self.max_tokens,
                verbose=False,
            )
        except Exception as e:
            raise VisionError(f"MLX inference failed: {e}") from e

        _log.info(
            "MLX inference complete",
            extra={"model": self.model_path, "chars": len(output)},
        )
        return parse_vlm_response(output, context=f"mlx/{self.model_path}")
