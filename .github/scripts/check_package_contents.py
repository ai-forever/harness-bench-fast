from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

FORBIDDEN = (
    ".free-code-logs",
    ".env",
    ".venv",
    ".github",
    "uv.lock",
    "__pycache__",
)


def main() -> None:
    dist = Path("dist")
    sdists = list(dist.glob("*.tar.gz"))
    wheels = list(dist.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        raise SystemExit(f"Expected one sdist and one wheel, got {sdists!r} and {wheels!r}")

    with tarfile.open(sdists[0]) as package:
        names = package.getnames()
    with zipfile.ZipFile(wheels[0]) as package:
        names.extend(package.namelist())

    matches = [name for name in names if any(part in name for part in FORBIDDEN)]
    if matches:
        raise SystemExit("Forbidden files in package:\n" + "\n".join(matches))


if __name__ == "__main__":
    main()
