"""JSON process boundary: `contract --json`, `doctor --json`, `run - --json`."""
from __future__ import annotations

import argparse
import json
import sys

from .contract import build_contract
from .doctor import build_doctor_report
from .errors import SubtitleSkillError
from .operations import execute
from .provenance import canonical_json


def _print_json(data: dict) -> None:
    print(canonical_json(data))


def _cmd_contract(_args: argparse.Namespace) -> int:
    _print_json(build_contract())
    return 0


def _cmd_doctor(_args: argparse.Namespace) -> int:
    report = build_doctor_report()
    _print_json(report)
    return 0 if report["healthy"] else 1


def _cmd_run(args: argparse.Namespace) -> int:
    if args.source == "-":
        raw = sys.stdin.read()
    else:
        with open(args.source, "r", encoding="utf-8") as f:
            raw = f.read()

    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        _print_json(
            {
                "status": "error",
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": f"request is not valid JSON: {exc}",
                    "retryable": False,
                },
            }
        )
        return 2

    try:
        response = execute(request)
    except SubtitleSkillError as exc:
        _print_json({"status": "error", "error": exc.to_dict()})
        return 1
    except Exception as exc:  # noqa: BLE001 - convert any unexpected failure to a typed envelope
        _print_json(
            {
                "status": "error",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(exc),
                    "retryable": False,
                },
            }
        )
        return 1

    _print_json(response)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subtitle-skill")
    sub = parser.add_subparsers(dest="command", required=True)

    p_contract = sub.add_parser("contract", help="print the machine-readable capability contract")
    p_contract.add_argument("--json", action="store_true", help="output as JSON (default)")
    p_contract.set_defaults(func=_cmd_contract)

    p_doctor = sub.add_parser("doctor", help="print dependency/health status")
    p_doctor.add_argument("--json", action="store_true", help="output as JSON (default)")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_run = sub.add_parser("run", help="execute an operation from a JSON request")
    p_run.add_argument("source", help="'-' for stdin, or a path to a JSON request file")
    p_run.add_argument("--json", action="store_true", help="output as JSON (default)")
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
