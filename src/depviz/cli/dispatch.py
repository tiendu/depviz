from __future__ import annotations

import argparse

from depviz.cli.commands import run_command
from depviz.cli.services import ApplicationServices


def dispatch(args: argparse.Namespace, services: ApplicationServices) -> int:
    return int(run_command(args, services))
