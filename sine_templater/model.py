"""Read-only view of an FLP: channel blocks and mixer inserts."""
from __future__ import annotations

from dataclasses import dataclass

from . import flp

WRAPPER_FRUITY = "Fruity Wrapper"
WRAPPER_BRSO = "BRSO Articulate"
SINE_PLUGIN_NAMES = ("SINE Player",)


@dataclass
class ChannelBlock:
    index: int
    start: int          # event index of EV_CHAN_NEW
    end: int            # event index of EV_CHAN_END, inclusive
    wrapper: str
    name: str

    @property
    def span(self) -> slice:
        return slice(self.start, self.end + 1)


def channel_blocks(project: flp.Project) -> list[ChannelBlock]:
    starts = [i for i, (eid, _) in enumerate(project.events) if eid == flp.EV_CHAN_NEW]
    ends = [i for i, (eid, _) in enumerate(project.events) if eid == flp.EV_CHAN_END]
    if not (len(starts) == len(ends) == project.nch):
        raise ValueError(
            f"channel count mismatch: header says {project.nch}, found "
            f"{len(starts)} starts and {len(ends)} ends"
        )

    blocks = []
    for position, (start, end) in enumerate(zip(starts, ends)):
        if end <= start:
            raise ValueError(f"malformed channel block at event {start}")
        wrapper = name = ""
        for eid, payload in project.events[start : end + 1]:
            if eid == flp.EV_WRAPPER_NAME:
                wrapper = flp.decode_text(payload)
            elif eid == flp.EV_PLUGIN_NAME:
                name = flp.decode_text(payload)
        blocks.append(
            ChannelBlock(index=position, start=start, end=end, wrapper=wrapper, name=name)
        )
    return blocks


def is_sine(block: ChannelBlock) -> bool:
    return block.wrapper == WRAPPER_FRUITY


def is_brso(block: ChannelBlock) -> bool:
    return block.wrapper == WRAPPER_BRSO


def plugin_state(project: flp.Project, block: ChannelBlock) -> bytes:
    for eid, payload in project.events[block.span]:
        if eid == flp.EV_PLUGIN_STATE:
            return payload
    raise ValueError(f"channel {block.index} ({block.name!r}) has no plugin state")


def insert_name_events(project: flp.Project) -> dict[int, int]:
    """insert index -> event index of its name event.

    An insert's name event immediately precedes that insert's own parameter
    block, so a pending name is claimed by the next block encountered. Inserts
    with no name simply have no entry.
    """
    mapping: dict[int, int] = {}
    seen_blocks = -1
    pending: int | None = None
    for position, (eid, _) in enumerate(project.events):
        if eid == flp.EV_INSERT_NAME:
            pending = position
        elif eid == flp.EV_INSERT_PARAMS:
            seen_blocks += 1
            if pending is not None:
                mapping[seen_blocks] = pending
                pending = None
    return mapping


def insert_count(project: flp.Project) -> int:
    return sum(1 for eid, _ in project.events if eid == flp.EV_INSERT_PARAMS)


def dynamic_mixer(project: flp.Project) -> bool:
    """True when the mixer grows on demand, as it does from FL 2026 on.

    Such a project stores only the inserts it actually has -- 16 in a new one, up
    to 500 -- and records how many in EV_INSERT_COUNT. Before that the mixer was a
    fixed 127 blocks that every project carried in full, so there was no count to
    keep and nothing to grow.
    """
    return any(eid == flp.EV_INSERT_COUNT for eid, _ in project.events)
