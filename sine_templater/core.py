"""The build pipeline, callable without a terminal.

`cli.py` and `gui.py` are both thin front ends over this module. Nothing here
prints or exits: failures are raised as `BuildError` carrying a message already
written for a human, so the GUI can put the same words in a dialog that the CLI
puts on stderr.

The pipeline is three steps, kept separate so a front end can show the plan
before anything is written:

    preview()  parse the input and derive the plan   -- reads only
    render()   apply the plan and verify the result  -- writes nothing
    write()    put the bytes on disk
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import apply as apply_mod, flp, plan as plan_mod, validate


# What a generated template is called when nobody says otherwise: the input's
# name with this on the end. One copy, because both front ends suggest it.
OUTPUT_SUFFIX = "_template"


def default_output_name(input_path: Path | str) -> str:
    """The suggested file name for a template built from `input_path`."""
    return Path(input_path).stem + OUTPUT_SUFFIX + ".flp"


def _message(exc: Exception) -> str:
    """An exception as a line of text, including the ones that carry none.

    A truncated file fails on a `struct.error` or an `IndexError` raised deep in
    a codec, and an `IndexError` in particular often says nothing at all -- which
    would reach the user as a dialog with an empty body.
    """
    return str(exc) or exc.__class__.__name__


class BuildError(Exception):
    """A failure the user is meant to read, not a traceback.

    `problems` carries the itemised list from `validate.check`, which the front
    ends render one per line under the message.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = list(problems or ())


@dataclass
class Preview:
    """A derived plan, plus everything a front end needs to describe it."""

    input_path: Path
    project: flp.Project
    plan: plan_mod.Plan
    colors_file: Path
    color_count: int

    @property
    def description(self) -> str:
        return plan_mod.describe(self.plan)

    @property
    def warnings(self) -> list[str]:
        return validate.warnings(self.plan)

    @property
    def default_output(self) -> Path:
        return self.input_path.with_name(default_output_name(self.input_path))


def frozen() -> bool:
    """True when running from a PyInstaller build rather than a source checkout."""
    return bool(getattr(sys, "frozen", False))


def user_colors_file() -> Path:
    """Where a palette the user edits is kept. May not exist yet.

    Frozen, that is next to the executable: this is a portable one-folder app, the
    palette has always been a file the user can open in a text editor, and keeping
    it with the app means a copied install brings its colors along.

    From a source checkout the same place would be the tracked
    `sine_templater/channel_colors.yaml`, and saving a palette is not a reason to
    edit the repository, so it goes under the per-user config directory instead.
    """
    if frozen():
        return Path(sys.executable).resolve().parent / plan_mod.DEFAULT_COLORS_FILE.name
    return _config_dir() / plan_mod.DEFAULT_COLORS_FILE.name


def _config_dir() -> Path:
    """Per-user settings directory: %APPDATA% on Windows, XDG elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "SINE Templater"


def resolve_colors_file() -> Path:
    """Where the palette lives for a normal run.

    Frozen, `plan.DEFAULT_COLORS_FILE` sits inside PyInstaller's temporary
    extraction directory: unreachable for editing and gone when the app exits.
    So the first frozen run copies it beside the executable and every run after
    reads that copy, which means editing the palette works the same way it does
    from a checkout. If the install directory is read-only the bundled copy is
    used unchanged -- a palette that cannot be edited beats a refusal to start.

    From a checkout there is nothing to copy: the saved palette is used once it
    exists, and the bundled default until then.
    """
    saved = user_colors_file()
    if not frozen():
        return saved if saved.is_file() else plan_mod.DEFAULT_COLORS_FILE
    if not saved.is_file():
        try:
            shutil.copyfile(plan_mod.DEFAULT_COLORS_FILE, saved)
        except OSError:
            return plan_mod.DEFAULT_COLORS_FILE
    return saved


def save_colors(colors: list[int], path: Path | str | None = None) -> Path:
    """Write a palette, by default to the file `resolve_colors_file` will read."""
    try:
        return plan_mod.write_channel_colors(
            path if path is not None else user_colors_file(), colors
        )
    except ValueError as exc:
        raise BuildError(str(exc)) from exc


def preview(
    input_path: Path | str,
    *,
    compact: bool = False,
    colors_file: Path | str | None = None,
) -> Preview:
    """Parse the input and derive the plan. Reads only; writes nothing."""
    input_path = Path(input_path)
    colors_file = Path(colors_file) if colors_file is not None else resolve_colors_file()
    try:
        colors = plan_mod.load_channel_colors(colors_file)
    except flp.CODEC_ERRORS as exc:
        raise BuildError(_message(exc)) from exc
    try:
        data = input_path.read_bytes()
    except OSError as exc:
        raise BuildError(f"cannot read {input_path}: {exc}") from exc
    try:
        project = flp.parse(data)
    except flp.CODEC_ERRORS as exc:
        # Separate from `derive` below: everything that one refuses is a project
        # this tool can read and will not build from, and says so in its own
        # words. Failing here means the bytes are not a project at all -- a
        # truncated file, or something that is not an .flp -- and the codec that
        # noticed has only its own low-level complaint to offer.
        raise BuildError(
            f"{input_path} is not a project this tool can read: {_message(exc)}"
        ) from exc
    try:
        derived = plan_mod.derive(project, compact=compact, colors=colors)
    except flp.CODEC_ERRORS as exc:
        raise BuildError(_message(exc)) from exc
    return Preview(input_path, project, derived, colors_file, len(colors))


def render(pv: Preview) -> bytes:
    """Apply the plan and verify the result. Still writes nothing.

    Raises `BuildError` with `problems` set if the output fails validation, which
    is the case where the caller must not write.
    """
    try:
        data = flp.serialize(apply_mod.apply(pv.project, pv.plan))
        problems = validate.check(data, pv.plan)
    except flp.CODEC_ERRORS as exc:
        raise BuildError(_message(exc)) from exc
    if problems:
        raise BuildError("validation failed, nothing written", problems)
    return data


def check_output(
    output: Path | str, input_path: Path | str, *, force: bool = False
) -> Path:
    """Raise if the destination is unusable. Separate from `write` so a front end
    can refuse before spending the time to render a megabyte of SINE state.

    The two refusals are ordered as the CLI has always ordered them: an existing
    output is reported before the write-over-the-input check, so pointing the
    output at the input still says "exists" unless `force` is set.
    """
    output = Path(output)
    if output.exists() and not force:
        raise BuildError(f"{output} exists (use --force to overwrite)")
    if output.resolve() == Path(input_path).resolve():
        raise BuildError("refusing to write over the input file")
    return output


def write(
    data: bytes, output: Path | str, input_path: Path | str, *, force: bool = False
) -> Path:
    """Write the rendered bytes, re-running the destination checks first."""
    output = check_output(output, input_path, force=force)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    except OSError as exc:
        raise BuildError(f"cannot write {output}: {exc}") from exc
    return output
