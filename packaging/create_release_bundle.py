from __future__ import annotations

import gzip
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def _copy_executable(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_tar_gz(source: Path, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="depviz-bundle-") as temporary_directory:
        staged = Path(temporary_directory) / "depviz"
        _copy_executable(source, staged)
        with output.open("wb") as raw_output:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=0) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as archive:
                    info = archive.gettarinfo(str(staged), arcname="depviz")
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o755
                    with staged.open("rb") as stream:
                        archive.addfile(info, stream)


def _write_zip(source: Path, output: Path) -> None:
    info = zipfile.ZipInfo("depviz.exe")
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o755 << 16
    with zipfile.ZipFile(output, mode="w") as archive:
        with source.open("rb") as stream, archive.open(info, mode="w") as destination:
            shutil.copyfileobj(stream, destination, length=_CHUNK_SIZE)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: create_release_bundle.py EXECUTABLE OUTPUT", file=sys.stderr)
        return 2

    source = Path(argv[1])
    output = Path(argv[2])
    if not source.is_file():
        print(f"not a file: {source}", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.name.endswith(".tar.gz"):
        _write_tar_gz(source, output)
    elif output.suffix == ".zip":
        _write_zip(source, output)
    else:
        print("output must end in .tar.gz or .zip", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
