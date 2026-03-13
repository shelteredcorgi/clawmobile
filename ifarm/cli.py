"""iFarm CLI entry point.

Commands:
    ifarm serve   [--port PORT] [--config PATH] [--host HOST]
    ifarm doctor  [--json] [--model MODEL]

Install the CLI:
    pip install -e .
Then run:
    ifarm serve
    ifarm doctor
"""
from __future__ import annotations

import argparse
import sys


def _cmd_doctor(args: argparse.Namespace) -> None:
    from ifarm.diagnostics import run_checks
    report = run_checks(ollama_model=args.model)

    if args.json:
        import json
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["overall"] != "not_ready" else 1)

    # Human-readable output
    overall = report["overall"]
    symbols = {"ok": "✓", "missing": "✗", "error": "!", "warning": "?"}
    colors = {
        "not_ready": "\033[91m",
        "network_ready": "\033[93m",
        "automation_ready": "\033[93m",
        "fully_ready": "\033[92m",
    }
    reset = "\033[0m"

    print(f"\niFarm Doctor  (v{report['version']})  —  {report['platform']} / Python {report['python']}")
    print(f"Overall: {colors.get(overall, '')}{overall}{reset}\n")

    current_phase = None
    for check in report["checks"]:
        phase = check["phase"]
        if phase != current_phase:
            current_phase = phase
            _phase_labels = {0: "foundation", 1: "network", 2: "automation", 3: "hardware"}
            key = _phase_labels.get(phase, f"phase{phase}")
            phase_status = report["phases"].get(key, "?")
            print(f"  [{key}: {phase_status}]")

        sym = symbols.get(check["status"], "?")
        color = "\033[92m" if check["status"] == "ok" else "\033[91m"
        print(f"    {color}{sym}{reset}  {check['name']}")
        if check["status"] != "ok":
            print(f"       → {check['detail']}")
            if check.get("fix"):
                print(f"       fix: {check['fix']}")

    missing = report["missing"]
    if missing:
        print(f"\n{len(missing)} check(s) need attention. Re-run after fixing.\n")
        sys.exit(1)
    else:
        print("\nAll checks passed.\n")
        sys.exit(0)


def _cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is required to run the server.\n"
            "Install with: pip install ifarm[serve]",
            file=sys.stderr,
        )
        sys.exit(1)

    from ifarm.server import create_app

    config_path = args.config or None
    app = create_app(config_path=config_path)

    print(f"iFarm server starting on http://{args.host}:{args.port}")
    print("OpenClaw skill endpoint: POST /scrape/feed")
    print("Press Ctrl+C to stop.\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ifarm",
        description="iFarm — iOS device orchestration for AI agents",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ifarm doctor
    doctor_p = sub.add_parser("doctor", help="Check all iFarm prerequisites")
    doctor_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    doctor_p.add_argument("--model", default="qwen2-vl", help="Ollama VLM model to check (default: qwen2-vl)")
    doctor_p.set_defaults(func=_cmd_doctor)

    # ifarm serve
    serve_p = sub.add_parser("serve", help="Start the iFarm HTTP server")
    serve_p.add_argument("--port", type=int, default=7420, help="Port (default 7420)")
    serve_p.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    serve_p.add_argument("--config", default=None, help="Path to ifarm.toml")
    serve_p.set_defaults(func=_cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
