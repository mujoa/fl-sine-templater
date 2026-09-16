"""FL Studio project file codec.

Parses an .flp to a flat list of (event_id, payload) and serializes it back.
`serialize(parse(data)) == data` byte-for-byte; the tool relies on that so any
difference between input and output is a change we made deliberately.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC_HEADER = b"FLhd"
MAGIC_DATA = b"FLdt"

# Event ids used by this tool. Payload width is implied by the id:
# <64 -> 1 byte, <128 -> 2, <192 -> 4, >=192 -> varint length prefix, except for
# the handful of ids in EXPLICIT_WIDTHS below.
EV_CHAN_NEW = 64          # channel index (starts a channel block)
EV_CHAN_END = 20          # last event of a channel block
EV_CHAN_INSERT = 22       # channel -> mixer insert, one byte (FL 21 and earlier)
EV_CHAN_INSERT_WIDE = 104 # the same field from FL 2026 on: two bytes, for 500 inserts
EV_CHAN_COLOR = 128
EV_CHAN_ORDINAL = 132     # two uint16, both = channel index + 1
EV_CHAN_GROUP = 145       # channel rack filter group (index into the group name list)
EV_CHAN_GROUP_NAME = 231  # one per filter group, in index order, before the channels
EV_WRAPPER_NAME = 201     # "Fruity Wrapper" / "BRSO Articulate"
EV_PLUGIN_NAME = 203      # channel display name
EV_PLUGIN_STATE = 213
EV_INSERT_NAME = 204      # precedes that insert's own parameter block
EV_INSERT_PARAMS = 236    # starts an insert block; index = ordinal
EV_INSERT_ROUTING = 235   # byte array indexed by destination insert
EV_INSERT_ICON = 147      # last event of an insert's own block
EV_INSERT_COLOR = 149     # emitted only for inserts that have a color
EV_INSERT_HAS_COLOR = 42  # 1 = use the color event, 0 = default gray
EV_INSERT_COUNT = 103     # FL 2026: how many insert blocks the mixer carries

# A channel's mixer insert, under both ids it has had. Only one of them appears in
# any given project, so code that reads or rewrites the field matches on the pair
# and takes the width from whichever id it found.
CHAN_INSERT_EVENTS = (EV_CHAN_INSERT, EV_CHAN_INSERT_WIDE)

# FL 2026 writes event 172 with a three-byte payload, which none of the size
# classes can express. What it holds is unknown and nothing here needs it; the
# parser only has to step over it without losing alignment, which is what this
# table is for. Getting a width wrong desynchronizes the whole event stream, so
# `parse` insists on landing exactly on the end of the chunk.
EXPLICIT_WIDTHS = {172: 3}


def payload_width(eid: int) -> int | None:
    """Fixed payload width for an event id, or None when the payload is length-prefixed."""
    if eid in EXPLICIT_WIDTHS:
        return EXPLICIT_WIDTHS[eid]
    if eid < 64:
        return 1
    if eid < 128:
        return 2
    if eid < 192:
        return 4
    return None


def encode_color(rgb: int) -> bytes:
    """Pack 0xRRGGBB into a color payload.

    FL stores a color as the bytes R, G, B, 0 -- so a little-endian uint32 of
    0xRRGGBB is byte-reversed and comes out with red and blue swapped. Confirmed
    against the hand-built reference, whose colors only read as a coherent palette
    this way round, and in FL itself.
    """
    if not 0 <= rgb <= 0xFFFFFF:
        raise ValueError(f"color {rgb:#x} is not a 24-bit RGB value")
    return rgb.to_bytes(3, "big") + b"\x00"


def decode_color(payload: bytes) -> int:
    """The 0xRRGGBB value of a color payload; the trailing byte is always 0."""
    return int.from_bytes(payload[:3], "big")


@dataclass
class Project:
    fmt: int
    nch: int
    ppq: int
    events: list[tuple[int, bytes]] = field(default_factory=list)


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def parse(data: bytes) -> Project:
    if data[:4] != MAGIC_HEADER:
        raise ValueError(f"not an FLP file (magic {data[:4]!r})")
    header_len = struct.unpack("<I", data[4:8])[0]
    fmt, nch, ppq = struct.unpack("<hHH", data[8 : 8 + header_len])

    pos = 8 + header_len
    if data[pos : pos + 4] != MAGIC_DATA:
        raise ValueError(f"missing {MAGIC_DATA!r} chunk")
    end = pos + 8 + struct.unpack("<I", data[pos + 4 : pos + 8])[0]
    pos += 8

    events: list[tuple[int, bytes]] = []
    while pos < end:
        eid = data[pos]
        pos += 1
        width = payload_width(eid)
        if width is None:
            width, pos = _read_varint(data, pos)
        events.append((eid, data[pos : pos + width]))
        pos += width
    if pos != end:
        raise ValueError(
            f"the event stream overruns the {MAGIC_DATA!r} chunk by {pos - end} byte(s): "
            f"this project uses an event whose payload width the parser gets wrong. "
            f"It was most likely saved by a newer FL Studio than this tool knows about."
        )
    return Project(fmt=fmt, nch=nch, ppq=ppq, events=events)


def serialize(project: Project) -> bytes:
    body = bytearray()
    for eid, payload in project.events:
        body.append(eid)
        if eid >= 192:
            body += _write_varint(len(payload))
        body += payload
    header = struct.pack("<hHH", project.fmt, project.nch, project.ppq)
    return (
        MAGIC_HEADER
        + struct.pack("<I", len(header))
        + header
        + MAGIC_DATA
        + struct.pack("<I", len(body))
        + bytes(body)
    )


def decode_text(payload: bytes) -> str:
    return payload.decode("utf-16-le").rstrip("\x00")


def encode_text(value: str) -> bytes:
    return (value + "\x00").encode("utf-16-le")
