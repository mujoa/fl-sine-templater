"""Command line entry point.

The pipeline itself lives in `core`; this module only parses arguments and turns
the results into console output. `gui.py` is the other front end over the same
three calls.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core, plan as plan_mod


def _make_console_utf8() -> None:
    """SINE names contain U+2006, which the default Windows console codec cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _report(exc: core.BuildError) -> int:
    """Print a failure the way this tool has always printed failures."""
    if exc.problems:
        print(f"\n{exc}:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    _make_console_utf8()
    parser = argparse.ArgumentParser(
        prog="sine-templater",
        description="Wire SINE Player instruments to BRSO Articulate and the FL mixer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="generate the wired template")
    build.add_argument("input", type=Path, help="SINE-only .flp with a BRSO donor as last channel")
    build.add_argument("-o", "--output", type=Path, help="output .flp")
    build.add_argument(
        "--compact-mixer",
        action="store_true",
        help="give each SINE instance only the inserts its instruments actually use, "
             "instead of reserving all 16 MIDI-channel slots for instruments that "
             "may be added later",
    )
    build.add_argument(
        "--colors",
        type=Path,
        metavar="FILE",
        help="channel rack / mixer palette, a YAML 'colors:' list of \"#RRGGBB\" "
             "(default: the palette saved from the window, or the shipped "
             f"{plan_mod.DEFAULT_COLORS_FILE.name} until one is)",
    )
    build.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    build.add_argument("--force", action="store_true", help="overwrite an existing output")

    window = sub.add_parser("gui", help="open the window")
    window.add_argument(
        "input", type=Path, nargs="?", help="optional project to load on opening"
    )
    window.add_argument("--compact-mixer", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    if args.command not in ("build", "gui"):
        parser.error(f"unknown command {args.command!r}")

    if args.command == "gui":
        from . import gui

        return gui.main(input_path=args.input, compact=args.compact_mixer)

    try:
        preview = core.preview(
            args.input, compact=args.compact_mixer, colors_file=args.colors
        )
    except core.BuildError as exc:
        return _report(exc)

    print(preview.description)
    print(f"palette: {preview.color_count} color(s) from {preview.colors_file}")
    for warning in preview.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.dry_run:
        return 0

    output = args.output or preview.default_output
    try:
        core.check_output(output, args.input, force=args.force)
        data = core.render(preview)
        core.write(data, output, args.input, force=args.force)
    except core.BuildError as exc:
        return _report(exc)

    print(f"\nwrote {output} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
