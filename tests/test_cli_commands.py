from depviz.cli import parse_args


def test_legacy_manifest_invocation_maps_to_inspect() -> None:
    args = parse_args(["environment.yml", "--report", "blast"])

    assert args.command == "inspect"
    assert args.manifest == "environment.yml"


def test_explicit_resolve_command_is_preserved() -> None:
    args = parse_args(["resolve", "environment.yml", "--tool", "conda"])

    assert args.command == "resolve"
    assert args.tool == "conda"
