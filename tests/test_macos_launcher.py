"""The macOS download: the launcher script and the archive that carries it.

Nothing here runs the launcher -- that needs a Mac, and the open questions about
it are recorded in docs/UI.md. What is testable from Windows is exactly what
Windows is likely to break: the line endings and the execute bit, both of which
fail silently and both of which make the file undiagnosable on the other side.
"""
from __future__ import annotations

import importlib.util
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

import paths

ROOT = paths.ROOT
LAUNCHER = ROOT / "packaging" / "run.command"


def _load_builder():
    """By path rather than by import: `packaging/` is not a package, and the name
    collides with the PyPI `packaging` that pytest itself pulls in."""
    spec = importlib.util.spec_from_file_location(
        "build_macos_zip", ROOT / "packaging" / "build_macos_zip.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def test_launcher_has_unix_line_endings():
    assert b"\r\n" not in LAUNCHER.read_bytes()


def test_launcher_starts_with_a_shebang():
    assert LAUNCHER.read_bytes().startswith(b"#!/bin/sh\n")


def test_launcher_opens_the_window():
    assert "-m sine_templater gui" in LAUNCHER.read_text(encoding="utf-8")


def test_launcher_never_probes_the_xcode_stub():
    """Running /usr/bin/python3 on a Mac without the Command Line Tools pops a
    700 MB install dialog, so the check has to skip it rather than test it."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert '"$p" != "/usr/bin/python3"' in text


def test_launcher_is_valid_shell():
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("no sh on this machine (Git Bash provides one)")
    result = subprocess.run([sh, "-n", str(LAUNCHER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_archive_keeps_the_execute_bit(tmp_path: Path):
    """The whole point of building the zip with a script. Without the Unix system
    marker the mode is dropped, and double-clicking run.command does nothing."""
    out = builder.build(tmp_path / "mac.zip")
    with zipfile.ZipFile(out) as archive:
        info = archive.getinfo(f"{builder.TOP}/run.command")
    assert info.create_system == 3, "not marked as coming from Unix; mode is ignored"
    assert stat.S_IMODE(info.external_attr >> 16) == 0o755


def test_archive_carries_the_whole_package(tmp_path: Path):
    """A new module in sine_templater/ has to reach the Mac too, and forgetting one
    shows up as an ImportError on somebody else's machine."""
    out = builder.build(tmp_path / "mac.zip")
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
    expected = {
        f"{builder.TOP}/sine_templater/{p.name}"
        for p in (ROOT / "sine_templater").glob("*.py")
    }
    expected |= {
        f"{builder.TOP}/sine_templater/channel_colors.yaml",
        f"{builder.TOP}/run.command",
        f"{builder.TOP}/LICENSE",
        f"{builder.TOP}/README.md",
    }
    assert expected <= names


def test_archive_unpacks_into_one_folder(tmp_path: Path):
    out = builder.build(tmp_path / "mac.zip")
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    assert all(n.startswith(f"{builder.TOP}/") for n in names)
