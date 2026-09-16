"""Fruity Wrapper plugin-state codec.

Layout: uint32 kind, then chunks of (uint32 id, uint64 length, bytes).
Chunk 1 holds the plugin's MIDI input port; chunk 53 holds the hosted VST's
own state (for SINE Player, that is the JSON handled in sine.py).
"""
from __future__ import annotations

import struct

CHUNK_PORT = 1
CHUNK_OUTPUT_ROUTING = 32
CHUNK_VST_STATE = 53

# Chunk 32 is a table of (mixer insert offset, enabled, reserved) int32 triples,
# one per plugin output. The offset is relative to the channel's own insert, so
# output N lands on `channel insert + offset[N]`. A freshly added plugin has every
# offset at 0, which sums all outputs onto the channel's own insert; an output has
# to be given a non-zero offset for a separate insert to receive anything.
OUTPUT_ENTRY_LEN = 12


def parse(data: bytes) -> tuple[int, list[tuple[int, bytes]]]:
    kind = struct.unpack("<I", data[:4])[0]
    pos = 4
    chunks: list[tuple[int, bytes]] = []
    while pos + 12 <= len(data):
        cid, length = struct.unpack("<IQ", data[pos : pos + 12])
        pos += 12
        chunks.append((cid, data[pos : pos + length]))
        pos += length
    if pos != len(data):
        raise ValueError(f"trailing bytes in wrapper state at {pos}/{len(data)}")
    return kind, chunks


def serialize(kind: int, chunks: list[tuple[int, bytes]]) -> bytes:
    out = bytearray(struct.pack("<I", kind))
    for cid, payload in chunks:
        out += struct.pack("<IQ", cid, len(payload)) + payload
    return bytes(out)


def get_chunk(chunks: list[tuple[int, bytes]], cid: int) -> bytes:
    for chunk_id, payload in chunks:
        if chunk_id == cid:
            return payload
    raise KeyError(f"wrapper chunk {cid} not present")


def set_chunk(chunks: list[tuple[int, bytes]], cid: int, payload: bytes) -> list[tuple[int, bytes]]:
    return [(c, payload if c == cid else p) for c, p in chunks]


def get_midi_port(chunks: list[tuple[int, bytes]]) -> int:
    return struct.unpack("<i", get_chunk(chunks, CHUNK_PORT)[:4])[0]


def set_midi_port(chunks: list[tuple[int, bytes]], port: int) -> list[tuple[int, bytes]]:
    payload = bytearray(get_chunk(chunks, CHUNK_PORT))
    struct.pack_into("<i", payload, 0, port)
    return set_chunk(chunks, CHUNK_PORT, bytes(payload))


def output_offsets(chunks: list[tuple[int, bytes]]) -> list[int]:
    payload = get_chunk(chunks, CHUNK_OUTPUT_ROUTING)
    return [
        struct.unpack_from("<i", payload, offset)[0]
        for offset in range(0, len(payload), OUTPUT_ENTRY_LEN)
    ]


def set_output_offsets(
    chunks: list[tuple[int, bytes]], offsets: list[int]
) -> list[tuple[int, bytes]]:
    """Send plugin output N to `channel insert + offsets[N]`.

    One entry per plugin output, so the caller has to size the list to the table it
    got from `output_offsets`; a short or long list would silently shift outputs onto
    the wrong inserts, which is exactly what this refuses to do.
    """
    payload = bytearray(get_chunk(chunks, CHUNK_OUTPUT_ROUTING))
    if len(payload) % OUTPUT_ENTRY_LEN:
        raise ValueError(
            f"plugin output table is {len(payload)} bytes, not a multiple of {OUTPUT_ENTRY_LEN}"
        )
    entries = len(payload) // OUTPUT_ENTRY_LEN
    if len(offsets) != entries:
        raise ValueError(
            f"got {len(offsets)} output offsets for a plugin with {entries} outputs"
        )
    for index, offset in enumerate(offsets):
        struct.pack_into("<i", payload, index * OUTPUT_ENTRY_LEN, offset)
    return set_chunk(chunks, CHUNK_OUTPUT_ROUTING, bytes(payload))
