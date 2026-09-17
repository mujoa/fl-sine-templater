"""Where the test .flp projects live.

They are not tracked in git -- they are large FL Studio projects and contain the
user's own work. Drop them in ./testfiles, or point SINE_TEMPLATER_TESTFILES at
wherever you keep them. Tests that need a file that is absent skip rather than
fail, so the skip count in pytest's summary tells you what was not covered.

Required:
    _sine_test_01.flp   the hand-built reference template (VST2). Needed by most
                        tests: codec symmetry, the BRSO comparison, and as the
                        source `make_fixture.py` derives the end-to-end input from.
Optional:
    _sine_test_02.flp   the same template with one instrument swapped; a second
                        sample for the codec symmetry checks.
    any VST3 project    any .flp here holding a VST3-hosted SINE instance enables
                        the VST3 container tests.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTFILES = Path(os.environ.get("SINE_TEMPLATER_TESTFILES") or ROOT / "testfiles")

PRIMARY_REFERENCE = "_sine_test_01.flp"
REFERENCE_NAMES = (PRIMARY_REFERENCE, "_sine_test_02.flp")

MISSING = (
    f"no test projects in {TESTFILES} "
    f"(set SINE_TEMPLATER_TESTFILES to point elsewhere)"
)


def reference(name: str = PRIMARY_REFERENCE) -> Path:
    return TESTFILES / name


def references() -> list[Path]:
    """The hand-built reference templates that are actually present."""
    return [p for p in (TESTFILES / n for n in REFERENCE_NAMES) if p.is_file()]


def projects() -> list[Path]:
    """Every .flp available to the tests."""
    return sorted(TESTFILES.glob("*.flp")) if TESTFILES.is_dir() else []
