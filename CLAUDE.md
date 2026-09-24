# SINE Templater

Builds a wired FL Studio orchestral template out of a SINE Player project: every
instrument routed to its own mixer insert and named after itself, a section bus per
SINE instance, and one BRSO Articulate channel per instrument with its articulation
grid filled in from the keyswitch notes already stored inside SINE.

Python 3.10+, standard library only. `pytest` is the single dev dependency.

## Layout

| | |
|---|---|
| `sine_templater/` | the package (see the pipeline below) |
| `packaging/` | PyInstaller spec, entry points, icon, Windows build script, the macOS launcher and its zip builder |
| `tests/` | the pytest suite |
| `docs/FORMAT.md` | verified file-format findings: FLP, Fruity Wrapper, SINE, BRSO |
| `docs/SPEC.md` | what the tool does — input contract, mixer layouts, decisions, open items |
| `docs/UI.md` | the window, packaging and distribution |

The `docs/` files are not loaded into context automatically. **Read `docs/FORMAT.md`
before touching any codec, and `docs/SPEC.md` before changing what the tool writes.**
Both record what was verified against real files rather than what seemed likely, and
that distinction is the point of them.

## The pipeline

```
flp.py        event-stream codec (FLhd/FLdt, varint payloads, colors)
wrapper.py    Fruity Wrapper chunk codec
sine.py       SINE Player state: container + JSON, all length fields
brso.py       BRSO Articulate state: parse, build, donor validation
model.py      read-only view over a parsed project
plan.py       Project -> Plan (pure, no mutation): insert layout, palette
apply.py      Plan -> mutated Project
validate.py   re-parses the output and checks the wiring holds
core.py       the pipeline, callable without a terminal; raises, never prints
cli.py        the command line
gui.py        the Tkinter window
```

`core.py` is what both front ends call. Anything that prints belongs in `cli.py`.

## Running it

```
python -m sine_templater build input.flp [-o out.flp] [--compact-mixer]
                                         [--colors FILE] [--dry-run] [--force]
python -m sine_templater gui
python -m pytest
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
python packaging\build_macos_zip.py       # the Mac download; no Mac needed
```

Console output needs `PYTHONIOENCODING=utf-8`: FLP text is UTF-16-LE and instrument
names contain U+2006.

## Tests

`python -m pytest` from the repo root. The suite needs no arguments — `pytest.ini`
sets the path.

Tests that need a real FL Studio project look in `testfiles/`, which is not tracked
(the projects are large and are somebody's own work). **A test that cannot find the
file it needs skips rather than fails**, so the skip count in pytest's summary is the
honest measure of what actually ran. `tests/paths.py` documents which files are
wanted and how to point `SINE_TEMPLATER_TESTFILES` somewhere else.
`tests/make_fixture.py` derives the end-to-end input from the reference project.

## Conventions

- **Standard library only.** No third-party runtime dependency, including for YAML —
  there is a small YAML subset reader in `plan.py`. Adding one would break
  `python -m sine_templater` on a fresh checkout, which has no install step.
- **Surgical edits, never reconstruction.** The FLP is parsed to a flat event list,
  only the intended events are touched, and it is re-serialized. Every codec holds
  `serialize(parse(x)) == x` on real blobs, asserted in the tests. Any byte that
  differs between input and output is therefore a deliberate change.
- **Verify against the bytes.** The format findings in `docs/FORMAT.md` were all read
  out of real projects. Several plausible-looking assumptions turned out to be wrong
  in ways no round-trip or symmetry test could catch, because the reference file was
  already correct — see the lesson at the end of that document before trusting a
  reading that "looks right".
- **Refuse rather than guess.** A malformed input, an unreadable palette or a layout
  that will not fit is an error naming the cause, never a silent fallback. Validation
  runs in memory before anything is written, so a failed check leaves no file behind.
- **US English** in code, comments, UI text and docs alike. Note that some FL and
  BRSO key names are data and keep their own spelling.
- Error messages are read by composers, not developers. Say what is wrong and what to
  do about it.

## Releasing

Push an annotated `v*` tag; CI freezes the executables, zips `dist\SINE Templater\`
and publishes the release, using the tag message as the notes. The tag must match
`__version__` in `sine_templater/__init__.py`.
