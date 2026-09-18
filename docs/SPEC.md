# sine-templater — implementation spec

Companion to `FORMAT.md` (which holds the file-format findings) and `UI.md` (the window
and packaging). This document is the *intent*: what the tool does, and the confirmed
reference example it must reproduce.

## The idea

You do the part only a human can do — choosing instruments and articulations — in SINE's
own browser. The tool does the mechanical part.

### Input contract

An `.flp` saved from FL Studio containing, in channel-rack order:

1. **One or more SINE Player instances**, VST2 or VST3 (both containers are supported),
   named (`SINE Strings`, `SINE Brass`, …), each with its instruments loaded and articulations
   chosen. Rack order determines port and mixer block assignment — first instance gets port 0.
   The channel name is used verbatim for the section bus, the filter group and (with a
   `(main)` suffix) the instance's own output insert — so name them meaningfully.
2. **Exactly one BRSO Articulate as the last channel**, left empty.

Nothing else is required. Mic outputs, mixer names, mixer routing and the SINE MIDI input
port are all set by the tool and should not be touched by hand.

**Why BRSO must be in the template rather than bundled with the tool:** its
settings tail carries the KV block (`cc64release`, `allchannelomni`,
`optimiseautomation`, `holdkeyswitch`, …). Taking it from the template means BRSO is
configured once, by the person who will use the result, and every generated instance
inherits that — and it can never go stale against a future FL or BRSO version, which a blob
bundled in the repo would. The donor also supplies the 43-event FL channel block (plugin
identification, wrapper metadata).

The donor's own articulation grid is discarded, so it can be completely empty. Only the tail
and the channel scaffolding are used.

This design paid off the first time BRSO gained a setting: the tail grew and its contents
shifted, and because the donor comes from the project being built, the new layout was
carried across as-is. Only the tool's assumptions about *where* things sit had to be
relaxed. A blob bundled in the repo would have been silently wrong.

The donor is **required**, not a fallback. If it is missing the tool errors out. A silently
stale bundled donor would be worse than a clear failure. The tool validates it structurally:
the KV block must parse and must end exactly at the end of the tail, and every long-standing
setting must be present. New settings added by future BRSO versions are accepted.

It must be **last** in the rack because the tool drops it after harvesting, and removing the
highest-index channel requires no renumbering — which keeps the tool entirely off the
untested channel-reordering path.

### Output

The same `.flp`, rewritten so that everything is wired. For each SINE instance (`i` = its
rack ordinal, 0-based), with `base` = its mixer block start:

1. Set the SINE instance's wrapper MIDI input port to `i`, and its FL channel → its section
   bus insert.
2. For each instrument in it (`c` = its 1-based SINE MIDI channel):
   - set every mic position's `outputBusIndex` to `c`, so it emits on SINE output `c`;
   - point wrapper output `c` at the instrument's insert, and name that insert after the
     instrument (which insert that is depends on the layout — see below);
   - append a **BRSO Articulate** channel named after the instrument, with `port = i` and
     `channel = c - 1`;
   - fill its articulation grid from the instrument's articulations — one contiguous cell
     each, carrying the keyswitch note and verbatim title read from SINE.
3. Name and route the whole block, reserved slots included where there are any (see below).

Everything above is derivable from the SINE state already in the file. Nothing has to be
asked for, and nothing has to be typed twice.

### Mixer layout

Two layouts, chosen with `--compact-mixer`. Both put Master at insert 0, one section bus per
instance at inserts `1..K`, and then the instance blocks laid end to end from `K + 1` with no
gaps. They differ only in how wide a block is.

**The SINE channel itself sits on its section bus**, not on an insert of its own, so a block
holds instrument inserts and nothing else. Whatever reaches SINE's main output — an
instrument added by hand inside SINE, an audition from its browser — lands on the section bus
alongside the summed instruments.

**Reserved slots (default).** A block is **16 inserts** wide, one per SINE MIDI channel, so an
instance costs 17 with its section bus.

```
insert 0                Master
inserts 1..K            section buses, one per SINE instance → Master
                        (the SINE channel itself sits here)
base + 0 .. base + 15   instrument slots, MIDI channel 1..16 → section bus
```

with `base(i) = K + 1 + 16*i`, and the instrument on MIDI channel `c` always at
`base + c - 1`.

**Every insert in a block is routed to the section bus, including unused slots**, and unused
slots are left unnamed. That is deliberate: adding an instrument in SINE later on, say on MIDI
channel 9, means it immediately lands on an insert that is already correctly routed — only the
name is missing. Reserving all 16 rather than sizing to the current instrument count is what
makes the template extensible by hand.

**Compact (`--compact-mixer`).** A block is `len(instruments)` wide, packed in MIDI-channel
order. Nothing is reserved, so there is no room for an instrument added by hand afterwards —
re-run the tool instead.

```
base + 0 .. base + n-1  one insert per instrument, in MIDI-channel order
```

What makes this work is that the insert an instrument lands on is *not* fixed by its MIDI
channel — it is whatever wrapper chunk 32 says. The SINE JSON is left alone (an instrument
still emits on the output matching its MIDI channel, and `validate` still checks that), and the
output table is remapped instead: outputs with instruments behind them point at packed slots,
every other output stays at offset `0` and folds back onto the instance's own insert. So an
unused output can never leak into a neighboring section's block.

On the six-instance reference this takes the highest insert used from 102 down to 42.

Instruments keep MIDI-channel order in the mixer under both layouts, so the two read the same
way top to bottom — only the gaps differ.

### Naming

The SINE channel name is used verbatim everywhere. The section bus is marked `(main)`,
because that is where the whole block lands and where the SINE channel itself sits:

| channel name | section bus | filter group |
|---|---|---|
| `SINE Strings` | `SINE Strings (main)` | `SINE Strings` |
| `Main Lead` | `Main Lead (main)` | `Main Lead` |

Instrument inserts take the instrument's own name, read from SINE. No prefix stripping —
whatever a channel is called is what appears in the mixer and the rack filter.

Capacity depends on the FL version, because the mixer does. Up to FL 21 it is a fixed 125
usable inserts, which with reserved slots — a section bus plus a 16-wide block per instance —
caps it at **7 SINE instances**. From FL 2026 the mixer grows on demand up to 500, which raises that to
**29**. Compact blocks are sized to their contents instead, so the limit becomes
`K + Σ instruments ≤ ceiling`: 20 instances of 3 instruments fit inside the old 125, never
mind 500. Either way the tool reads the ceiling off the project it was given, errors
before writing if the layout would not fit, and catches the reserved-slot case up front,
before the (large) plugin states are parsed.

### Channel rack filter groups

One group per SINE instance, named after the SINE channel, containing the instance and its
generated BRSO channels. Two traps, both hit during testing:

- The group name list (event 231) has to be written at all — a fresh project defines only
  `Unsorted`, so per-channel group indices alone point at groups that do not exist and those
  channels vanish from every filter except `All`.
- **FL sorts the names**, and a channel's group index refers to the sorted position rather
  than file order. The names are therefore numbered — `01 SINE Strings`, `02 SINE Brass`, … —
  so sorted order equals port order, which is exactly what the hand-built reference does.
  Both `apply` and `validate` refuse to write a list that is not already in sorted order.

### Colors

- **Channel color** — one per SINE instance, shared by the instance and all of its generated
  BRSO channels, so the rack reads as colored sections.
- **Mixer insert color** — the whole block plus its section bus take the instance color.
  Requires both event 149 (the value) and event 42 (the enable flag).
- **Articulation color** — BRSO per-cell array 5; all articulations within one instrument get
  distinct values. A palette of small integer *indices*, unrelated to the RGB above, and still
  hardcoded in `plan.py`.

**The section palette lives in `sine_templater/channel_colors.yaml`**, not in the code, so
colors can be tried out without editing Python. A `colors:` key holding a list of `"#RRGGBB"`
strings, in section order; `--colors FILE` points at another one. The list may be any length —
section *N* takes color *N % len* — so it cycles rather than running out, which matters now
that compact blocks allow well past 12 instances.

Parsed by a small in-tree reader rather than PyYAML: the project has no third-party
dependencies and no packaging to declare one in, so requiring PyYAML would break
`python -m sine_templater` until it was installed. Block lists, quoted or bare scalars and `#`
comments are supported; anything else — flow style included — is rejected with file and line
rather than guessed at. The one trap is that `- #c30e0e` unquoted is a *comment* in real YAML,
so that case is detected specially and the error names the fix.

A bad or missing colors file is an error, never a silent fall back to a built-in palette —
editing the file wrong should say so, not quietly build with the old colors. It is read before
the project, so a typo costs nothing.

Byte order is `R, G, B, 0`; see `FORMAT.md`. This was wrong at first and shipped to FL.

**Why this is the right shape:** the keyswitch numbers, articulation names, MIDI channels and
instrument names all already exist inside SINE's own JSON in the `.flp`. Without the tool
all of it is re-entered by hand into BRSO and the mixer. The tool just stops the
duplication.

Swapping an instrument then becomes: change it in SINE, re-run the tool. The BRSO grid, the
mixer names and the routing are regenerated together, so they cannot drift apart.

## Confirmed reference example

The shape below was read directly out of the hand-built reference project, and it is what
the generator must reproduce. Instrument and section names are illustrative — the wiring is
what was verified, and none of it depends on what anything is called.

### The chain, end to end

```
FL channel 0  "SINE Strings"  (Fruity Wrapper → SINE Player)
  ├─ wrapper MIDI input port = 0
  ├─ 16 stereo outputs enabled
  ├─ FL channel → mixer insert 11
  └─ SINE JSON, instruments[0]:
         title        "Low Strings"
         midiChannel  "1"
         micPositions SPOT → outputBusIndex "1"
                      TREE → outputBusIndex "1"
         articulations
             sustains      "Sustains"     mainOptions.switchValue "24"
             spiccato      "Spiccato"     mainOptions.switchValue "25"
             marcatolong   "Marcato long" mainOptions.switchValue "26"

FL channel 1  "Low Strings"  (BRSO Articulate)
  ├─ FL channel → mixer insert 0   (makes no audio)
  ├─ port    = 0     → addresses the SINE Strings instance
  ├─ channel = 0     → addresses SINE midiChannel 1 (0-based!)
  └─ articulation grid:
         cell 0  keyswitch 24   name "Sustains"
         cell 1  keyswitch 25   name "Spiccato"
         cell 2  keyswitch 26   name "Marcato long"

Mixer
  insert 12  "Low Strings"         ← SINE output bus 1     → sends to insert 1
  insert 11  "SINE Strings"        ← SINE main out (bus 0) → sends to insert 1
  insert  1  "Strings"                                     → sends to insert 0 (Master)
```

Keyswitch notes 24/25/26 read as **C0 / C#0 / D0** in FL (middle C = C3 convention). The
tool works in raw MIDI note numbers; note naming is display only.

### One instance, as built

Six instruments on MIDI channels 1–6, with a 10-insert block starting at insert 11:

| SINE MIDI ch | bus | insert | BRSO port / channel |
|---|---|---|---|
| 1 | 1 | 12 | 0 / 0 |
| 2 | 2 | 13 | 0 / 1 |
| 3 | 3 | 14 | 0 / 2 |
| 4 | 4 | 15 | 0 / 3 |
| 5 | 5 | 16 | 0 / 4 |
| 6 | **0** | **11** | 0 / 5 |

The last row is not a layout variant — it is a defect, and it is the reason for the next
section.

### What the hand-built template got wrong

Worth stating, because it is the concrete evidence that the manual process does not hold
together, and it is exactly what the tool eliminates:

1. **An instrument that was never routed.** The instrument on MIDI channel 6 still has its
   mic positions on `outputBusIndex 0`, so it plays out of the SINE main output onto the
   instance's own insert instead of its own. The insert it should have been on carries the
   name of an instrument that was swapped out long ago, and nothing routes to it.
2. **BRSO articulation names drifted from SINE.** One instrument's second articulation is
   `Spiccato` in SINE and `Staccato` in BRSO. The keyswitch is right, so it plays correctly
   and the wrong label is invisible until you read it. The same Sustain/Staccato/Marcato
   naming had clearly been copied across many instruments regardless of what SINE held.
3. **Stray grid cells.** Two BRSO channels carry an extra occupied cell with a keyswitch and
   no name, left over from editing. Harmless, and a clear sign the grid is maintained by
   hand.

All three are the same failure: the wiring and the labels are maintained separately, so they
drift the moment an instrument changes. Generating both from the SINE state makes that
impossible by construction.

## What the generator writes

### 1. SINE JSON (per instrument)

Only one field per mic position changes:

```
instruments[n].micPositions[*].outputBusIndex = str(instruments[n].midiChannel)
```

Re-serialize with `json.dumps(obj, indent=4) + "\n"` — byte-exact — then update the uint32
length at offset 9 of the 21-byte chunk header **and** the wrapper chunk's own uint64 length.

Read-only inputs from the same JSON: `title`, `midiChannel`, and per articulation
`title` + `mainOptions.switchValue`.

The JSON is identical under both mixer layouts — an instrument always emits on the output
matching its MIDI channel. Where that output *goes* is wrapper chunk 32's business, alongside
it in the same event: 16 `(offset, enabled, reserved)` int32 triples, offset relative to the
channel's own insert. Reserved layout writes the identity mapping; compact writes the packed
one. Also in the same wrapper: chunk 1's first int32, the MIDI input port.

### 2. BRSO Articulate state (one new channel per instrument)

Structure, fully verified across all 36 instances in the reference file:

```
uint32   version    = 10
uint16   cells                          # last occupied grid cell + 1
int32[cells] × 6                        # six parallel per-cell arrays
pascal[cells]                           # articulation names, "" for empty cells
int32[cells]                            # zeros
uint16   bankCount  = 16
pascal[16]                              # bank names, normally all ""
byte[...]                               # settings tail, length varies by version
```

`pascal` = `uint16` length + ASCII bytes.

The six per-cell arrays:

| array | meaning | value to write |
|---|---|---|
| 0 | **keyswitch note**, `-1` = empty cell | SINE's `mainOptions.switchValue` |
| 1 | velocity | `127` |
| 2, 3, 4 | unused rule slots | `-1` |
| 5 | piano-roll color index | `55` (default; observed 4, 10, 28, 231 elsewhere) |

The grid is **4 columns wide** (cells 0–3 = row 0, 4–7 = row 1, …). The reference template
places most articulations one per row (cells 0, 4, 8, 12), but **contiguous placement is
confirmed to work** — instruments with 5 and with 10 articulations do it, in cells 0–4 and
0–9. So: **write articulation `k` to cell `k`, and set `cells` = number of
articulations.**

The tail is identical across all 36 instances of one version except for six bytes, of which
only two matter. Their offsets move between BRSO versions, so they are located by key name:

| setting | write |
|---|---|
| `port` | SINE instance index (0-based) |
| `channel` | SINE `midiChannel - 1` (0-based) |
| `preserve`, `page`, `editing`, plus a byte at tail offset 2 | UI state; copy from donor |

(In the versions seen so far `port` and `channel` happen to land at the same two offsets,
because the settings added since were inserted after them. Do not rely on that.)

So the tail is taken verbatim from a donor BRSO instance and two int32s are patched,
found via `brso.kv_offsets()` rather than at hardcoded offsets.

### 3. FL channel for the new BRSO instance

A BRSO channel is a fixed 43-event block. Clone it from a donor and change only:

| event | meaning |
|---|---|
| 64 | channel index |
| 203 | channel display name (the instrument name) |
| 213 | the BRSO state built above |
| 128 | channel color |
| 132 | two `uint16`s, both = channel index + 1 |
| 145 | channel-rack filter group |
| 212 | plugin window geometry (cosmetic) |
| 22 | mixer insert — **stays 0** for BRSO |

Event 22 on the *SINE* channel is the one that carries the insert — the instance's `base`.
Every wrapper chunk-32 offset is relative to it, so if this is wrong the whole block shifts;
`validate` checks it explicitly.

### 4. Mixer

- Insert name = event 204, which **precedes** that insert's own `236` parameter block.
- Insert send routing = event 235, a byte array indexed by destination insert.
- Up to FL 21 all 127 insert blocks exist in the file regardless, so the tool only renames
  and re-routes. From FL 2026 the mixer is only as large as the project made it, so `apply`
  appends insert blocks and raises the count in event 103 when the layout needs more.

### 5. Project metadata

Event 200 is FL's registration stamp: the registration of whichever installation
last saved the project. It is emptied on write — an empty text payload, not a
deleted event — so a template does not carry the registration of whoever generated
it. The field is not load-bearing: a project whose event 200 is empty, or missing
altogether, opens, renders and saves, and the save puts the saving installation's
own registration back. Confirmed in FL Studio on all three shapes; see `FORMAT.md`.

Event 207, the project author, is **not** touched. FL does not rewrite it on save,
so unlike the stamp it survives indefinitely, which means a value there is a
deliberate act by whoever set it and not ours to discard.

`validate` refuses to write an output whose stamp still carries a value.

### 6. Write-out

Re-serialize the whole FLP. The parser round-trips both reference files byte-for-byte, so any
diff in the output is a change the tool made deliberately.

## Proven in FL Studio

Confirmed by opening each file in FL Studio.

**A fully generated template works.** A VST3 SINE project (3 instances, 6 instruments) built
by `sine_templater` opens correctly, every instrument reaches its own mixer insert, and the
mixer is colored per section. That covers the whole pipeline: plan derivation, SINE JSON
rewrite in the VST3 container, wrapper output fan-out, generated BRSO channels, and mixer
naming, routing and coloring.

Mixer routing, insert colors and channel-rack filter groups are all confirmed working.

**`--compact-mixer` is confirmed too**, on the 6-instance reference: instruments reach their
own inserts with the block sized to its contents. Verified three ways before it went to FL —
the default-mode output is byte-identical to the pre-change build; an audit script re-derived
the whole chain from the output bytes alone (SINE bus → chunk 32 offset → channel insert →
insert name → routing → section bus → Master, plus BRSO port/channel → instrument) for both
layouts; and a test asserts the two layouts agree on everything except insert numbers.

Five things had to be fixed to get there. None could have been caught by round-trip or
symmetry checks, because in every case the reference file was already correct — they were
fields the hand-built template had set by hand, or conventions FL applies on read:

- **Wrapper chunk 32** — every output offset is `0` in a fresh project, so all 16 outputs sum
  onto the instance's own insert. `outputBusIndex` looked correct and nothing worked. Output
  *N* must be given offset *N*.
- **Event 42** — the insert color enable flag. Writing the color event (149) alone leaves
  the insert gray.
- **Events 147 / 149 / 42 describe the *next* insert**, like the name event, so colors were
  landing one insert late. Invisible inside a uniformly colored block; only the section buses
  exposed it.
- **FL sorts filter-group names** and indexes them by sorted position, so the names must be
  numbered to keep sorted order equal to port order.
- **Color byte order is `R, G, B, 0`**, not a little-endian uint32 of `0xRRGGBB`. Red and blue
  were swapped from the very first build and nobody noticed, because the placeholder palette
  had been chosen to look plausible either way round. Caught only once real colors went in.

All five are now written correctly and asserted by `validate.py`, the last of them additionally
by a round-trip over every color event in the reference — i.e. against bytes FL itself wrote,
rather than against our own assumption, which is what was missing the first time.

The earlier component write tests:

**Modifying existing state.** One instrument's two mic positions were moved from
`outputBusIndex "0"` to `"6"`, and the mixer insert it should land on was renamed — to a
longer name, so the event changed size. The file opened; the rename was visible; the audio
moved from the instance's insert to the instrument's own. Confirms: FLP re-serialization,
SINE JSON rewrite with both length fields updated, and variable-length event rewrite.

**Generating new state.** A BRSO channel was appended to the rack with a state **built from
scratch** — 5 contiguous cells, consecutive keyswitches, verbatim SINE articulation titles,
`port` and `channel` set to address a specific instrument. Only the settings tail was
reused, taken from a donor at a different port and channel and then patched, so the patch
path was exercised rather than a straight copy. `nChannels` was bumped to match. The file
opened and BRSO showed all 5 articulations correctly. Confirms: channel append, the
`nChannels` bump, and that the BRSO layout above is complete and correct enough to
synthesize from nothing.

## Implementation

Python 3.11, standard library only (`pytest` is a dev dependency for the tests).

```
sine_templater/
  flp.py                event-stream codec (FLhd/FLdt, varint payloads, colors)
  wrapper.py            Fruity Wrapper chunk codec
  sine.py               SINE state: 21-byte header + JSON, both length fields
  brso.py               BRSO state: parse, build from scratch, donor validation
  model.py              read-only view: channel blocks, mixer inserts
  plan.py               Project -> Plan (pure, no mutation); insert layout, palette
  apply.py              Plan -> mutated Project
  validate.py           re-parses the output and checks the wiring holds
  cli.py
  channel_colors.yaml   the section palette, editable without touching code
tests/
  paths.py            where the reference projects live, and what is optional
  make_fixture.py     builds a contract-shaped input from the reference project
  test_codecs.py      codec symmetry over every real blob, color round-trip
  test_end_to_end.py  full build + validation, both mixer layouts
  test_layout.py      insert allocation, on synthetic plans
  test_palette.py     the YAML subset reader and the color parsing
  test_mixer_2026.py  the FL 2026 container: wide insert field, growing the mixer
  test_robustness.py  malformed input, truncated blobs, refusals
```

Tests that need a real project skip when it is absent rather than failing, so the skip
count in pytest's summary is the honest measure of what ran.

```
python -m sine_templater build input.flp [-o output.flp]
                                         [--compact-mixer] [--colors FILE]
                                         [--dry-run] [--force]
```

Never writes in place; refuses to overwrite without `--force`; validates before writing, so a
failed check leaves no file behind. `--dry-run` prints the derived plan and writes nothing.

**Design rule: surgical edits, never reconstruction.** The FLP is parsed to a flat event list,
only the intended events are touched, and it is re-serialized. Any byte that differs between
input and output is therefore a deliberate change. Every nested codec holds
`serialize(parse(x)) == x` on all real blobs in the reference files (6 SINE states, 36 BRSO
states), asserted in the test suite — that is the guard against having misread a format.

`apply` runs four passes ordered so none invalidates the next one's positions: SINE channel
edits in place → donor replaced by generated BRSO channels → mixer rewritten by a stateful
walk (it may insert name and color events, so it cannot use precomputed positions) → filter
groups, last, because writing them shifts every offset ahead of the channels.

**Insert numbers are assigned in exactly one place**, `plan._assign_inserts`, for both layouts.
A compact block's width is only known once its instruments have been parsed, so blocks are
placed after the whole rack is read rather than at a computed stride. `validate` then checks
the layout against itself — no insert claimed twice by two blocks, the buses or Master, and no
instrument sitting outside its own block — as a guard against exactly the silent
off-by-one-block mis-mapping this restructuring could have introduced.

## Decisions taken

- **Append order**, not interleaved. Generated BRSO channels all go at the end of the rack.
  Channel-rack filter groups still give the visual grouping. Interleaving would require
  renumbering existing channels, which is untested and is the one remaining real risk, since
  channel indices may be referenced by pattern/automation data elsewhere in the file.
- **Verbatim SINE articulation titles** in BRSO (`Marcato long`, not `Marcato Long`). Keeps
  the names honest and drift-free by construction.
- **16 instrument slots per instance by default**, unused ones reserved and pre-routed;
  `--compact-mixer` opts out. Reserved stays the default because it is what makes the template
  extensible by hand, and because it was the behavior already confirmed in FL — the flag is
  additive, and its absence reproduces the previous output byte for byte.
- **Donor BRSO comes from the template**, is required, and must be the last channel.
- **The section palette is config, the articulation palette is not.** Section colors are the
  ones a user actually wants to fiddle with; BRSO's per-cell indices are an inference nobody
  has confirmed in BRSO's UI yet (open item 1), so exposing them would invite tuning a value
  whose meaning is unverified.
- **No third-party dependencies**, including for YAML. See "Colors".

## Open items

1. **Articulation color is still an inference.** BRSO per-cell array 5 is written with
   distinct values per articulation, and mixer/channel colors are confirmed working, but
   nobody has checked in BRSO's own UI that array 5 is really the piano-roll color. If it
   turns out to be something else, write the default 55 everywhere instead.
2. **Idempotency.** Re-running on an already-generated file is undefined. The input contract
   assumes a clean template; decide later whether to detect and replace previously generated
   BRSO channels rather than requiring a fresh input. Note that switching mixer layouts means
   re-running, so this matters more now than it did.
3. **SINE MIDI channel 16 has no plugin output — so an instance holds at most 15
   instruments.** Wrapper chunk 32 holds 16 entries, outputs 0–15, and chunk 30 says the same
   independently (`(0, 16, 0, 0)`). Output 0 is the main out, which goes to the instance
   itself, leaving buses 1–15 for instruments. But `plan.py` accepts MIDI channels 1–16, so an
   instrument on channel 16 gets an insert, a name and a route, and nothing feeding it — under
   either layout. Pre-existing, not caused by compaction: it only became visible because the
   offset table had to be written out explicitly instead of hardcoded to the identity.
   No test project comes near it — the highest MIDI channel any of them uses is 9.

   `validate` now reports it and refuses to write, rather than producing a silently dead
   insert. Three things to settle:

   - **What SINE actually does with `outputBusIndex "16"`** — clamp to 15, fall back to the
     main out, or stay silent. Untested in FL, and it changes the symptom: a fallback to the
     main out means the instrument is audible but summed onto the instance's insert, which is
     a different bug from a dead insert.
   - **Whether to reject channel 16 in `plan.py`**, naming the instrument, so it fails in the
     first second instead of after a megabyte of SINE state has been parsed and with a message
     about inserts and outputs. Recommended; it is a two-line change.
   - **Whether the reserved stride should drop from 17 to 16.** The reserved layout
     currently reserves a slot that can never be fed. It buys no capacity on a fixed mixer
     — `125 ÷ 17` and `125 ÷ 16` are both 7 instances — so this is tidiness, not a fix.
     On a dynamic mixer it would move 29 to 31.
4. **Channel reordering** remains untested — generated BRSO channels are appended, never
   interleaved. See "Decisions taken".
5. **Adding articulations to SINE** is out of scope by design — they are chosen in SINE's
   own browser. The tool only *reads* them. (`Library.json` at
   `%LOCALAPPDATA%\Orchestral Tools\SINE Player\` holds the full catalog if generating
   instruments ever becomes desirable.)
6. ~~**Scope:** currently a CLI script; application shell can come later if wanted.~~
   **Settled:** there is a window and a frozen Windows build, both over the same `core`
   pipeline. See `UI.md`.
