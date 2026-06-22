from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="depviz",
        description=(
            "Inspect, resolve, plan, lock, apply, verify, promote, and roll back "
            "dependency environments."
        ),
    )
    parser.add_argument(
        "--list-plugins", action="store_true", help="List backend plugins and exit."
    )
    subparsers = parser.add_subparsers(dest="command")
    _add_inspect_parser(subparsers)
    _add_resolve_parser(subparsers)
    _add_plan_parser(subparsers)
    _add_lock_parser(subparsers)
    _add_apply_parser(subparsers)
    _add_verify_parser(subparsers)
    _add_promote_parser(subparsers)
    _add_rollback_parser(subparsers)
    _add_status_parser(subparsers)
    _add_doctor_parser(subparsers)
    _add_gc_parser(subparsers)
    return parser


def _add_inspect_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("inspect", help="Inspect an approximate dependency graph.")
    parser.add_argument("manifest")
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--report",
        choices=["all", "blast", "weight", "why", "impact", "deps", "tree"],
        default="all",
    )
    parser.add_argument("--package")
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cache-file")
    parser.add_argument("--require-complete", action="store_true")


def _add_resolver_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--resolver", default="auto")
    parser.add_argument("--platform")
    parser.add_argument("--tool", choices=["micromamba", "mamba", "conda"], default="micromamba")
    parser.add_argument("--executable")
    parser.add_argument("--solver", choices=["classic", "libmamba"])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output-limit", type=int, default=32 * 1024 * 1024)
    parser.add_argument("--python", help="Python interpreter for Python backends.")
    parser.add_argument("--uv-executable", help="uv executable for Python backends.")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Select a pyproject optional dependency extra; may be repeated.",
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        help="Select a pyproject dependency group; may be repeated.",
    )


def _add_resolve_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("resolve", help="Resolve an exact desired environment.")
    parser.add_argument("manifest")
    _add_resolver_options(parser)
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=20)


def _add_plan_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("plan", help="Compare an exact prefix with a desired solve.")
    parser.add_argument("manifest")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--prefix", help="Existing Conda prefix to inspect.")
    target.add_argument("--empty", action="store_true", help="Plan creation of a new environment.")
    parser.add_argument("--inspector", default="auto")
    _add_resolver_options(parser)
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument(
        "--fail-on-policy",
        choices=["never", "error", "warning"],
        default="error",
    )


def _add_lock_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("lock", help="Create an exact backend lock from a resolution.")
    parser.add_argument("resolution")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--allow-weak-checksum",
        action="store_true",
        help="Allow legacy MD5-only Conda artifacts; unsafe and disabled by default.",
    )
    parser.add_argument("--json", action="store_true")


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tool", choices=["micromamba", "mamba", "conda"], default="micromamba")
    parser.add_argument("--executable")
    parser.add_argument("--solver", choices=["classic", "libmamba"])
    parser.add_argument("--python", help="Python interpreter for Python backends.")
    parser.add_argument("--uv-executable", help="uv executable for Python backends.")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output-limit", type=int, default=32 * 1024 * 1024)
    parser.add_argument("--lock-timeout", type=float, default=30.0)
    parser.add_argument(
        "--allow-weak-checksum",
        action="store_true",
        help="Allow reading legacy MD5-only Conda locks; unsafe and disabled by default.",
    )


def _add_apply_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "apply", help="Create an isolated candidate from an exact lock without re-solving."
    )
    parser.add_argument("lock")
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--driver", default="auto")
    parser.add_argument("--keep-failed", action="store_true")
    parser.add_argument("--json", action="store_true")
    _add_runtime_options(parser)


def _add_verify_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "verify", help="Verify an applied candidate against its exact lock."
    )
    parser.add_argument("lock")
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--verifier", default="auto")
    parser.add_argument(
        "--probe-command",
        action="append",
        default=[],
        help="Additional command to run without a shell; may be repeated.",
    )
    parser.add_argument(
        "--probe-import",
        action="append",
        default=[],
        help="Python module that must import successfully; may be repeated.",
    )
    parser.add_argument("--json", action="store_true")
    _add_runtime_options(parser)


def _add_switch_verification_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--verifier", default="auto")
    parser.add_argument(
        "--probe-command",
        action="append",
        default=[],
        help="Command that must pass immediately before switching; may be repeated.",
    )
    parser.add_argument(
        "--probe-import",
        action="append",
        default=[],
        help="Python module that must import immediately before switching; may be repeated.",
    )
    _add_runtime_options(parser)


def _add_promote_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "promote",
        help="Re-verify and atomically make a candidate current.",
    )
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--json", action="store_true")
    _add_switch_verification_options(parser)


def _add_rollback_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "rollback", help="Re-verify and atomically restore the previous candidate."
    )
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--json", action="store_true")
    _add_switch_verification_options(parser)


def _add_status_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("status", help="Show managed deployment state.")
    parser.add_argument("--deployment", required=True)
    parser.add_argument(
        "--deployment-kind",
        choices=[
            "managed-conda-deployment",
            "managed-python-deployment",
            "managed-conda-pip-deployment",
        ],
        default="managed-conda-deployment",
    )
    parser.add_argument("--json", action="store_true")


def _add_doctor_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("doctor", help="Validate plugins and managed deployment state.")
    parser.add_argument("--deployment")
    parser.add_argument(
        "--plugin",
        action="append",
        default=[],
        help="Check only the named plugin; may be repeated.",
    )
    parser.add_argument("--tool", choices=["micromamba", "mamba", "conda"], default="micromamba")
    parser.add_argument("--executable")
    parser.add_argument("--solver", choices=["classic", "libmamba"])
    parser.add_argument("--python", help="Python interpreter for Python backend checks.")
    parser.add_argument("--uv-executable", help="uv executable for Python backend checks.")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output-limit", type=int, default=1024 * 1024)
    parser.add_argument("--lock-timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")


def _add_gc_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "gc", help="Safely remove old non-current candidate directories."
    )
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--lock-timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "inspect",
        "resolve",
        "plan",
        "lock",
        "apply",
        "verify",
        "promote",
        "rollback",
        "status",
        "doctor",
        "gc",
    }
    root_only = {"-h", "--help", "--list-plugins"}
    if raw and raw[0] not in commands and raw[0] not in root_only:
        raw.insert(0, "inspect")
    return build_parser().parse_args(raw)
