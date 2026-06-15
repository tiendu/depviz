import argparse
import logging
import sys
from pathlib import Path

from depviz.renderer import (
    print_blast_radius,
    print_dependency_weight,
    print_deps,
    print_impact,
    print_summary,
    print_why,
)

from depviz.fetchers import build_graph_concurrently
from depviz.parsers import get_parser

from depviz.cache import (
    default_cache_path,
    load_graph_cache,
    save_graph_cache
)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def run(args: argparse.Namespace) -> None:
    path = Path(args.manifest)

    if not path.exists():
        logger.error("File not found: %s", path)
        sys.exit(1)

    try:
        parser = get_parser(path)
    except ValueError as err:
        logger.error(str(err))
        sys.exit(1)

    parse_result = parser.parse(path)

    if not parse_result.packages:
        logger.error("No packages found in %s", path)
        sys.exit(1)

    logger.info("Found %d root package(s)", len(parse_result.packages))

    if parse_result.channels:
        logger.info("Detected Conda channels: %s", ", ".join(parse_result.channels))

    logger.info("Resolving dependencies...")

    cache_path = (
        Path(args.cache_file)
        if args.cache_file
        else default_cache_path(path, args.depth)
    )

    if args.from_cache:
        if not cache_path.exists():
            logger.error("Cache file not found: %s", cache_path)
            sys.exit(1)

        logger.info("Loading graph from cache: %s", cache_path)
        graph = load_graph_cache(cache_path)

    else:
        if (
            not args.no_cache
            and not args.refresh_cache
            and cache_path.exists()
        ):
            logger.info("Loading graph from cache: %s", cache_path)
            graph = load_graph_cache(cache_path)
        else:
            logger.info("Resolving dependencies...")

            graph = build_graph_concurrently(
                parse_result=parse_result,
                max_workers=args.workers,
                max_depth=args.depth,
            )

            if not args.no_cache:
                logger.info("Saving graph cache: %s", cache_path)
                save_graph_cache(graph, parse_result, cache_path)

    print_summary(graph)

    if args.report in {"why", "impact", "deps"} and not args.package:
        logger.error("--package is required for report '%s'", args.report)
        sys.exit(1)

    if args.report in {"all", "blast"}:
        print_blast_radius(graph, args.limit)

    if args.report in {"all", "weight"}:
        print_dependency_weight(graph, args.limit)

    if args.report == "why":
        print_why(graph, args.package, args.limit)

    if args.report == "impact":
        print_impact(graph, args.package, args.limit)

    if args.report == "deps":
        print_deps(graph, args.package, args.limit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="depviz",
        description="Inspect dependency graphs for PyPI and Conda/Bioconda manifests.",
    )

    parser.add_argument(
        "manifest",
        help="Path to requirements.txt or environment.yml",
    )

    parser.add_argument(
        "--depth",
        type=int,
        default=3,
        help="Maximum recursive dependency depth. Default: 3",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=12,
        help="Number of concurrent fetch workers. Default: 12",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of rows to show per report. Default: 10",
    )

    parser.add_argument(
        "--report",
        choices=["all", "blast", "weight", "why", "impact", "deps"],
        default="all",
        help="Report type to print. Default: all",
    )

    parser.add_argument(
        "--package",
        help="Package name for why, impact, or deps reports."
    )

    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Load the resolved graph from cache instead of resolving dependencies.",
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write graph cache.",
    )

    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing cache and rebuild it.",
    )

    parser.add_argument(
        "--cache-file",
        help="Custom graph cache file path.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()

