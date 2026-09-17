# sine-templater — application shell

Companion to `SPEC.md` (which holds the tool's intent) and `FORMAT.md` (the file-format
findings). This document covers the **UI and distribution** question: making the tool usable
by someone who will not open a terminal.

Picks up SPEC.md "Open items" #6. **The Windows half is built** — see "What exists" below.
The macOS sections remain analysis.

## What exists

| | |
|---|---|
| `sine_templater/core.py` | The pipeline, callable without a terminal. Raises `BuildError`; prints nothing. |
| `sine_templater/cli.py` | Unchanged behavior, now a front end over `core`. Gained a `gui` subcommand. |
| `sine_templater/gui.py` | The Tkinter window. |
| `packaging/*.spec`, `*_entry.py` | PyInstaller: one folder, two executables. |
| `packaging/build_windows.ps1` | `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` |

One trap worth knowing: a **running copy of the app holds `dist` open**, and COLLECT then
leaves the previous build in place while still reporting success — which looked exactly like
the icon silently failing to apply. The build script now kills anything running out of `dist`
first and checks PyInstaller's exit code, which `$ErrorActionPreference` does not catch for a
native executable.

`dist\SINE Templater\` holds `SINE Templater.exe` (windowed) and `sine-templater-cli.exe`
(console, the CLI unchanged) sharing one `_internal` folder — 25 MB total, 1.9 MB per stub.
Zip the folder to distribute it.

Verified: the test suite still passes in full; the frozen CLI and the GUI both produce output
**byte-identical** to a source-tree CLI run; the palette is extracted beside the executable on
first run and read from there; the windowed build opens with no console behind it.

The command line remains a first-class entry point — `sine-templater-cli.exe build …` takes the
same flags as before, and `python -m sine_templater build …` still works from a checkout.

## The requirement

A composer with no technical background should be able to:

1. download and double-click a program, like any other application;
2. pick an input `.flp`;
3. name the output and choose where it lands;
4. press a button.

No Python, no install steps, no command line.

## Verdict: feasible, and the GUI is the small part

The distribution is the real cost, not the code.

### Why the codebase makes this easy

- **Stdlib only.** Under two thousand lines across `sine_templater/`. No PyYAML — there is a
  hand-rolled YAML subset reader in `plan.py` — no numpy, no native extensions. Nothing to
  compile, nothing to fight with a bundler.
- **The whole pipeline is six lines.** `load_channel_colors` → `flp.parse` → `plan.derive`
  → `apply.apply` → `validate.check` → `write_bytes`, now factored into `core`. A GUI calls
  the same functions.
- **Errors are already written for humans.** *"no BRSO Articulate channel found. Add one
  empty BRSO Articulate as the last channel of the rack; it is the donor for the generated
  instances."* These need no rewriting for a dialog box.
- **It is file-in, file-out and finishes in milliseconds.** No progress bars, no cancellation,
  no background threads, no state to keep between runs.
- **Portable back to Python 3.9.** Verified: no `match`, no walrus, no 3.10-only syntax, and
  every substantive module carries `from __future__ import annotations`, so the `X | None`
  hints are strings and never evaluate at runtime. (`__init__.py` and `__main__.py` lack the
  future import but carry no annotations — 1 and 3 lines respectively.)

## Design decisions

### Toolkit: Tkinter

In the stdlib, present in any Windows CPython install, and bundled into the frozen
executable by PyInstaller so end users install nothing. Qt/PySide6 looks better but adds
~80 MB and LGPL obligations for what is a four-widget dialog. Not justified here.

### Refactor required first

The core currently `print`s and returns exit codes (`cli.py`). A GUI should call the library
functions directly and let `ValueError`/`KeyError` propagate to the window, rather than
screen-scraping `main()`. This is a small extraction — the logic does not change, only where
the output goes. `cli.py` keeps working on top of the same functions.

### The plan preview should be mandatory, not optional

`--dry-run` prints the plan and `validate.warnings()` reports problems. For a non-technical
user this is the most valuable output in the tool — *"6 instances, 47 inserts, 3 warnings"*.
The GUI should **always** show the plan and require a second click to write, rather than
hiding the preview behind a checkbox as the CLI does.

### The palette file needs a real home when frozen

`plan.DEFAULT_COLORS_FILE` resolves the palette as
`Path(__file__).with_name("channel_colors.yaml")`. Under PyInstaller, `__file__` points inside
the temporary extraction directory: the file must be added via `--add-data`, and it becomes
**unreachable for editing and vanishes on exit**.

If the palette is meant to stay user-editable — and the last three commits suggest it is —
the app must write a default copy next to the executable (or to `%APPDATA%` /
`~/Library/Application Support`) on first run and read it from there.

## Packaging

### You cannot cross-compile

PyInstaller, Nuitka and cx_Freeze all emit a binary for the OS they are run on. Three
platforms means three build machines, or GitHub Actions (`windows-latest`, `macos-latest`,
`ubuntu-latest`), which is free and the sane route.

**A *frozen* `.dmg` or `.app` cannot be built without access to a Mac or a macOS CI runner.**
This constraint applies to bundles with an embedded interpreter. It does **not** apply to a
script-based `.app` wrapper, which is plain text and folders and can be assembled on Windows
— see "The script wrapper" below.

### Windows — ship unsigned

Unsigned `.exe`, downloaded and double-clicked:

1. The browser may warn "this file isn't commonly downloaded" → **Keep**.
2. SmartScreen shows "Windows protected your PC" → **More info** → **Run anyway**.
3. It runs, and that machine remembers.

Two extra clicks, once. A code-signing certificate (~$200-400/yr) removes them and is **not
worth buying** for an audience of a handful of composers. A README line and a screenshot
cover it.

Prefer PyInstaller **onedir** over onefile: it starts faster and draws fewer antivirus false
positives. Distribute as a zip, or wrap in an Inno Setup installer.

### macOS — the only place the double-click promise actually breaks

An unsigned `.dmg` gets the `com.apple.quarantine` flag from the browser and Gatekeeper
refuses it. The old right-click → **Open** bypass was removed in macOS 15. The current route
is: double-click → refusal → System Settings → Privacy & Security → scroll to the bottom →
**Open Anyway** → authenticate → confirm.

PyInstaller bundles frequently land on the worse variant — *"app is damaged and can't be
opened, move it to the Trash"* — whose usual remedy is `xattr -cr` in Terminal, i.e. exactly
what the GUI exists to avoid.

There is no free fix, and this is Apple policy rather than a tooling gap:

- A free Apple ID yields an **Apple Development** certificate: local machines only, not
  distribution.
- **Developer ID** signing and **notarisation** both require the $99/yr Developer Program.
- Notarisation itself is free once enrolled and automates cleanly in CI. The certificate is
  the entire cost; it is per-account, not per-app, and covers unlimited apps and updates.

**Ranked Mac options:**

| | Cost | Needs a Mac to build? | User experience |
|---|---|---|---|
| 1. Signed + notarised `.dmg` | $99/yr | yes | Flawless double-click |
| 2. Unsigned `.dmg` + instruction card | free | yes | One-time five-click ritual, then native |
| 3. Script-based `.app` wrapper | free | **no** | Python install once, then double-click, no Terminal |
| 4. python.org installer + `run.command` | free | no | Python install once, Terminal window visible |
| 5. Raw script | free | no | Command line forever |

**2 beats 4.** A one-off Gatekeeper ritual is less ongoing friction than a permanent Python
dependency, and the result looks like a real app. If paying is off the table, unsigned-dmg is
the better free choice rather than dropping to scripts — *provided* a Mac or CI runner exists
to build it. **If not, option 3 is the best available**, and it is the only Mac path requiring
neither a Mac nor $99.

On options 3-5: macOS has shipped no usable Python since 12.3 — `/usr/bin/python3` is a stub that
prompts for Xcode Command Line Tools (~700 MB). Direct users to the **python.org** installer
instead: its `.pkg` is itself signed and notarised, so it installs with a clean double-click,
and it bundles Tcl/Tk 8.6 rather than the deprecated 8.5 in Apple's Command Line Tools, which
makes Tkinter render badly. Ship a `run.command` (marked executable, double-clickable from
Finder) rather than a bare `.py`.

### The script wrapper — a double-clickable Mac build made on Windows

A bare `.py` is not reliably double-clickable: Finder only runs it if python.org's Python
Launcher has claimed the extension, and otherwise opens it in a text editor or does nothing.
Two mechanisms make it dependable. Neither bundles an interpreter — Python must still be
installed — but both remove the terminal from the user's path.

**`run.command`.** A shell script with a shebang, `chmod +x`, named with the `.command`
extension. Terminal.app owns that extension natively, so double-click from Finder runs it and
the Tkinter window appears. A Terminal window sits behind the GUI for the session; whether it
closes on exit depends on the user's Terminal profile ("When the shell exits…"), so assume it
stays. Nothing beyond Python is needed.

**A script-based `.app` bundle.** An `.app` is just a directory with a fixed layout:

```
SINE Templater.app/
  Contents/
    Info.plist
    MacOS/run          <- shell script: exec python3 "$(dirname "$0")/../Resources/main.py"
    Resources/         <- the .py files, icon
```

Because that is nothing but folders and text files, **it can be assembled on Windows without
a Mac** — the no-cross-compiling rule binds only frozen bundles with an embedded interpreter.
The payoff is a real app icon, a proper Dock name instead of "Python", and no Terminal window.
Only Mach-O binaries need a code signature to execute, so a shell-script `CFBundleExecutable`
runs unsigned.

**Two caveats, both of which need testing on a real machine before this is promised:**

- **Gatekeeper still applies** to anything arriving via a browser download. Plain scripts are
  understood to draw the milder "this is a script downloaded from the Internet, are you sure?"
  prompt rather than the hard app block — but the `.app` form very likely gets the hard block,
  since Gatekeeper judges the bundle rather than the script inside it. Unverified.
- **The execute bit is the sharp edge.** Packaging happens on Windows, and a zip built by
  Windows Explorer does not preserve Unix permissions — so `run.command` or `Contents/MacOS/run`
  arrives without `+x` and double-click fails silently. Build the archive with something that
  stores Unix modes (7-Zip writing `.tar.gz`, or `tar` under Git Bash), or the recipient needs
  one `chmod +x`, which is precisely the Terminal step being designed out. **Test this first;
  it decides whether the approach is viable at all.**

Net effect: this moves macOS from option 5 to roughly option 3 — one setup step, then
double-click — and makes it look like a real program. It does not reach option 1.

**A Windows-only GUI with a script fallback on Mac is a legitimate split** — but note what it
ships: Mac users get the command line, which is the exact situation this work exists to fix.
It is a reasonable trade only if there are few or no Mac users.

### Linux — probably drop it

FL Studio has no native Linux build, so nobody who can use this tool's output is running it
there. A glibc-compatible binary (build in an old container, or ship an AppImage) is real work
for an audience of approximately zero.

## Effort

| Piece | Effort |
|---|---|
| Refactor core to be callable without printing | ~1 hour |
| Tkinter window + wiring | ~half a day |
| PyInstaller spec, data files, icon | ~2 hours |
| Script-based `.app` wrapper + archive that keeps the exec bit | ~2 hours, no Mac needed |
| GitHub Actions matrix build | ~half a day |
| macOS signing + notarisation | $99/yr, ~half a day of CI wrestling |
| Windows code signing | not recommended |

## Open items

1. **Are there Mac users at all?** FL Studio runs on macOS, so they may exist. If the audience
   is Windows-only in practice, build one `.exe` and stop — the entire macOS section becomes
   moot.
2. **Is a Mac or macOS CI runner available?** Without one, no frozen `.dmg` can be produced
   and the script wrapper (Mac option 3) becomes the ceiling — still double-clickable, but
   requiring the user to install Python once.
3. ~~Where does the user-editable palette live once frozen?~~ **Settled:** next to the
   executable. `core.resolve_colors_file` copies the bundled default there on first frozen
   run; a read-only install falls back to the bundled copy rather than refusing to start.
4. ~~Does the GUI expose `--compact-mixer`?~~ **Settled:** yes, as a checkbox reading
   "Compact mixer (only the inserts each instrument uses)". Toggling it re-derives the plan
   immediately.
5. ~~Overwrite handling.~~ **Settled:** the GUI asks; the CLI still demands `--force`. Writing
   over the input stays a hard error in both.
6. ~~No icon.~~ **Settled:** `packaging/sine_templater.ico`, converted from
   `sine_templater_icon.png` in the repo root (1254x1254 RGBA) at 256/128/64/48/32/16, all 32-bit with the
   circle's transparency intact. The spec picks the file up if present and falls back to
   PyInstaller's default if not, so replacing the artwork means replacing that one file.
   Windows requires `.ico`; PyInstaller converts a `.png` itself only with Pillow installed,
   which this project does not have.

   Two things about icons that are easy to get wrong, both hit in practice:

   - **Tk does not inherit the executable's icon.** The PE resource is what Explorer, the
     taskbar and Alt-Tab read; the window itself keeps Tk's default until `iconbitmap` is
     called. That needs a real file on disk, so the spec bundles the `.ico` as data as well
     as embedding it — `gui._icon_file` finds it under `sys._MEIPASS` when frozen and under
     `packaging/` from a checkout.
   - **Explorer caches icons by path.** An executable rebuilt at the same path keeps showing
     its old icon indefinitely; a newly-named one shows the correct one immediately. This
     looks exactly like the icon having failed to apply. `ie4uinit.exe -show` clears it. It
     never affects a recipient, whose path is new.
7. **The Store-Python caveat did not bite.** PyInstaller documents Microsoft Store Python as
   unsupported; it worked on the machine this was built on, where `python311.dll` is
   readable. Worth remembering if the build machine changes — a python.org install is the
   safer base.
8. **Does the execute bit survive the trip to macOS?** Blocking question for the script
   wrapper, and cheap to answer: build the archive on Windows, unpack it on a Mac, check
   whether `Contents/MacOS/run` is still `+x`. If it is not and no archive format fixes it,
   Mac option 3 collapses into option 5.
9. **What exactly does Gatekeeper do to a downloaded script `.app`?** Determines whether Mac
   option 3 is a clean double-click or carries the same Privacy & Security ritual as option 2.
   Needs checking on a real machine with a genuinely quarantined download, not a local copy.
