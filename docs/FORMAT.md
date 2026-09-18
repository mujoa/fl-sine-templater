# File-format findings

Everything the tool knows about the formats it reads and writes, and how each piece of it
was established. Companion to `SPEC.md` (what the tool does with all this) and `UI.md`
(the window and packaging).

Goal: programmatically generate/modify FL Studio orchestral templates that wire up
**Orchestral Tools SINE Player** + **BRSO Articulate**, so that adding, swapping, or
re-articulating an instrument does not mean hours of manual GUI clicking.

## The problem being solved

Wiring one instrument by hand currently requires four separate manual steps:

1. Create a BRSO Articulate channel, set its **port** and **channel**.
2. Load the instrument in the matching SINE instance, set its **MIDI channel** to match.
3. Inside BRSO, map every articulation to a keyswitch note — by hand, one per articulation.
4. Inside SINE's mixer, set each mic position's **output bus** so it lands on its own FL mixer insert.

Multiply by ~36 instruments. Worse, it is not modifiable: swapping an instrument that has a
different articulation count invalidates the BRSO keyswitch map and the naming of the mixer inserts.

Everything above is stored in the `.flp`. All of it is machine-readable and machine-writable
(see "Verified file-format findings").

## How these findings were made

None of these formats is documented. Everything below was read out of real projects saved
from FL Studio, and each note says whether a reading is confirmed or still an inference —
that distinction is the point of this document.

The method that did most of the work was an A/B: one hand-built template saved twice,
identical except for a single instrument swapped inside one SINE instance for another with
a different articulation count. Everything else — the other instances, every BRSO channel,
the whole mixer — is byte-for-byte the same, so any byte that moved belongs to the thing
that changed. That is how the per-cell BRSO arrays and the keyswitch field were pinned
down.

The test suite reads projects of this shape from `testfiles/`, which is not tracked; see
`tests/paths.py` for what it wants. Instrument and section names in the examples below are
illustrative, and no finding depends on them.

## Template shape

A hand-wired template of this kind is one SINE Player instance per section — strings,
brass, woodwinds and so on — each holding several instruments on distinct MIDI channels,
plus one BRSO Articulate channel per instrument. Six sections and thirty-odd instruments
come to forty-odd FL channels, with one channel-rack filter group per section to keep the
rack navigable.

**The wiring rule — this is the whole template in one line:**

```
BRSO.port == SINE instance index          (0-based, both sides)
BRSO.channel == SINE instrument.midiChannel - 1
SINE micPosition.outputBusIndex == instrument.midiChannel
FL mixer insert == instance_block_base + outputBusIndex
```

`outputBusIndex 0` (SINE main out) lands on the block's first insert; an instrument on MIDI
channel N routes to block base + N. Each instrument's mic positions all share one bus, i.e.
mics are summed per instrument rather than split out.

The hand-built reference uses a 10-insert stride per instance, with the instance's own main
output on the block's first insert. The generator lays the mixer out differently: the SINE
channel sits on its section bus, so a block holds instrument inserts only — 16 of them by
default, one per SINE MIDI channel, so slots can be left reserved and pre-routed for
instruments added later by hand, or exactly as many as there are instruments with
`--compact-mixer`. See `SPEC.md` for both.

Mixer layout: insert 0 = Master; then one section bus per instance → Master; then the
instance blocks laid end to end, every insert in a block routed to its section bus.

**Hand-wiring drifts, and that is the whole reason for the tool.** In the reference
template several inserts still carry the name of an instrument that has since been swapped
out, one instrument's mic positions were never moved off the main output so it plays onto
the instance's own insert rather than its own, and some BRSO articulation names no longer
match what SINE calls them. Insert names and grid labels are cosmetic, so nothing in FL
notices when they desync — they simply stop being true.

## Verified file-format findings

All of the below was verified directly against the two `.flp` files, not assumed.

### FLP container

`FLhd` header (`int16 format, uint16 nChannels, uint16 ppq`) + `FLdt` event stream.
Events are `uint8 id` + payload sized by id: `<64` → 1 byte, `<128` → 2, `<192` → 4,
`>=192` → varint length then bytes. One exception: FL 2026 writes event 172 with a
three-byte payload, which fits no size class, so the parser carries an explicit width for
it. What it holds is unknown and nothing here needs it — it only has to be stepped over
without losing alignment, and `parse` insists on landing exactly on the end of the chunk.

**The mixer's size is version-dependent.** Up to FL 21 every project carried a fixed 127
insert blocks: Master, inserts 1–125, and a trailing block for the "current" insert, which
is not one of them. From FL 2026 on the mixer grows on demand — a new project has 16
inserts and it goes up to 500 — and the project records how many it has in event 103. A
file without event 103 has the fixed mixer and cannot be grown; one with it can be, by
appending insert blocks and raising the count.

**Re-serializing a parsed file reproduces the original byte-for-byte** (confirmed on both
files). No unknown padding, no checksums. A read/modify/write tool is viable.
`pyflp` is *not* installed and is not needed — the parser is ~30 lines.

Event ids that matter:

| id | meaning |
|---|---|
| 64 | new channel (index) |
| 22 | channel → mixer insert, one byte, FL 21 and earlier (SINE = its block base; BRSO = 0) |
| 104 | the same field from FL 2026 on: two bytes, so it can address 500 inserts |
| 200 | registration stamp — see below |
| 103 | FL 2026 only: how many insert blocks the mixer carries |
| 145 | channel filter-group index (0–5) — *not* the MIDI port |
| 128 | channel color — bytes `R, G, B, 0`; see below |
| 201 | wrapper name (`Fruity Wrapper` / `BRSO Articulate`) |
| 203 | channel display name |
| 212 | plugin header (52 bytes) |
| 213 | **plugin state blob** |
| 231 | channel filter-group names, in index order, **before** the channel blocks |
| 204 | mixer insert name — **precedes** the insert's own event block |
| 236 | starts an insert's parameter block (127 of them; index = ordinal) |
| 235 | insert send routing (byte array, index = destination insert) |
| 147 | insert icon — always present, and the anchor for the color event |
| 149 | insert color (same 4 bytes as event 128) — **only emitted when set**, directly after event 147 |
| 42 | 1 byte: `1` = use the color event, `0` = default gray |

**Insert color needs both 149 and 42.** Writing the color event alone leaves the insert gray
in FL — event 42 is the enable flag and must be set to 1 as well. The two agree exactly across
all 254 inserts in the two reference files: 67 colored, 60 not, zero mismatches.

**Color byte order is `R, G, B, 0` — not a little-endian uint32 of `0xRRGGBB`.**
`struct.pack("<I", 0xRRGGBB)` reverses the bytes and swaps red with blue, and FL renders the
result without complaint, so this is invisible unless the colors are asymmetric enough to
notice. Two independent confirmations: `#c30e0e` written that way came out of FL blue; and the
reference file's own colors, which FL itself wrote, only read as a deliberate palette
red-first — `94 35 9f` purple, `c6 ac 54` gold, `48 51 56` gray, `8d 31 2f` dark red,
`3e 89 bb` blue, `6f 42 25` brown, `35 9f 52` green. Read the other way two of them collapse
into near-identical navy. The trailing byte is `0` on all 109 color events in the reference.
`flp.encode_color` / `decode_color` are the only places that know this.

**Events 147 / 149 / 42 describe the insert whose block comes NEXT**, exactly like the name
event 204 — they sit at the tail of the *previous* insert's block. Event 235 (routing) belongs
to the block it is in, so the boundary falls between them. Confirmed against the reference project:
under this rule every section bus carries its own section's channel color (Brass bus = brass
channel color, Woodwinds = woodwinds, Choir = choir); under the naive reading each bus carries
the *next* section's color. Inside a uniformly colored run the shift is invisible, so this
only shows at block boundaries.

**FL sorts the filter-group names (event 231), and a channel's group index (event 145) refers
to the position in the SORTED list, not the order the names appear in the file.** Writing
`['SINE Strings', 'SINE Brass', 'SINE Percussion']` sorts to
`['SINE Brass', 'SINE Percussion', 'SINE Strings']`, so group 0 shows up labeled `SINE Brass`
and every filter is mislabeled. This is why generated group names are numbered — `01 …`, `02 …`, in port order — which
makes sorted order equal index order. Prefix the names, or assign indices by sorted
rank.

### The registration stamp (event 200)

Sixteen obfuscated characters, UTF-16 with a terminator, 34 bytes, appearing exactly
once at event index 4 and nowhere else in the file. It is **identical across every
project saved by the same FL installation**, so it fingerprints the installation
rather than the project.

What it is not is load-bearing. Tested in FL Studio against a generated template, in
three shapes — the payload emptied, the event deleted outright, and the payload
replaced with sixteen zeros. All three opened, rendered to MP3 and saved without a
warning of any kind.

**FL rewrites the field on every save**, with its own registration, at the same event
index — including in the variant where the event had been deleted, which FL
re-created from scratch. So the stamp records whoever saved a project last, and a
file handed to someone else is re-stamped with theirs the moment they save it.

That is why the tool empties it rather than deleting it: an empty text payload is the
shape FL writes for every other empty text field, and the shape it normalizes a
deleted event back to.

The project author, event 207, behaves differently and is left alone: FL does *not*
rewrite it on save, so it persists across saves until somebody edits it by hand.

### Fruity Wrapper state (event 213 on a SINE channel)

`uint32 kind (=10)` then chunks of `uint32 id, uint64 length, bytes`:

| chunk | contents |
|---|---|
| 1  | 20 bytes; **first int32 = plugin MIDI input port** (0-based, one per instance) |
| 30 | `(0, 16, 0, 0)` — 16 outputs |
| 32 | 192 bytes = 16 × int32 `(mixer insert offset, enabled, reserved)` — see below |
| 51 | VST id `553Y` |
| 52 | `YTSV53s5ine play` |
| 54 | `SINE Player` |
| 55 | `C:\Program Files\VstPlugins\SINE Player.dll` |
| 56 | `Orchestral Tools` |
| 53 | **the SINE state** (see below) |

**Chunk 32 is load-bearing and easy to miss.** Each triple's first int32 is the mixer insert
offset *relative to the channel's own insert*, i.e. output *N* lands on
`channel insert + offset[N]`. A freshly added plugin has every offset at `0`, so all 16 outputs
sum onto the channel's insert and the per-instrument inserts stay silent — the SINE
`outputBusIndex` values look right and nothing works. In the hand-built reference this reads
`(0,1,0), (1,1,0), (2,1,0), …`; in a fresh project it reads `(0,1,0), (0,1,0), (0,1,0), …`.
This is the step that is done by hand in FL's wrapper when outputs are set up.

**The mapping is arbitrary, not necessarily the identity.** Offset *N* → *N* is only what you
want if every MIDI channel has a reserved insert. The generator's compact layout instead maps
the outputs that have instruments behind them to packed slots and leaves the rest at `0`, which
sends them back to the instance's own insert — FL's default for an unrouted output, and inside
the instance's own block either way, so an unused output can never leak into a neighboring
section.

**The table only has 16 entries (outputs 0–15), so SINE MIDI channel 16 has no output behind
it at all** — an instrument there gets an insert nothing feeds, silently. Chunk 30 says the
same independently: `(0, 16, 0, 0)`, sixteen outputs. Output 0 is the main out and the
generator gives it to the instance itself, so **buses 1–15 are what remain: an instance can
carry at most 15 instruments**, not the 16 its MIDI channel count suggests. `validate.py`
reports the dead insert rather than letting it through; see `SPEC.md` open item 3 for what is
still unknown about it.

### SINE Player state (wrapper chunk 53)

**The container depends on which plugin format SINE was loaded as.** Both hold the same JSON.

| | VST2 (`SINE Player.dll`) | VST3 (`SINE Player.vst3`) |
|---|---|---|
| wrapper chunk 51 (VST id) | `553Y` | absent |
| state preamble | 21 bytes | 268 bytes |
| container | raw chunk | FL `VstW` + VST2-bank (`CcnK`/`FBCh`/`Y355`) |
| length fields | one LE uint32 | BE uint32 at 112 and 264, plus the wrapper's |
| trailing data after JSON | none | ~8.4 KB (VST3 controller state) |

**Both are supported.** `sine.parse` dispatches on the preamble and returns a container that
rebuilds itself around a new JSON body, keeping every length field in sync. Anything else
raises `sine.UnsupportedContainer` rather than being mis-parsed.

**VST2 layout:** 21-byte binary header, then **plain UTF-8 pretty-printed JSON**, then a
trailing `\n`. The JSON byte length is a little-endian uint32 at header offset 9.

**VST3 layout** — FL's `VstW` preamble wrapped around a VST2-format bank:

```
byte[..]  FL preamble; little-endian uint32 section length at vstw-8
'VstW'    8 bytes of fields
'CcnK'    big-endian uint32 byteSize at ccnk+4  == bankEnd - (ccnk + 8)
'FBCh'    version, fxID 'Y355', fxVersion, numPrograms, future[128]
          big-endian uint32 chunkSize at ccnk+156
          chunk = JSON + "\n" + a ~60-byte JUCE trailer
byte[..]  FL's own trailing section (per-output data), preserved verbatim
```

Three nested length fields must be updated together on a write: `chunkSize`, the bank
`byteSize`, and the little-endian section length before `VstW`. The trailing FL section and
the JUCE trailer are opaque and are carried across unchanged.

The JSON is written as ASCII, so a decoder's character offset is also a byte offset — that is
what lets the JSON be split from the binary trailer safely.

Header: `f7 ff ff ff  0d fe ff ff  ff  <uint32 jsonLength>  00×8`
— the length is a **little-endian uint32 at header offset 9**, and must be updated on write.
The wrapper chunk's own `uint64 length` must be updated too.

`json.dumps(obj, indent=4) + "\n"` reproduces the original JSON byte-for-byte. Full
read/modify/write fidelity is available.

JSON shape (`samplerVersion` "1.3.0"; every value is a **string**, including numbers/booleans):

```
fileFormatVersion, samplerVersion, instanceName, dynamicXFadeCC ("11"),
velCCXFade ("1"), disableExpressionCC, masterTune, masterTempo, syncToHost,
keyswitchOptions, selectedEntry, renderer{controllerMappingRules…}, preloader,
instruments[]
  instanceId (uuid), id (catalog id), title, midiChannel, omniMode,
  performanceType, keyRangeLow/High, transposition, volume, mute/solo,
  switchMode ("MONO"), switchType ("KEYSWITCH"), variationCC ("3"), color, …
  micPositions[]
    instanceId, id ("spot"/"tree"), title, enabled, volume, balance,
    mute/solo, outputBusIndex, adaptiveLegato{}
  articulations[]
    instanceId, id, title, enabled, volume, keyRangeLow/High,
    definedKeyRange*, transposition, active, variationCCValue,
    mainOptions{ switchValue = KEYSWITCH NOTE NUMBER, purge, releases,
                 xfade, timestretch, … },
    roundRobinOptions{}, dynamicsOptions{dynamicLayers[]},
    envelopeOptions{}, legatoOptions{zoneTypeFeatures[], zones[], …}
```

The three fields a templater actually has to drive are `instrument.midiChannel`,
`micPositions[].outputBusIndex`, and `articulations[].mainOptions.switchValue`.

The embedded SINE JSON runs to a few hundred kilobytes per instance, so a six-section
project carries roughly a megabyte of it — most of the file.

### BRSO Articulate state (event 213 on a BRSO channel)

Native FL plugin, so **not** the Fruity Wrapper chunk format. Fully decoded — the parse below
was validated against all 36 BRSO instances in the reference file:

```
uint32   version    = 10
uint16   cells                    # last occupied grid cell + 1 (1,5,6,9,10,12,13,16 observed)
int32[cells] × 6                  # six parallel per-cell arrays
pascal[cells]                     # articulation names, "" for empty cells
int32[cells]                      # zeros
uint16   bankCount  = 16
pascal[16]                        # bank names, normally all ""
byte[...]                         # settings tail, length varies by version
```

`pascal` = `uint16` length + ASCII bytes.

**The tail length is not fixed — it grows as BRSO adds settings.** The two versions tested
here differ by one setting and a couple of dozen bytes. Find the tail by parsing the head,
never by counting back a fixed length from the end: a fixed-size slice taken off the end of
a newer blob starts inside the data, and every offset read from it lands short.

Per-cell arrays: `0` = **keyswitch note** (`-1` = empty cell), `1` = velocity (`127`),
`2/3/4` = unused rule slots (`-1`), `5` = piano-roll color index (`55` default; 4, 10, 28,
231 also seen). Array 0 was confirmed by diffing two same-size blobs — changing SINE's
keyswitches changes exactly those int32s.

The grid is **4 columns wide** (cells 0–3 = row 0, 4–7 = row 1, …). Placement is free-form;
contiguous placement is confirmed working, on instruments with 5 and with 10 articulations.

The tail ends with a KV block: `uint16 count` then `count` × (`uint16 len`, ASCII
key, `int32` value) — `port`, `channel`, `preserve`, `page`, `paramknob`, `minimised`,
`oldparams`, `optimiseautomation`, `releasecc17`, `changeanyrule`, `singleeditor`,
`overlapping`, `altmiddlec`, `ghost`, `ghosttransport`, `holdkeyswitch`, `cctimerfix`,
`cc64release`, `allchannelomni`, `ccuacc`, `editing`, `articbanks`, `startart`, `midixtend`.

The block runs to the very end of the tail — entries that do not consume it exactly mean the
layout is not the expected one, which is a cheap and reliable structural check.

Across all 36 instances of one version the tail differs in **at most six bytes**: `port`,
`channel`, and the UI-only `preserve`, `page`, `editing`, plus a byte at tail offset 2.
`port` and `channel` are both **0-based**. So a generator copies the tail verbatim from a
donor and patches two int32s — but it must locate them **by key name**, not at fixed offsets,
since a new setting inserted before them would move both.

A BRSO **FL channel** is a fixed 43-event block; only events 64 (index), 203 (name), 213
(state), 128 (color), 132 (two `uint16`s, both = channel index + 1), 145 (filter group) and
212 (window geometry) vary. Event 22 stays 0 — BRSO produces no audio.

### SINE library catalog (the source of truth for instruments)

`%LOCALAPPDATA%\Orchestral Tools\SINE Player\Library.json` (~1.9 MB) is the local catalog:

```
series[] (Metropolis Ark, SINEfactory, Free Series)
  collections[]  id, title            (Ark 1/2/3/4/5/Ø, Layers, …)
    instruments[]  id, title, folder, sortIndex, color
      articulations[]  id, title, legatoActiveOnDefault, autoSustain
      micPositions[]   id, title, installed, installationStatus, …
```

Several hundred instruments. The catalog's instrument `id` matches the `id` in the FLP's
SINE JSON exactly — that is what makes the two joinable, and it was checked on a sample read
from both sides. `micPositions[].installed` explains why a loaded instrument exposes fewer
mic positions than the catalog lists: only the installed ones appear.

Also present: `SINELibrary.db`, `Index.db`, `SINEThumbnails.db` (SQLite, not yet examined).

**Caveat:** `Library.json` lists only `id`/`title` per articulation. The FLP's articulation
records additionally carry measured playback data (dynamic-layer loudness values, legato zone
timings, envelope settings) that the catalog does not have. It is **unknown** whether SINE
accepts a minimally-populated articulation record and fills in the rest on load. Also, the FLP
can contain articulations the catalog does not list — a legato variant carries a suffix on the
base articulation's id and appears only in the project. Until this is settled, the safe
approach is **donor-based**: copy an articulation record out of a project where SINE itself
created it, then patch `switchValue` / routing.

## Status

**Working and released.** The tool builds a complete wired template from a SINE-only
project, and the result is confirmed in FL Studio: instruments reach their own mixer
inserts, BRSO articulations are correct, and the mixer is colored per section. Both VST2
and VST3 SINE containers are supported, and both mixer containers — the fixed 127-block
mixer of FL 21 and earlier, and FL 2026's grow-on-demand one.

Also confirmed in FL:

- **`--compact-mixer`** sizes each block to the instruments it holds instead of reserving
  all 16 slots, which is what lifts the instance count past the reserved layout's ceiling.
- **Color byte order fixed** (see above). Everything goes through `flp.encode_color` /
  `decode_color`.
- **The palette lives in `sine_templater/channel_colors.yaml`**, not in the code, and
  cycles when there are more sections than colors.
- **A window and a frozen Windows build**, both running over the same `core` pipeline.
  See `UI.md`.

See `SPEC.md` for the tool's contract and the remaining open items — the notable ones are
channel *reordering* (generated channels are only ever appended) and re-running on an
already-generated file.

**Lesson worth keeping:** the three bugs that survived to FL were all fields whose value looks
plausible in the data — wrapper chunk 32 (output fan-out) and event 42 (insert color enable),
both set *by hand* in the reference and left at their default in a fresh project, plus the
color byte order, where the bytes were well-formed and simply meant something else. Round-trip
and symmetry checks cannot catch that class of bug: the reference file is already correct, and
a reversed color round-trips perfectly. When something looks right in the data but wrong in
FL, compare a fresh project against the reference rather than re-reading the writer — and when
a field's interpretation is genuinely unconfirmed, pick test values that *fail visibly* if the
guess is wrong. The old placeholder palette was deliberately chosen to look plausible either
way round, which is precisely why the byte order stayed unnoticed for so long.

**The same lesson, about inherited analysis:** this work started from a long transcript of
an earlier AI session that had summarized the same project files. Its conclusions read
confidently and were materially wrong — the number of SINE instances, the number of
instruments, the size of the embedded JSON and the routing rule were all off, and the
mixer and the BRSO channels had never been looked at at all. None of it was checkable
without opening the files, and once they were open it was quicker to start over. Treat
handed-over analysis as a list of things to verify, never as findings.

## Environment

- Windows. The frozen build is Windows-only and so is BRSO Articulate; the package itself
  is pure Python and platform-independent.
- Python 3.10 or newer, standard library only.
- Console output needs `PYTHONIOENCODING=utf-8`: FLP text is UTF-16-LE, and library and
  instrument names contain U+2006.
- SINE's catalog is at `%LOCALAPPDATA%\Orchestral Tools\SINE Player\Library.json`. The
  tool does not read it — everything it needs is already in the project — but it is where
  instrument and articulation ids can be looked up by hand.
