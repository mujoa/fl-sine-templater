"""SINE Player state codec.

Both plugin formats store the same pretty-printed UTF-8 JSON; only the wrapping
differs, so `parse` returns a container that knows how to rebuild itself around
a new JSON body.

VST2 (`SINE Player.dll`)::

    byte[21]  preamble, little-endian uint32 JSON length at offset 9
    JSON

VST3 (`SINE Player.vst3`) -- FL's VstW preamble around a VST2-format bank::

    byte[..]  FL preamble, little-endian uint32 section length at vstw-8
    'VstW'    header (8 bytes of fields)
    'CcnK'    big-endian uint32 byteSize at ccnk+4
    'FBCh'    bank-with-chunk: version, fxID 'Y355', fxVersion, numPrograms,
              future[128], then big-endian uint32 chunkSize at ccnk+156
    JSON + a JUCE trailer ("JUCEPrivateData", "Bypass", ...)
    byte[..]  FL's own trailing section (per-output data)

`json.dumps(obj, indent=4) + "\\n"` reproduces SINE's own formatting byte-for-byte,
so parse/serialize round-trips exactly in both containers.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass

VST2_PREAMBLE = b"\xf7\xff\xff\xff"
VST2_HEADER_LEN = 21
VST2_OFF_JSON_LEN = 9

VST3_MARKER = b"VstW"
FXB_MAGIC = b"CcnK"
# FBCh header: magic, byteSize, fxMagic, version, fxID, fxVersion, numPrograms, future[128]
FXB_HEADER_LEN = 4 + 4 + 4 + 4 + 4 + 4 + 4 + 128
FXB_OFF_BYTE_SIZE = 4
FXB_OFF_CHUNK_SIZE = FXB_HEADER_LEN
VSTW_OFF_SECTION_LEN = -8       # relative to the 'VstW' marker


class UnsupportedContainer(ValueError):
    """The SINE state is not in a container this tool knows how to rewrite."""


@dataclass
class Vst2Container:
    header: bytes
    trailer: bytes

    kind = "VST2"

    def rebuild(self, body: bytes) -> bytes:
        header = bytearray(self.header)
        struct.pack_into("<I", header, VST2_OFF_JSON_LEN, len(body) + len(self.trailer))
        return bytes(header) + body + self.trailer


@dataclass
class Vst3Container:
    prefix: bytes       # everything up to and including chunkSize
    trailer: bytes      # JUCE data inside the chunk, after the JSON
    suffix: bytes       # FL's trailing section, after the bank
    vstw: int           # offset of the 'VstW' marker within prefix
    ccnk: int           # offset of the 'CcnK' marker within prefix

    kind = "VST3"

    def rebuild(self, body: bytes) -> bytes:
        chunk = body + self.trailer
        out = bytearray(self.prefix)
        struct.pack_into(">I", out, self.ccnk + FXB_OFF_CHUNK_SIZE, len(chunk))
        bank_end = len(self.prefix) + len(chunk)
        struct.pack_into(
            ">I", out, self.ccnk + FXB_OFF_BYTE_SIZE, bank_end - (self.ccnk + 8)
        )
        struct.pack_into(
            "<I", out, self.vstw + VSTW_OFF_SECTION_LEN, bank_end - self.vstw
        )
        return bytes(out) + chunk + self.suffix


@dataclass
class SineState:
    container: Vst2Container | Vst3Container
    data: dict

    @property
    def sampler_version(self) -> str:
        return self.data.get("samplerVersion", "?")

    @property
    def instruments(self) -> list[dict]:
        return self.data["instruments"]


def _split_json(body: bytes) -> tuple[dict, bytes]:
    """Decode the JSON document at the start of `body`, returning it and the rest.

    SINE writes the JSON as ASCII, so the decoder's character offset is also a
    byte offset and the binary trailer that may follow is never misread.
    """
    start = body.find(b"{")
    if start < 0:
        raise UnsupportedContainer("no JSON object found in the SINE state")
    text = body[start:].decode("utf-8", errors="replace")
    data, end = json.JSONDecoder().raw_decode(text)
    end += start
    if body[end : end + 1] == b"\n":
        end += 1
    return data, body[end:]


def parse(blob: bytes) -> SineState:
    if blob.startswith(VST2_PREAMBLE):
        header, body = blob[:VST2_HEADER_LEN], blob[VST2_HEADER_LEN:]
        declared = struct.unpack_from("<I", header, VST2_OFF_JSON_LEN)[0]
        if declared != len(body):
            raise ValueError(
                f"SINE JSON length mismatch: header says {declared}, got {len(body)}"
            )
        data, trailer = _split_json(body)
        return SineState(Vst2Container(header=header, trailer=trailer), data)

    vstw = blob.find(VST3_MARKER)
    ccnk = blob.find(FXB_MAGIC, vstw) if vstw >= 0 else -1
    if vstw < 0 or ccnk < 0:
        raise UnsupportedContainer(
            f"unrecognized SINE state container (starts with {blob[:4].hex(' ')})"
        )
    if vstw + VSTW_OFF_SECTION_LEN < 0:
        raise UnsupportedContainer("VST3 state is truncated before the VstW header")

    chunk_start = ccnk + FXB_HEADER_LEN + 4
    chunk_size = struct.unpack_from(">I", blob, ccnk + FXB_OFF_CHUNK_SIZE)[0]
    bank_end = chunk_start + chunk_size
    if bank_end > len(blob):
        raise UnsupportedContainer(
            f"VST3 chunk claims {chunk_size} bytes but only {len(blob) - chunk_start} remain"
        )
    data, trailer = _split_json(blob[chunk_start:bank_end])
    return SineState(
        Vst3Container(
            prefix=blob[:chunk_start],
            trailer=trailer,
            suffix=blob[bank_end:],
            vstw=vstw,
            ccnk=ccnk,
        ),
        data,
    )


def serialize(state: SineState) -> bytes:
    body = (json.dumps(state.data, indent=4) + "\n").encode("utf-8")
    return state.container.rebuild(body)


def articulations(instrument: dict) -> list[tuple[str, int]]:
    """(verbatim title, keyswitch note) for each articulation, in SINE's order."""
    return [
        (art["title"], int(art["mainOptions"]["switchValue"]))
        for art in instrument["articulations"]
    ]


def set_output_bus(instrument: dict, bus: int) -> None:
    """Route every mic position of an instrument to one SINE output bus."""
    for mic in instrument["micPositions"]:
        mic["outputBusIndex"] = str(bus)
