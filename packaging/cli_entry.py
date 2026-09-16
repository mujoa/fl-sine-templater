"""Frozen entry point for the console build.

The command line stays a first-class way to run this: the same flags the source
checkout accepts, with no Python installed. See `gui_entry.py` for why this shim
exists.
"""
from sine_templater.cli import main

raise SystemExit(main())
