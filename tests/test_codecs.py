"""Codec symmetry: build(parse(x)) == x for every real blob in the reference files.

This is the guard against having misread a format. If a field was misunderstood,
symmetry breaks here on real data instead of silently corrupting a template.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import paths
from sine_templater import brso, flp, model, sine, wrapper

ROOT = paths.ROOT
REFERENCES = paths.references()

pytestmark = pytest.mark.skipif(not REFERENCES, reason=paths.MISSING)


@pytest.fixture(params=REFERENCES or [None], ids=lambda p: p.name if p else "none")
def reference(request) -> Path:
    return request.param


def test_flp_roundtrip(reference: Path):
    data = reference.read_bytes()
    assert flp.serialize(flp.parse(data)) == data


def test_color_roundtrip(reference: Path):
    """Every color FL wrote survives decode/encode, trailing byte included."""
    project = flp.parse(reference.read_bytes())
    seen = 0
    for eid, payload in project.events:
        if eid not in (flp.EV_CHAN_COLOR, flp.EV_INSERT_COLOR):
            continue
        assert flp.encode_color(flp.decode_color(payload)) == payload
        seen += 1
    assert seen > 0


def test_color_byte_order_is_red_first():
    """FL stores R, G, B, 0 -- not a little-endian uint32 of 0xRRGGBB.

    The reference proves it: read red-first its channel colors are a plain section
    palette, read the other way round two of them collapse into near-identical navy.
    """
    assert flp.encode_color(0xC30E0E) == bytes([0xC3, 0x0E, 0x0E, 0x00])
    assert flp.decode_color(bytes([0xC3, 0x0E, 0x0E, 0x00])) == 0xC30E0E
    with pytest.raises(ValueError, match="24-bit RGB"):
        flp.encode_color(0x1000000)


def test_channel_blocks_match_header(reference: Path):
    project = flp.parse(reference.read_bytes())
    blocks = model.channel_blocks(project)
    assert len(blocks) == project.nch
    assert all(b.end > b.start for b in blocks)


def test_wrapper_roundtrip(reference: Path):
    project = flp.parse(reference.read_bytes())
    seen = 0
    for block in model.channel_blocks(project):
        if not model.is_sine(block):
            continue
        state = model.plugin_state(project, block)
        kind, chunks = wrapper.parse(state)
        assert wrapper.serialize(kind, chunks) == state
        seen += 1
    assert seen == 6


def test_sine_roundtrip(reference: Path):
    project = flp.parse(reference.read_bytes())
    for block in model.channel_blocks(project):
        if not model.is_sine(block):
            continue
        _, chunks = wrapper.parse(model.plugin_state(project, block))
        blob = wrapper.get_chunk(chunks, wrapper.CHUNK_VST_STATE)
        state = sine.parse(blob)
        assert state.sampler_version == "1.3.0"
        assert sine.serialize(state) == blob


def test_brso_roundtrip(reference: Path):
    project = flp.parse(reference.read_bytes())
    seen = 0
    for block in model.channel_blocks(project):
        if not model.is_brso(block):
            continue
        blob = model.plugin_state(project, block)
        state = brso.parse(blob)
        assert brso.serialize(state) == blob
        assert brso.extract_tail(blob) == state.tail
        brso.validate_tail(state.tail)
        seen += 1
    assert seen == 36


def _sine_states(path: Path):
    project = flp.parse(path.read_bytes())
    for block in model.channel_blocks(project):
        if not model.is_sine(block):
            continue
        _, chunks = wrapper.parse(model.plugin_state(project, block))
        yield block.name, wrapper.get_chunk(chunks, wrapper.CHUNK_VST_STATE)


def _find_vst3_states() -> list[tuple[str, bytes]]:
    found = []
    for path in paths.projects():
        try:
            for name, blob in _sine_states(path):
                if sine.VST3_MARKER in blob[:512]:
                    found.append((f"{path.name}:{name}", blob))
        except (ValueError, KeyError):
            continue
    return found


VST3_STATES = _find_vst3_states()


@pytest.mark.skipif(not VST3_STATES, reason="no VST3-hosted SINE instance available")
@pytest.mark.parametrize("name,blob", VST3_STATES, ids=[n for n, _ in VST3_STATES])
def test_vst3_sine_roundtrip(name: str, blob: bytes):
    state = sine.parse(blob)
    assert state.container.kind == "VST3"
    assert state.instruments
    assert sine.serialize(state) == blob


@pytest.mark.skipif(not VST3_STATES, reason="no VST3-hosted SINE instance available")
def test_vst3_length_fields_track_a_changed_body():
    import struct

    _, blob = VST3_STATES[0]
    state = sine.parse(blob)
    for instrument in state.instruments:
        sine.set_output_bus(instrument, 9)
    rebuilt = sine.serialize(state)
    container = state.container

    chunk_size = struct.unpack_from(">I", rebuilt, container.ccnk + sine.FXB_OFF_CHUNK_SIZE)[0]
    bank_end = len(container.prefix) + chunk_size
    assert bank_end + len(container.suffix) == len(rebuilt)
    assert struct.unpack_from(">I", rebuilt, container.ccnk + sine.FXB_OFF_BYTE_SIZE)[0] == (
        bank_end - (container.ccnk + 8)
    )
    assert struct.unpack_from(
        "<I", rebuilt, container.vstw + sine.VSTW_OFF_SECTION_LEN
    )[0] == (bank_end - container.vstw)
    assert sine.parse(rebuilt).instruments[0]["micPositions"][0]["outputBusIndex"] == "9"


def test_brso_build_matches_handbuilt_wiring():
    """A generated grid should reproduce the hand-built one bar names and colors."""
    project = flp.parse(paths.reference().read_bytes())
    blocks = {b.name: b for b in model.channel_blocks(project)}
    reference_state = brso.parse(model.plugin_state(project, blocks["Tutti Orchestra ARK 0"]))

    sine_block = blocks["SINE Strings"]
    _, chunks = wrapper.parse(model.plugin_state(project, sine_block))
    state = sine.parse(wrapper.get_chunk(chunks, wrapper.CHUNK_VST_STATE))
    instrument = next(i for i in state.instruments if i["title"] == "Tutti Orchestra")
    arts = sine.articulations(instrument)

    generated = brso.build(
        keyswitches=[ks for _, ks in arts],
        names=[title for title, _ in arts],
        colors=list(range(len(arts))),
        port=0,
        channel=int(instrument["midiChannel"]) - 1,
        tail=reference_state.tail,
    )

    assert generated.cells == reference_state.cells
    assert generated.port == reference_state.port
    assert generated.channel == reference_state.channel
    assert (generated.arrays[brso.ARRAY_KEYSWITCH]
            == reference_state.arrays[brso.ARRAY_KEYSWITCH])
    assert brso.parse(brso.serialize(generated)).names == [t for t, _ in arts]
