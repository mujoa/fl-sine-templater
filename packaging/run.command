#!/bin/sh
# SINE Templater -- double-clickable launcher for macOS.
#
# Finder hands a .command file to Terminal.app, so double-clicking this opens the
# window with nothing typed at a prompt. It is not a frozen app: there is no Mac
# executable to ship (see docs/UI.md -- a .dmg cannot be built without a Mac, and
# an unsigned one is worse for the user than this is anyway). The Mac download is
# this script sitting beside the sine_templater package, run by whatever Python is
# already on the machine.
#
# In order:
#   1. look for an interpreter that is new enough *and* can import tkinter;
#   2. if there is none, offer to fix that -- Homebrew when it is already
#      installed, otherwise the python.org download page;
#   3. open the window.
#
# It never installs Homebrew and never asks for an administrator password. A tool
# that builds FL Studio templates has no business doing either.

set -u

HERE=$(cd -- "$(dirname -- "$0")" && pwd)
DOWNLOAD_PAGE="https://www.python.org/downloads/macos/"

say() { printf '%s\n' "$*"; }

# Terminal closes the window when the shell exits, depending on the user's
# profile, and an error nobody read is an error nobody can act on.
pause() {
    printf '\nPress Return to close this window. '
    read -r _ 2>/dev/null </dev/tty || true
    printf '\n'
}

# Default No: a bare Return installs nothing.
confirm() {
    printf '%s [y/N] ' "$1"
    read -r answer 2>/dev/null </dev/tty || answer=""
    case "$answer" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# In the download, the package sits beside this script. In the repository the
# script lives in packaging/ and the package is one level up, so accept both
# rather than only working once it has been zipped.
if [ -d "$HERE/sine_templater" ]; then
    APP="$HERE"
elif [ -d "$HERE/../sine_templater" ]; then
    APP=$(cd -- "$HERE/.." && pwd)
else
    say "The sine_templater folder is not next to this script."
    say "Unpack the whole download and run it from there."
    pause
    exit 1
fi

# /usr/bin/python3 is deliberately missing from this list. It is a stub: on a Mac
# without the Xcode Command Line Tools, *running* it pops a 700 MB install dialog,
# so probing it is itself the damage. Where the tools are installed it works, but
# links Tk 8.5, which draws the window badly. Either way, not this one.
candidates() {
    for p in \
        /Library/Frameworks/Python.framework/Versions/*/bin/python3 \
        /opt/homebrew/bin/python3 \
        /usr/local/bin/python3
    do
        [ -x "$p" ] && printf '%s\n' "$p"
    done
    p=$(command -v python3 2>/dev/null) || p=""
    if [ -n "$p" ] && [ "$p" != "/usr/bin/python3" ]; then
        printf '%s\n' "$p"
    fi
}

# One question, not two. "Is Python installed?" and "is Tkinter installed?" are
# separate states with the same remedy, and a Python that cannot import tkinter
# (pyenv, conda) is usually not repairable in place -- so ask what actually
# matters: can this interpreter open the window?
usable() {
    "$1" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1
}

find_python() {
    candidates | while IFS= read -r p; do
        if usable "$p"; then
            printf '%s\n' "$p"
            return 0
        fi
    done
}

# Homebrew leaves Tkinter out of its python formula on purpose; python-tk is a
# separate, version-numbered formula, and which numbers exist changes with every
# Python release. So ask brew which one it has rather than hardcoding a number
# that will be wrong in a year.
install_with_homebrew() {
    minor=""
    brewpy="$(brew --prefix 2>/dev/null)/bin/python3"
    if [ -x "$brewpy" ]; then
        minor=$("$brewpy" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || minor=""
    fi
    for formula in ${minor:+python-tk@$minor} python-tk; do
        if brew info --formula "$formula" >/dev/null 2>&1; then
            say ""
            say "Installing $formula with Homebrew. This takes a few minutes."
            say ""
            brew install "$formula"
            return $?
        fi
    done
    say ""
    say "Homebrew here does not offer a python-tk formula, so it cannot help."
    return 1
}

# Assisted, not automatic: they run Apple's own installer themselves. The
# python.org build bundles Tcl/Tk 8.6, so it answers "no Python" and "no Tkinter"
# in one step, and pinning no download URL here means nothing to keep up to date.
install_from_python_org() {
    say ""
    say "Opening the Python download page:"
    say ""
    say "  1. Download the macOS installer for the latest release."
    say "  2. Run it and click through with the default options."
    say "  3. Come back to this window."
    say ""
    say "That installer includes Tkinter. There is nothing else to install, and it"
    say "leaves any Python you already have alone."
    open "$DOWNLOAD_PAGE" >/dev/null 2>&1 || say "Could not open a browser. The address is $DOWNLOAD_PAGE"
    printf '\nPress Return once the installer has finished. '
    read -r _ 2>/dev/null </dev/tty || true
}

how_to_fix_it_by_hand() {
    say ""
    say "Install Python from $DOWNLOAD_PAGE -- that build includes Tkinter -- then"
    say "double-click this file again."
}

PY=$(find_python)

if [ -z "$PY" ]; then
    say "SINE Templater needs Python with Tkinter, and this Mac has neither."
    say ""
    if command -v brew >/dev/null 2>&1; then
        say "Homebrew is installed here, so it can be fetched without an"
        say "administrator password and without touching anything else."
        if confirm "Install it now?"; then
            install_with_homebrew || true
        else
            how_to_fix_it_by_hand
            pause
            exit 1
        fi
    else
        if confirm "Open the Python download page so you can install it?"; then
            install_from_python_org
        else
            how_to_fix_it_by_hand
            pause
            exit 1
        fi
    fi
    PY=$(find_python)
fi

if [ -z "$PY" ]; then
    say ""
    say "Still no Python with Tkinter, so the window cannot open."
    how_to_fix_it_by_hand
    pause
    exit 1
fi

say "Starting SINE Templater with $PY"

# The package is found because it sits in APP. PYTHONIOENCODING is
# for the command line half of it: FL text is UTF-16-LE and instrument names
# contain U+2006, which a POSIX locale cannot print.
cd "$APP" || exit 1
PYTHONIOENCODING=utf-8 exec "$PY" -m sine_templater gui
