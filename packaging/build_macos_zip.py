"""Build the macOS download.

    python packaging/build_macos_zip.py [output.zip]

Produces `dist/SINE Templater macOS.zip`, or the named file -- the release workflow
passes a path with the tag in it. It holds the package, the launcher, the license
and the README, under one folder. There is nothing to freeze and nothing to
compile -- the Mac download is source plus `run.command`, which finds a Python and
opens the window. See docs/UI.md.

The whole reason this is a script rather than "right-click, Send to, Compressed
folder" is the execute bit. A zip written by Windows Explorer carries no Unix
permissions, so `run.command` arrives on the Mac without `+x`, and double-clicking
it then does nothing at all -- no error, no window. `zipfile` can store the mode,
as long as the entry also claims to have come from a Unix system, and macOS honors
it on extraction. That is the one thing this file exists to get right.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "SINE Templater macOS.zip"

# Everything unpacks under one folder, so an extraction never scatters files into
# whatever directory the zip was opened in.
TOP = "SINE Templater"

LAUNCHER = "run.command"
EXECUTABLE = 0o755
READABLE = 0o644

# Unix. Without this on the entry, the stored mode is ignored and the execute bit
# is lost again -- the failure this script exists to prevent.
UNIX = 3


def contents() -> list[tuple[Path, str, int]]:
    """(source file, path inside the zip, mode), in the order they are written."""
    items = [(ROOT / "packaging" / LAUNCHER, LAUNCHER, EXECUTABLE)]
    for path in sorted((ROOT / "sine_templater").iterdir()):
        if path.suffix == ".py" or path.name == "channel_colors.yaml":
            items.append((path, f"sine_templater/{path.name}", READABLE))
    for name in ("LICENSE", "README.md"):
        items.append((ROOT / name, name, READABLE))
    return items


def check_launcher(data: bytes) -> None:
    """Two ways the launcher arrives dead, both invisible on Windows.

    CRLF is the nastier one: `#!/bin/sh\r` is not a program any Mac has, and the
    error names a file that is plainly there. `.gitattributes` keeps the checkout
    LF; this is the check that it did.
    """
    if not data.startswith(b"#!"):
        raise SystemExit(f"{LAUNCHER} has lost its shebang line")
    if b"\r\n" in data:
        raise SystemExit(
            f"{LAUNCHER} has CRLF line endings and will not run on macOS "
            "(see .gitattributes)"
        )


def build(out: Path = OUT) -> Path:
    items = contents()
    missing = [str(src) for src, _, _ in items if not src.is_file()]
    if missing:
        raise SystemExit("missing: " + ", ".join(missing))

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for src, name, mode in items:
            data = src.read_bytes()
            if name == LAUNCHER:
                check_launcher(data)
            info = zipfile.ZipInfo(f"{TOP}/{name}")
            info.create_system = UNIX
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) > 1:
        raise SystemExit(f"usage: {Path(__file__).name} [output.zip]")
    out = build(Path(args[0]) if args else OUT)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    print(f"Built {out}  ({out.stat().st_size / 1024:.0f} KB, {len(names)} files)")
    print(f"  {TOP}/{LAUNCHER} is marked executable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
