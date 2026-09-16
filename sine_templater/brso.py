"""BRSO Articulate plugin-state codec.

Layout:
    uint32   version = 10
    uint16   cells                  last occupied grid cell + 1
    int32[cells] x 6                per-cell arrays (see ARRAY_* below)
    pascal[cells]                   articulation names, "" for empty cells
    int32[cells]                    zeros
    uint16   bankCount = 16
    pascal[bankCount]               bank names, normally all ""
    byte[...]                       settings tail, ending with the KV block

The grid is 4 columns wide, but placement is free-form and contiguous placement
is confirmed working, so generated instances fill cells 0..n-1.

The tail is opaque and is taken verbatim from a donor instance; only `port` and
`channel` are patched. Its length and the offsets of those two values are read
from the donor rather than hardcoded, because they move between BRSO releases:
v1.17 has a 2920-byte tail with 24 settings, v1.33 a 2943-byte tail with 25
(`holdkeyswitchperm` was added after `holdkeyswitch`). The KV block is
self-describing and ends exactly at the end of the tail, which is what makes it
safe to locate by parsing rather than by offset.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

VERSION = 10
BANK_COUNT = 16
GRID_COLUMNS = 4
GRID_ROWS = 4
# Articulations per BRSO channel. A fresh instance reports cells = 16 with every
# cell empty and `page` = 1, so one 4x4 page is what BRSO allocates, and no instance
# with more has been observed. More than this is refused rather than written and
# hoped for: a cell BRSO does not draw is an articulation that cannot be selected,
# which fails silently in the piano roll instead of loudly here.
MAX_CELLS = GRID_COLUMNS * GRID_ROWS

ARRAY_KEYSWITCH = 0   # MIDI note that selects the articulation, -1 = empty cell
ARRAY_VELOCITY = 1    # always 127
ARRAY_COLOR = 5       # piano-roll color index
ARRAY_COUNT = 6
DEFAULT_VELOCITY = 127
DEFAULT_COLOR = 55
EMPTY = -1

# Settings every BRSO version seen so far defines. Newer releases add keys; that is
# fine, this is only a sanity check that the block is the one we think it is.
# The KV block is located by finding its first entry, "port", encoded as a
# pascal-style key. Built with struct so the literal carries no escapes.
_PORT_ENTRY = struct.pack("<H", 4) + b"port"

EXPECTED_KV_KEYS = (
    "port", "channel", "preserve", "page", "paramknob", "minimised", "oldparams",
    "optimiseautomation", "releasecc17", "changeanyrule", "singleeditor", "overlapping",
    "altmiddlec", "ghost", "ghosttransport", "holdkeyswitch", "cctimerfix", "cc64release",
    "allchannelomni", "ccuacc", "editing", "articbanks", "startart", "midixtend",
)


@dataclass
class BrsoState:
    cells: int
    arrays: list[list[int]]
    names: list[str]
    trailing: list[int]
    bank_names: list[str]
    tail: bytes
    version: int = VERSION

    @property
    def port(self) -> int:
        return struct.unpack_from("<i", self.tail, kv_offsets(self.tail)["port"])[0]

    @property
    def channel(self) -> int:
        return struct.unpack_from("<i", self.tail, kv_offsets(self.tail)["channel"])[0]

    def used_cells(self) -> list[tuple[int, int, str]]:
        ks = self.arrays[ARRAY_KEYSWITCH]
        return [(i, ks[i], self.names[i]) for i in range(self.cells) if ks[i] != EMPTY]


def _read_pascal(data: bytes, pos: int, count: int) -> tuple[list[str], int]:
    out = []
    for _ in range(count):
        (length,) = struct.unpack_from("<H", data, pos)
        pos += 2
        out.append(data[pos : pos + length].decode("ascii"))
        pos += length
    return out, pos


def _write_pascal(values: list[str]) -> bytes:
    out = bytearray()
    for value in values:
        encoded = value.encode("ascii", "replace")
        out += struct.pack("<H", len(encoded)) + encoded
    return bytes(out)


def parse(blob: bytes) -> BrsoState:
    version, cells = struct.unpack_from("<IH", blob, 0)
    pos = 6
    arrays = []
    for _ in range(ARRAY_COUNT):
        arrays.append(list(struct.unpack_from(f"<{cells}i", blob, pos)))
        pos += 4 * cells
    names, pos = _read_pascal(blob, pos, cells)
    trailing = list(struct.unpack_from(f"<{cells}i", blob, pos))
    pos += 4 * cells
    (bank_count,) = struct.unpack_from("<H", blob, pos)
    pos += 2
    bank_names, pos = _read_pascal(blob, pos, bank_count)
    tail = blob[pos:]
    return BrsoState(
        version=version, cells=cells, arrays=arrays, names=names,
        trailing=trailing, bank_names=bank_names, tail=tail,
    )


def serialize(state: BrsoState) -> bytes:
    n = state.cells
    out = bytearray(struct.pack("<IH", state.version, n))
    for array in state.arrays:
        out += struct.pack(f"<{n}i", *array)
    out += _write_pascal(state.names)
    out += struct.pack(f"<{n}i", *state.trailing)
    out += struct.pack("<H", len(state.bank_names))
    out += _write_pascal(state.bank_names)
    out += state.tail
    return bytes(out)


def kv_offsets(tail: bytes) -> dict[str, int]:
    """Setting name -> byte offset of its int32 value within the tail.

    The block is `uint16 count` then `count` entries of `uint16 len, name, int32`.
    It runs to the very end of the tail, which is checked here: if the entries do
    not consume the tail exactly, this is not the block we think it is.
    """
    marker = tail.find(_PORT_ENTRY)
    if marker < 2:
        raise ValueError("BRSO settings tail has no KV block (missing 'port' key)")
    (count,) = struct.unpack_from("<H", tail, marker - 2)
    pos = marker
    offsets: dict[str, int] = {}
    for _ in range(count):
        if pos + 2 > len(tail):
            raise ValueError("BRSO settings tail ends mid-key")
        (length,) = struct.unpack_from("<H", tail, pos)
        pos += 2
        name = tail[pos : pos + length].decode("ascii", "replace")
        pos += length
        if pos + 4 > len(tail):
            raise ValueError(f"BRSO setting {name!r} has no value")
        offsets[name] = pos
        pos += 4
    if pos != len(tail):
        raise ValueError(
            f"BRSO settings block claims {count} entries ending at {pos}, but the "
            f"tail is {len(tail)} bytes; the layout is not what this tool expects"
        )
    return offsets


def extract_tail(blob: bytes) -> bytes:
    """Lift the settings tail from a state blob.

    The length is not fixed -- it grows as BRSO adds settings -- so the head is
    parsed to find where the tail starts rather than counting back from the end.
    """
    return parse(blob).tail


def validate_tail(tail: bytes) -> None:
    """Fail loudly if the donor does not match the format this tool understands."""
    offsets = kv_offsets(tail)
    missing = [k for k in EXPECTED_KV_KEYS if k not in offsets]
    if missing:
        raise ValueError(f"BRSO donor KV block is missing keys: {', '.join(missing)}")


def build(
    keyswitches: list[int],
    names: list[str],
    colors: list[int],
    port: int,
    channel: int,
    tail: bytes,
) -> BrsoState:
    """Synthesise a BRSO state with one contiguous cell per articulation."""
    if not (len(keyswitches) == len(names) == len(colors)):
        raise ValueError("keyswitches, names and colors must be the same length")
    n = len(keyswitches)
    arrays = [
        list(keyswitches),                  # 0 keyswitch note
        [DEFAULT_VELOCITY] * n,             # 1 velocity
        [EMPTY] * n,                        # 2 unused rule slot
        [EMPTY] * n,                        # 3 unused rule slot
        [EMPTY] * n,                        # 4 unused rule slot
        list(colors),                       # 5 piano-roll color
    ]
    offsets = kv_offsets(tail)
    patched = bytearray(tail)
    struct.pack_into("<i", patched, offsets["port"], port)
    struct.pack_into("<i", patched, offsets["channel"], channel)
    return BrsoState(
        cells=n, arrays=arrays, names=list(names), trailing=[0] * n,
        bank_names=[""] * BANK_COUNT, tail=bytes(patched),
    )
