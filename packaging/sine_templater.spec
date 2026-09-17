# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one folder, two executables.

    SINE Templater.exe       windowed, opens the GUI      (console=False)
    sine-templater-cli.exe   console, the CLI unchanged   (console=True)

The windowed one is deliberately the one named after the product: it is what a
non-technical user double-clicks, and two near-identical names beside each other
was the confusing part.

Both are built with `exclude_binaries=True` and handed to a single COLLECT, so
they share one `_internal` folder instead of shipping the interpreter twice.
Each EXE stub resolves its dependencies relative to its own directory, so both
find the same one.

onedir rather than onefile, deliberately: it starts faster and draws fewer
antivirus false positives.

Nothing is shipped that the app cannot reach: see EXCLUDES and `_wanted` below,
and the smoke check at the end of build_windows.ps1 that proves the trimmed
build still runs.
"""
from pathlib import PurePath, Path

ROOT = Path(SPECPATH).parent

PALETTE = [(str(ROOT / "sine_templater" / "channel_colors.yaml"), "sine_templater")]

# Drop an icon at packaging/sine_templater.ico to brand the executables. Without
# one they carry PyInstaller's default, which is why this is optional rather than
# a hard path. Windows wants .ico specifically: a .png is rejected unless Pillow
# is installed to convert it.
_icon = ROOT / "packaging" / "sine_templater.ico"
ICON = str(_icon) if _icon.is_file() else None

# `cli.py` imports the GUI inside a function, which static analysis can miss.
# The icon is embedded in the exe as a PE resource, but Tk's iconbitmap needs a
# real file on disk, so it is bundled as data too. Costs 370 KB; without it the
# window keeps Tk's default icon while Explorer shows ours.
WINDOW_ICON = [(ICON, ".")] if ICON else []

# Modules nothing in the tool imports. The tool reads and writes one local file
# and draws one window: no network, no crypto, no compression beyond the zlib
# built into python311.dll.
#
# The C extensions are named rather than their pure-Python front ends on purpose.
# `hashlib` falls back to its built-in digests when `_hashlib` is missing, and
# `random` imports `hashlib` -- so excluding `_hashlib` drops the .pyd and, with
# `_ssl` gone too, the 5 MB libcrypto-3.dll behind it, while leaving every import
# in the stdlib satisfiable. Excluding `hashlib` itself would break `random`.
# `unicodedata` is not in this list, and was until 0.2.2: `brso.ascii_name` folds
# an articulation name to the ASCII BRSO stores by normalizing it, so the 1.1 MB
# .pyd is now load-bearing. Excluding it builds cleanly and fails on the first
# import at runtime, which is what the smoke check at the end of
# build_windows.ps1 is there to catch.
EXCLUDES = [
    "numpy", "PIL", "pytest", "setuptools",
    "_hashlib", "_ssl",          # OpenSSL; takes libcrypto-3.dll with them
    "_socket", "select",         # no networking
    "_bz2", "_lzma",             # no compression
    "_decimal",
]

COMMON = dict(
    pathex=[str(ROOT)],
    binaries=[],
    datas=PALETTE + WINDOW_ICON,
    hiddenimports=["sine_templater.gui"],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# Tcl/Tk data the tkinter hook copies wholesale. It takes whole directories rather
# than working out what Tk touches, so the trimming happens here instead.
#
# Kept deliberately, though this machine never reads them: every other `cp*.enc`,
# because Tcl loads the *user's* Windows ANSI codepage at startup and panics if it
# is missing (cp932/936/949/950 included -- those are the CJK Windows codepages,
# which is not the same thing as the CJK conversion tables dropped below), and
# both `msgs` directories, which localize dialog strings on a non-English Windows.
DROP_TCL_DIRS = {
    ("tcl8",),                      # Tcl Modules: tcltest, http, platform, msgcat
    ("_tcl_data", "tzdata"),        # timezone database; the app shows no times
}

# Conversion tables for scripts the app never transcodes, plus the Mac and X11
# font encodings, on a Windows-only build.
DROP_ENCODINGS = {
    "big5", "cns11643", "dingbats", "ebcdic", "euc-cn", "euc-jp", "euc-kr",
    "gb12345", "gb1988", "gb2312", "gb2312-raw", "jis0201", "jis0208", "jis0212",
    "ksc5601", "macCentEuro", "macCroatian", "macCyrillic", "macDingbats",
    "macGreek", "macIceland", "macJapan", "macRomania", "macThai", "macTurkish",
    "macUkraine", "shiftjis", "symbol",
}


def _wanted(entry) -> bool:
    """Whether a TOC entry is shipped. Predicate, so it fails open: if a future
    PyInstaller reorganizes these paths it stops matching and the files come back,
    which is a bigger download rather than a broken build."""
    parts = PurePath(str(entry[0]).replace("\\", "/")).parts
    if any(parts[:len(prefix)] == prefix for prefix in DROP_TCL_DIRS):
        return False
    if parts[:2] == ("_tcl_data", "encoding"):
        return PurePath(parts[-1]).stem not in DROP_ENCODINGS
    return True


def trim(toc):
    return [entry for entry in toc if _wanted(entry)]

gui_analysis = Analysis([str(ROOT / "packaging" / "gui_entry.py")], **COMMON)
cli_analysis = Analysis([str(ROOT / "packaging" / "cli_entry.py")], **COMMON)

gui_pyz = PYZ(gui_analysis.pure, gui_analysis.zipped_data)
cli_pyz = PYZ(cli_analysis.pure, cli_analysis.zipped_data)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="SINE Templater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    icon=ICON,
    console=False,
)

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="sine-templater-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    icon=ICON,
    console=True,
)

COLLECT(
    gui_exe,
    cli_exe,
    trim(gui_analysis.binaries),
    trim(gui_analysis.datas),
    trim(cli_analysis.binaries),
    trim(cli_analysis.datas),
    strip=False,
    upx=False,
    name="SINE Templater",
)
