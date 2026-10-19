"""Bounded regular-file reads for untrusted task artifacts."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _read_regular(path: Path, maximum: int, exact_size: int | None = None) -> bytes:
    """Bounded read; reject symlinks, devices and FIFOs before blocking I/O.

    fstat checks the opened object too, so a path swap cannot smuggle in a
    special file after lstat. O_NONBLOCK prevents a FIFO open from hanging.
    """
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("expected regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
            or current.st_size > maximum
            or (exact_size is not None and current.st_size != exact_size)
        ):
            raise ValueError("invalid file type or size")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("oversized file")
        return data
    finally:
        os.close(fd)
