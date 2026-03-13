# Contributing to iFarm

Thank you for your interest in contributing. This document covers the project
structure and the common extension points.

## Development Setup

```bash
git clone <repo-url>
cd ifarm
make install-automation   # or install-all for everything
make test             # verify the baseline passes
```

## Running Tests

```bash
make test             # unit tests only (no hardware, no VLM)
make test-hardware    # requires USB-connected iPhone
make test-vlm         # requires Ollama running locally
make test-all         # everything
```

Tests requiring hardware use `@pytest.mark.hardware`. Tests requiring a
running VLM use `@pytest.mark.vlm`. Both are skipped automatically in CI.

## Project Layout

```
ifarm/
├── controller.py        # IFarmController — public API facade
├── swarm.py             # IFarmSwarmController — multi-device
├── modules/
│   ├── proxy.py         # Network — cellular routing
│   ├── sms.py           # Network — SMS/2FA extraction
│   ├── scraper.py       # Automation — Appium + VLM pipeline
│   └── hardware.py      # Hardware — GPS spoof, camera inject
├── vision/
│   ├── base.py          # VisionBackend abstract class
│   ├── __init__.py      # get_backend() factory + "auto" detection
│   ├── ollama_backend.py
│   ├── mlx_backend.py
│   └── ocr_fallback.py
├── utils/
│   ├── config.py        # ifarm.toml + devices.json loader
│   ├── device.py        # libimobiledevice wrappers
│   └── logger.py        # structured JSON logger
├── diagnostics.py       # ifarm doctor health checks
├── server.py            # FastAPI HTTP server
└── cli.py               # CLI entry point
```

## Adding a Vision Backend

1. Create `ifarm/vision/my_backend.py` subclassing `VisionBackend`:

   ```python
   from pathlib import Path
   from ifarm.vision.base import VisionBackend
   from ifarm.exceptions import VisionError

   class MyBackend(VisionBackend):
       def is_available(self) -> bool:
           # Return True if your service/lib is reachable
           return True

       def query(self, image_path: Path | str, prompt: str) -> dict | list:
           # Call your model, parse JSON, return structured data
           raise NotImplementedError
   ```

2. Register it in `ifarm/vision/__init__.py`:

   ```python
   _BACKEND_REGISTRY["my_backend"] = "ifarm.vision.my_backend.MyBackend"
   ```

3. Document the config key in `config/ifarm.example.toml`.

4. Add tests in `tests/test_phase2.py` (automation tests).

## Adding a Hardware Module

1. Implement the feature in `ifarm/modules/hardware.py`.
2. Add a thin delegate method in `ifarm/controller.py`.
3. Expose it via `ifarm/server.py` (new endpoint or extend an existing one).
4. Add a diagnostic check in `ifarm/diagnostics.py` if a new binary is required.
5. Add tests in `tests/test_phase3.py` (hardware tests).

## Code Style

iFarm uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
make lint       # check
make lint-fix   # auto-fix
make format     # reformat
```

Configuration lives in `pyproject.toml` under `[tool.ruff]`.

General guidelines:
- All public functions/methods must have Google-style docstrings.
- Use type hints throughout.
- No hardcoded paths — resolve everything via `ifarm/utils/config.py` or `Path.home()`.
- Wrap all subprocess calls in `try/except` and log failures via `ifarm/utils/logger.py`.

## Commit Style

```
feat(proxy): implement cycle_airplane_mode via libimobiledevice
fix(sms): handle NULL body rows in chat.db query
docs(vision): add OllamaBackend usage example
test(diagnostics): add run_checks integration tests
```

## Pull Request Guidelines

- One logical change per PR.
- New features must include tests. Bug fixes should include a regression test.
- Hardware-dependent tests must use `@pytest.mark.hardware`.
- Do not commit `config/devices.json` or `ifarm.toml` — both are gitignored.
- Target the `main` branch.

## License

By contributing you agree that your code will be released under the
[MIT License](LICENSE).
