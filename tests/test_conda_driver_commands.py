from pathlib import Path

from depviz.builtin.conda.driver import _build_apply_command
from depviz.builtin.conda.tooling import isolated_environment


def test_mamba_1_exact_apply_disables_ambient_defaults_and_pins() -> None:
    command = _build_apply_command(
        tool="mamba",
        executable="mamba",
        prefix=Path("/tmp/candidate"),
        explicit_file=Path("/tmp/explicit.txt"),
        tool_version="1.5.12",
        offline=False,
    )

    assert "--no-default-packages" in command
    assert "--no-pin" in command


def test_mamba_2_exact_apply_uses_micromamba_cli() -> None:
    command = _build_apply_command(
        tool="mamba",
        executable="mamba",
        prefix=Path("/tmp/candidate"),
        explicit_file=Path("/tmp/explicit.txt"),
        tool_version="2.1.0",
        offline=False,
    )

    assert "--no-default-packages" not in command
    assert "--no-pin" not in command


def test_only_micromamba_receives_an_isolated_root_prefix() -> None:
    temporary_root = Path("/tmp/depviz")
    empty_rc = temporary_root / "empty.yml"

    assert "MAMBA_ROOT_PREFIX" in isolated_environment("micromamba", temporary_root, empty_rc)
    assert "MAMBA_ROOT_PREFIX" not in isolated_environment("mamba", temporary_root, empty_rc)
    assert "MAMBA_ROOT_PREFIX" not in isolated_environment("conda", temporary_root, empty_rc)
