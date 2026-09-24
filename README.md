# SINE Templater

Builds an FL Studio orchestral template out of a SINE Player project.

You do the part only you can do: load the instruments you want in SINE Player and
pick their articulations. The tool does the mechanical part that follows, which is
otherwise typed in by hand and drifts out of step the moment an instrument is
swapped:

* every instrument is routed to its own mixer insert, and that insert is named
  after the instrument;
* every SINE instance gets a section bus feeding Master, with all of its
  instruments summed into it;
* one **BRSO Articulate** channel is generated per instrument, addressed at that
  instrument, with its articulation grid filled in from the keyswitch notes and
  articulation names already stored inside SINE;
* channels, mixer inserts and channel rack filter groups are named and colored per
  section.

Everything is read out of the `.flp` you point it at. Nothing has to be typed twice,
and nothing has to be told to the tool.

The input project is never modified. A new `.flp` is written beside it.

## What you need

* **FL Studio**, **SINE Player** and **BRSO Articulate**.
* On **Windows**: the build (`SINE Templater.exe`), which needs no Python, or a
  source checkout with **Python 3.10 or newer**.
* On **macOS**: the Mac download, which is the same tool as a folder with a
  `run.command` in it — double-click that and the window opens. There is no Mac
  executable to download; the script uses the Python on your machine, and offers to
  help you install one if there is none. See *Running it on a Mac* below.

The tool uses the standard library only, so there is nothing to install either way.

Built and tested against FL Studio 21.1.1 and 2026 (26.1.6), SINE Player 1.3.0 (both
the VST2 and VST3 versions) and BRSO Articulate 1.17 and 1.33. Other versions are
untested rather than unsupported: the tool reads what the project actually contains
instead of checking version numbers.

## Step 1 — prepare the project in FL Studio

The input is an ordinary `.flp` saved from FL Studio. It has to hold two things, in
this order in the channel rack:

1. **One or more SINE Player instances**, one per section.

   * Name each channel in the rack the way you want the section named: `SINE
     Strings`, `Brass`, `Percussion`. The name is used verbatim for the section bus,
     for the instance's own mixer insert and for the rack filter group, so give it a
     name you want to read in the mixer.
   * Rack order decides everything else: the first SINE instance becomes port 0 and
     takes the first color in the palette, the second becomes port 1, and so on.
   * Inside each instance, load your instruments and choose their articulations in
     SINE's own browser. Give every instrument its own MIDI channel, 1 to 16. Two
     instruments on the same MIDI channel in one instance is an error.

2. **Exactly one BRSO Articulate channel, as the last channel in the rack**, with an
   empty articulation grid.

   This one is the donor. Its settings, its plugin identification and its settings
   tail are what every generated BRSO channel is built from, so set BRSO up the way
   you like it once (hold keyswitch, CC64 release, and the rest) and all of the
   generated channels inherit it. The donor itself is dropped from the output, which
   is why it has to be last and why its own grid can be empty.

   The donor is required. If it is missing, the tool stops rather than guessing.

Do **not** bother setting the mixer up by hand. Mic outputs, mixer insert names,
mixer routing and SINE's MIDI input port are all written by the tool, and anything
you set there yourself is overwritten.

Save the project, then leave it alone while the template is built.

## Step 2 — build the template

### Using the window

1. Start **`SINE Templater.exe`**. You can also drop a `.flp` straight onto it, or set
   it as the "Open with" handler for `.flp` files, to have that project loaded
   immediately. From a source checkout, the same window opens with
   `python -m sine_templater gui`.
2. **SINE project** → *Browse…* and pick the project you saved in step 1.
3. The plan appears as soon as the project is read. **Nothing has been written at this
   point.** Read it: it lists every instance with its port and its mixer block, every
   instrument with the insert it will land on, and the BRSO port/channel pair and
   keyswitch notes each generated channel will carry. Warnings, if there are any, are
   at the top.
4. Optional: tick **Compact mixer** to give each instance only the inserts its
   instruments need (see *Mixer layout* below), or press **Set colors…** to edit the
   section palette. Either one re-derives the plan on the spot.
5. **Save as** and **Folder** are filled in for you with `<project>_template.flp` next
   to the input. Change them if you want it somewhere else.
6. Press **Build template**. If a file of that name is already there you are asked
   before it is replaced. Writing over the input project is always refused.

### Using the command line

From a source checkout:

```
python -m sine_templater build "MyProject.flp" --dry-run
python -m sine_templater build "MyProject.flp"
```

From the Windows build, the same thing with `sine-templater-cli.exe` in place of
`python -m sine_templater`.

`--dry-run` prints the plan and writes nothing, which is how you check a project
before committing to it. Without it, the plan is printed and then the template is
written to `<input>_template.flp`.

| option | what it does |
|---|---|
| `-o`, `--output FILE` | write somewhere other than `<input>_template.flp` |
| `--compact-mixer` | give each instance only the inserts its instruments need |
| `--colors FILE` | use this palette for this run instead of the saved one |
| `--dry-run` | print the plan, write nothing |
| `--force` | overwrite an existing output file |

The output is validated in memory before anything reaches the disk. If validation
fails, nothing is written and the reasons are printed one per line.

## Step 3 — check it in FL Studio

Open the generated `_template.flp`.

* **Channel rack.** Each SINE instance is followed by the BRSO Articulate channels
  generated from it, all in that section's color. The filter groups are numbered
  (`01 SINE Strings`, `02 SINE Brass`, …) so they sort into rack order.
* **Mixer.** Insert 0 is Master. Inserts 1 upwards are the section buses, one per
  instance, each named `<channel name> (main)` and routed to Master. After those come
  the instance blocks: one insert per instrument, named after the instrument and
  routed into that section's bus.
* **Playing it.** Write to the generated BRSO Articulate channels in the piano roll.
  Each one is already pointed at its instrument (the BRSO `port` is the SINE instance,
  the BRSO `channel` is the instrument's SINE MIDI channel minus one), and its
  articulation cells carry the keyswitch notes and names read out of SINE, in the
  order SINE lists them.

If you want the result in FL's *New from template* list, save it into FL Studio's
`Data\Projects\Templates` folder under a category of your choice.

## Mixer layout

Both layouts put Master at insert 0 and the section buses at inserts 1 to K, one per
SINE instance. The instance blocks follow, end to end, from insert K + 1. The SINE
instance itself sits on its section bus, so a block holds instrument inserts and
nothing else. The two layouts differ only in how wide a block is.

**Reserved slots (the default).** Every instance takes 16 inserts, one per SINE MIDI
channel, whether an instrument is on it or not. Unused slots are left unnamed but are
routed to the section bus already, so an instrument added in SINE later on, say on
MIDI channel 9, lands on an insert that is already wired and only needs a name. The
cost is mixer space: 16 inserts plus a section bus is 17 per instance, which fits
**7 instances** on FL Studio 21 and earlier, where the mixer is a fixed 125 inserts,
and **29** on FL Studio 2026, whose mixer grows on demand up to 500. The tool reads
the ceiling off the project you give it, so a 2026 project is not held to the old
limit.

**Compact (`--compact-mixer`, or the checkbox).** A block is exactly as wide as the
instance has instruments, packed in MIDI channel order. Nothing is reserved, so there
is no room for an instrument added by hand afterwards — change it in SINE and re-run
the tool instead. This is what to use when the reserved layout does not fit.

Either way the tool refuses before writing if the layout would not fit in the mixer,
and instruments keep MIDI channel order in both, so the two read the same way from
top to bottom.

## Colors

One color per section, used for the SINE channel, every BRSO channel generated from
it, and its whole mixer block including the section bus. Sections take colors in rack
order and wrap around when the list runs out, so a palette can be any length from one
color upwards.

The palette is a small YAML file:

```yaml
colors:
  - "#c30e0e"   # red
  - "#ffc000"   # amber
  - "#3d85c6"   # blue
```

Keep the quotes. An unquoted `#` starts a comment in YAML, so `- #c30e0e` is an empty
list entry rather than a color.

Where it lives:

* **Windows build** — `channel_colors.yaml` beside the executable. It is put there on
  first run, so copying the whole folder somewhere else takes your colors along.
* **Source checkout** — `%APPDATA%\SINE Templater\channel_colors.yaml` once you have
  saved one, and the shipped `sine_templater/channel_colors.yaml` until then. Saving a
  palette never edits the file in the checkout.

**Set colors…** in the window edits that file: click a chip for the system color
picker, or type into the box beside it (`#c30e0e`, `c30e0e`, `#c00`, `195, 14, 14` and
`rgb(195, 14, 14)` are all accepted). On the command line, `--colors FILE` points at a
different palette for one run without touching the saved one.

A palette that cannot be read is an error, never a silent fall back to the defaults.
It is also read before the project is, so a typo in it costs nothing.

## When it refuses

Every refusal names what is wrong. The common ones:

| message | what to do |
|---|---|
| no SINE Player instances found | you picked the wrong project |
| no BRSO Articulate channel found | add one empty BRSO Articulate as the last rack channel |
| expected exactly one BRSO Articulate channel, found *N* | you pointed it at a template it already built, or at a project with hand-built BRSO channels; point it at the source project |
| the donor must be the last channel in the rack | move the BRSO channel to the end |
| needs mixer inserts up to *N*, but FL only has *M* | the layout does not fit this project's mixer — use the compact layout, or split the project. *M* is 125 for a project saved by FL Studio 21 or earlier and 500 for one saved by FL Studio 2026; saving the project in 2026 raises it |
| more than one instrument on MIDI channel(s) *N* | two instruments in one SINE instance share a MIDI channel; change one in SINE |
| on MIDI channel *N*; only 1..16 are supported | SINE addresses 16 MIDI channels per instance |
| channel *N* ... is a plugin channel whose state is not a SINE Player's | the project holds another plugin; the input takes SINE instances and the BRSO donor and nothing else |
| *file* is not a project this tool can read | the file is truncated, or is not an `.flp` |
| *file* exists | choose another name, or pass `--force` |
| refusing to write over the input file | the input is never overwritten, on purpose |

Two things are reported as warnings rather than refusals:

* two articulations of the same instrument carrying the same keyswitch note in SINE.
  Both grid cells answer to that note and only the first one can be selected.
* an articulation name BRSO cannot store as it stands. Its cells are plain ASCII, so
  `Legato – slow` goes in as `Legato - slow` and an accent is written without it.

The template is written either way, and the warning says exactly what it found; fix
it in SINE if it matters.

## What the tool clears

FL stamps every project it saves with the registration of the installation that
saved it. Left alone it would travel with the file, so **the generated template
has that stamp emptied**. Nothing is lost by it: FL does not read the field back,
and the first time anyone opens and saves the template, FL writes their own
registration into it — which is what it should say anyway.

The **project author** is left exactly as the input had it. FL does not rewrite
that one on save, so a name there was put there deliberately. If you do not want
your name on a template you hand to someone else, clear it in FL's project info
before building, or in the template afterwards.

Nothing else in the project is touched that the plan does not describe.

## Re-running

Swapping an instrument is: change it in SINE, save, run the tool again on the source
project. The BRSO grids, the mixer names and the routing are all regenerated
together, so they cannot drift apart.

Re-run it on the **source project**, not on a template it produced — a template
already has its generated BRSO channels, and those are not donors.

## Working on the tool

The repository carries everything needed to build on it:

* `docs/FORMAT.md` — what is known about the `.flp` container, the Fruity Wrapper
  chunks, SINE's embedded state and BRSO's, and how each finding was established.
* `docs/SPEC.md` — what the tool does with all that: the input contract, both mixer
  layouts, the decisions taken and the open items.
* `docs/UI.md` — the window, freezing the executables, and the distribution question.
* `tests/` — `python -m pytest` from the repository root. Tests that need a real FL
  Studio project look in `testfiles/`, which is not tracked; those tests skip when it
  is absent rather than failing, so the skip count tells you what did not run. See
  `tests/paths.py`.

## Running it on a Mac

Unpack the download and **double-click `run.command`**. A Terminal window opens behind
the SINE Templater window and stays there while the tool is running; closing it closes
the tool.

The first run may stop and tell you that Python is missing. Python is what the tool is
written in, and macOS no longer ships a usable copy — Apple's `/usr/bin/python3` is a
placeholder that asks to install 700 MB of developer tools. So `run.command` offers to
sort it out:

* if you have **Homebrew**, it offers to install the missing piece with it. This needs
  no password and touches nothing else;
* otherwise it offers to **open the Python download page**. Download the macOS
  installer for the latest release, run it with the default options, come back to the
  Terminal window and press Return. That installer includes everything the window needs,
  and it leaves any Python you already have alone.

Answering **no** to either is fine. It prints the address and stops, and you can install
Python yourself and double-click the file again.

The script never installs Homebrew and never asks for your administrator password. If it
ever does ask for one, something is wrong and you should not give it.

Two things to expect on a Mac, neither of them a fault:

* macOS may ask whether you are sure about running something downloaded from the
  internet. That is Gatekeeper, and it applies to anything unsigned;
* if double-clicking `run.command` does nothing at all, the file lost permission to run
  when it was unpacked. `chmod +x run.command` in Terminal, once, fixes it.

The command line works the same as on Windows, from the unpacked folder:

```
python3 -m sine_templater build "MyProject.flp" --dry-run
```

## Building the Windows executables

```
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

PyInstaller is installed into a throwaway virtual environment under `.venv-build`, so
nothing lands in your system Python and deleting that folder undoes everything the
script did. The result is `dist\SINE Templater\`, holding

* `SINE Templater.exe` — the window, with no console behind it;
* `sine-templater-cli.exe` — the command line, with the flags above;
* `_internal\` — shared by both, so the interpreter is only shipped once;
* `LICENSE` and `README.md` — copied in by the build script, so a folder handed to
  someone else carries them.

Zip that folder to hand it to someone else. The script starts both executables before
calling the build good, so a trimmed build that no longer runs fails there rather than
on somebody else's machine.

## Building the Mac download

```
python packaging\build_macos_zip.py
```

Writes `dist\SINE Templater macOS.zip`: the package, `run.command`, `LICENSE` and
`README.md` under one folder. Nothing is frozen and nothing is compiled, so this builds
on Windows.

Build it with the script rather than by zipping the folder by hand. A zip written by
Windows Explorer does not carry Unix permissions, and `run.command` then arrives on the
Mac unable to run — double-clicking it does nothing, with no error to explain why.

## License

MIT — see [LICENSE](LICENSE). FL Studio, SINE Player and BRSO Articulate are separate
products with their own licenses; this tool only reads and writes project files.
