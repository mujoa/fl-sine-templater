"""Build a contract-shaped input .flp from the reference project, for testing.

Keeps the 6 SINE instances, drops the 36 hand-built BRSO channels, and leaves a
single BRSO channel last as the donor. This renumbers channels, which the tool
itself deliberately never does -- it exists only to produce a fixture without
needing FL Studio. A genuine end-to-end check still needs an input saved from FL.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sine_templater import brso, flp, model  # noqa: E402

import paths  # noqa: E402

ROOT = paths.ROOT
SOURCE = paths.reference()
FIXTURE = ROOT / "tests" / "fixture_sine_only.flp"


def _renumber(events: list[tuple[int, bytes]], index: int) -> list[tuple[int, bytes]]:
    out = []
    for eid, payload in events:
        if eid == flp.EV_CHAN_NEW:
            payload = struct.pack("<H", index)
        elif eid == flp.EV_CHAN_ORDINAL:
            payload = struct.pack("<HH", index + 1, index + 1)
        out.append((eid, payload))
    return out


def build() -> bytes:
    project = flp.parse(SOURCE.read_bytes())
    blocks = model.channel_blocks(project)

    sine_blocks = [b for b in blocks if model.is_sine(b)]
    donor = next(b for b in blocks if model.is_brso(b))

    # Blank the donor's articulation grid, as an untouched BRSO instance would be.
    donor_state = brso.parse(model.plugin_state(project, donor))
    empty = brso.build([], [], [], port=0, channel=0, tail=donor_state.tail)
    donor_events = [
        (eid, brso.serialize(empty) if eid == flp.EV_PLUGIN_STATE else payload)
        for eid, payload in project.events[donor.span]
    ]

    channels: list[tuple[int, bytes]] = []
    for index, block in enumerate(sine_blocks):
        channels += _renumber(project.events[block.span], index)
    channels += _renumber(donor_events, len(sine_blocks))

    region_start = blocks[0].start
    region_end = blocks[-1].end + 1
    events = project.events[:region_start] + channels + project.events[region_end:]

    return flp.serialize(
        flp.Project(fmt=project.fmt, nch=len(sine_blocks) + 1, ppq=project.ppq, events=events)
    )


if __name__ == "__main__":
    FIXTURE.write_bytes(build())
    print(f"wrote {FIXTURE} ({FIXTURE.stat().st_size} bytes)")
