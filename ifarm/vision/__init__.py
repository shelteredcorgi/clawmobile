"""Vision backends for iFarm's visual scraping pipeline.

All backends implement the VisionBackend interface from ifarm.vision.base.
Use get_backend() to select one from config rather than instantiating directly.

Adding a new backend:
  1. Create a file here (e.g. my_backend.py) subclassing VisionBackend
  2. Add it to the BACKEND_REGISTRY below
  3. Document the config key in config/ifarm.example.toml
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ifarm.vision.base import VisionBackend

if TYPE_CHECKING:
    from ifarm.utils.config import IFarmConfig

# Registry maps config key → backend class (lazy import to avoid hard deps)
_BACKEND_REGISTRY: dict[str, str] = {
    "ollama": "ifarm.vision.ollama_backend.OllamaBackend",
    "mlx": "ifarm.vision.mlx_backend.MLXBackend",
    "ocr": "ifarm.vision.ocr_fallback.OCRFallback",
}

# Probe order for backend = "auto"
_AUTO_ORDER = ["ollama", "mlx", "ocr"]


def _build_backend(backend_key: str, vision_cfg: dict) -> VisionBackend:
    """Instantiate a backend by registry key, forwarding matching config keys."""
    import importlib

    module_path, class_name = _BACKEND_REGISTRY[backend_key].rsplit(".", 1)
    cls = getattr(importlib.import_module(module_path), class_name)

    kwargs: dict = {}
    if backend_key == "ollama":
        if "model" in vision_cfg:
            kwargs["model"] = vision_cfg["model"]
        if "host" in vision_cfg:
            kwargs["host"] = vision_cfg["host"]
    elif backend_key == "mlx":
        if "model_path" in vision_cfg:
            kwargs["model_path"] = vision_cfg["model_path"]

    return cls(**kwargs)


def get_backend(config: "IFarmConfig") -> VisionBackend:
    """Instantiate the VisionBackend selected in ifarm.toml [vision] section.

    Set ``backend = "auto"`` to probe ollama → mlx → ocr in order and return
    the first available one. Useful during initial setup when you aren't sure
    which backends are installed.

    Args:
        config: Loaded IFarmConfig instance.

    Returns:
        An instantiated VisionBackend subclass.

    Raises:
        ValueError: If the configured backend key is not registered.
        RuntimeError: If backend is "auto" and no backend is available.
    """
    vision_cfg = config.vision
    backend_key = vision_cfg.get("backend", "ollama")

    if backend_key == "auto":
        for key in _AUTO_ORDER:
            backend = _build_backend(key, vision_cfg)
            if backend.is_available():
                return backend
        raise RuntimeError(
            "backend='auto' found no available vision backend. "
            "Options: Ollama (brew install ollama && ollama pull qwen2-vl), "
            "MLX (pip install mlx-vlm, Apple Silicon only), "
            "or OCR (pip install ifarm[automation] && brew install tesseract)."
        )

    if backend_key not in _BACKEND_REGISTRY:
        raise ValueError(
            f"Unknown vision backend '{backend_key}'. "
            f"Available: {list(_BACKEND_REGISTRY) + ['auto']}"
        )

    return _build_backend(backend_key, vision_cfg)


__all__ = ["VisionBackend", "get_backend"]
