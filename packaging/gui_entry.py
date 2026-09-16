"""Frozen entry point for the windowed build.

PyInstaller needs a script rather than a module, and a script cannot use the
package-relative imports `sine_templater.gui` is written with -- hence this
shim rather than pointing the spec at `gui.py` directly.

A single optional argument is passed through as the project to open, so dropping
a .flp on the executable (or setting it as the "Open with" handler) loads that
project and shows its plan straight away.
"""
import sys

from sine_templater.gui import main

raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
