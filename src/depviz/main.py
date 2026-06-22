from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from depviz.api.errors import PluginError
from depviz.cli.dispatch import dispatch
from depviz.cli.exit_codes import ExitCode
from depviz.cli.parser import parse_args
from depviz.cli.services import ApplicationServices
from depviz.infrastructure import LocalCommandRunner
from depviz.plugins.defaults import create_default_registry


def run(args: argparse.Namespace) -> int:
    try:
        services = ApplicationServices(
            registry=create_default_registry(),
            command_runner=LocalCommandRunner(),
        )
    except PluginError as error:
        logging.getLogger(__name__).error("Plugin registration failed: %s", error)
        return int(ExitCode.INVALID_INPUT)
    return dispatch(args, services)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
