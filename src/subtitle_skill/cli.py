"""JSON process boundary: `contract --json`, `doctor --json`, `run - --json`."""
from __future__ import annotations

import argparse
import json
import sys

from .contract import build_contract
from .doctor import build_doctor_report
from .errors import SubtitleSkillError
from .installer import install as install_skill
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


def _cmd_install(args: argparse.Namespace) -> int:
    results = install_skill(
        claude=args.claude,
        cursor=args.cursor,
        codex=args.codex,
        project=args.project,
        custom_dir=args.dir,
        all_=args.all,
        uninstall=args.uninstall,
    )
    failed = [r for r in results if r.action == "failed"]
    if args.json:
        _print_json({"results": [r.to_dict() for r in results], "ok": not failed})
    else:
        verb = "removed" if args.uninstall else "installed SKILL.md to"
        for r in results:
            if r.action == "failed":
                print(f"failed for {r.label} ({r.directory}): {r.error}", file=sys.stderr)
            else:
                print(f"{verb} {r.label}: {r.directory}")
        if not args.uninstall:
            print(
                "\nNote: this only places SKILL.md for agent discovery. "
                "The `subtitle-skill` command itself must separately be on PATH "
                "(`pip install subtitle-skill`, or `pip install -e .` from a checkout)."
            )
    return 1 if failed else 0


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

    p_install = sub.add_parser("install", help="place SKILL.md for agent discovery (Claude Code / Cursor / Codex)")
    p_install.add_argument("--claude", action="store_true", help="~/.claude/skills/subtitle-skill")
    p_install.add_argument("--cursor", action="store_true", help="~/.cursor/skills/subtitle-skill")
    p_install.add_argument("--codex", action="store_true", help="~/.codex/skills/subtitle-skill")
    p_install.add_argument("--all", action="store_true", help="claude + cursor + codex")
    p_install.add_argument("--project", action="store_true", help="./.claude/skills/subtitle-skill in the current directory")
    p_install.add_argument("--dir", help="custom parent directory")
    p_install.add_argument("--uninstall", action="store_true", help="remove from the selected targets")
    p_install.add_argument("--json", action="store_true", help="output as JSON")
    p_install.set_defaults(func=_cmd_install)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
