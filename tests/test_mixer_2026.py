"""The FL 2026 container: a wider insert field and a mixer that has to be grown.

Synthetic events throughout -- these are about the shape of the file, not about
any particular project, so they need no .flp and never skip.
"""
from __future__ import annotations

import pytest

from sine_templater import apply as apply_mod, flp, model, plan as plan_mod

Events = list[tuple[int, bytes]]


def _mixer(inserts: int) -> Events:
    """A mixer shaped the way FL 2026 writes one.

    A count, then Master, `inserts` ordinary inserts and the trailing "current"
    block. Every block is preceded by its own color flag and ends on its icon,
    which is the attribution the code under test relies on. Ordinary inserts feed
    Master; Master and "current" feed nothing, so their arrays hold only a zero.
    """
    blocks = inserts + 2
    events: Events = [(flp.EV_INSERT_COUNT, blocks.to_bytes(2, "little"))]
    for index in range(blocks):
        feeds_master = 0 < index < blocks - 1
        events += [
            (flp.EV_INSERT_HAS_COLOR, b"\x00"),
            (flp.EV_INSERT_PARAMS, bytes(12)),
            (flp.EV_INSERT_ROUTING, b"\x01" if feeds_master else b"\x00"),
            (flp.EV_INSERT_ICON, bytes(4)),
        ]
    return events


def _fixed_mixer(blocks: int = 127) -> Events:
    """FL 21's mixer: no count event, and a full-width routing array per insert."""
    events: Events = []
    for index in range(blocks):
        events += [
            (flp.EV_INSERT_HAS_COLOR, b"\x00"),
            (flp.EV_INSERT_PARAMS, bytes(12)),
            (flp.EV_INSERT_ROUTING, bytes(blocks)),
            (flp.EV_INSERT_ICON, bytes(4)),
        ]
    return events


def _plan(channels: list[list[int]], *, reserve: bool = True) -> plan_mod.Plan:
    instances = []
    for port, midi_channels in enumerate(channels):
        instance = plan_mod.InstancePlan(
            channel_index=port,
            name=f"SINE {port}",
            port=port,
            bus_insert=port + 1,
            bus_name=f"SINE {port} (main)",
            group_name=f"{port + 1:02d} SINE {port}",
            base_insert=0,
            color=0xC30E0E,
            reserve_slots=reserve,
        )
        instance.instruments = [
            plan_mod.InstrumentPlan(title=f"i{c}", midi_channel=c, insert=0, articulations=[])
            for c in midi_channels
        ]
        instances.append(instance)
    plan_mod._assign_inserts(instances, plan_mod.MAX_INSERT_DYNAMIC)
    return plan_mod.Plan(
        instances=instances,
        donor_channel=None,
        donor_tail=b"",
        first_new_channel=0,
        max_insert=plan_mod.MAX_INSERT_DYNAMIC,
    )


# --- the codec -------------------------------------------------------------

def test_three_byte_event_survives_a_roundtrip():
    """FL 2026's event 172 fits no size class, so the parser carries its width."""
    project = flp.Project(fmt=0, nch=0, ppq=96, events=[(172, b"\x01\x01\x00")])
    data = flp.serialize(project)
    assert flp.parse(data).events == project.events
    assert flp.serialize(flp.parse(data)) == data


def test_an_event_of_the_wrong_width_is_reported_not_guessed_at():
    """Reading one width wrong desynchronizes everything after it.

    That used to go unnoticed and come out as a nonsense channel count much later,
    so the parser checks that the events land exactly on the end of the chunk.
    """
    body = bytes([flp.EV_CHAN_NEW, 0])      # a two-byte payload with one byte left
    data = (
        flp.MAGIC_HEADER
        + (6).to_bytes(4, "little")
        + bytes(6)
        + flp.MAGIC_DATA
        + len(body).to_bytes(4, "little")
        + body
    )
    with pytest.raises(ValueError, match="newer FL Studio"):
        flp.parse(data)


def test_payload_width_covers_every_class():
    assert flp.payload_width(flp.EV_CHAN_END) == 1
    assert flp.payload_width(flp.EV_CHAN_INSERT_WIDE) == 2
    assert flp.payload_width(flp.EV_CHAN_COLOR) == 4
    assert flp.payload_width(172) == 3
    assert flp.payload_width(flp.EV_PLUGIN_STATE) is None


# --- the channel's insert field -------------------------------------------

def test_insert_field_keeps_the_width_of_the_id_it_was_found_under():
    assert apply_mod._insert_payload(flp.EV_CHAN_INSERT, 11) == b"\x0b"
    assert apply_mod._insert_payload(flp.EV_CHAN_INSERT_WIDE, 300) == b"\x2c\x01"


def test_the_old_one_byte_field_cannot_hold_a_2026_insert():
    """Which is why FL widened it; better an error here than a truncated route."""
    with pytest.raises(OverflowError):
        apply_mod._insert_payload(flp.EV_CHAN_INSERT, 300)


# --- the ceiling -----------------------------------------------------------

def test_ceiling_follows_the_projects_fl_version():
    dynamic = flp.Project(fmt=0, nch=0, ppq=96, events=_mixer(16))
    fixed = flp.Project(fmt=0, nch=0, ppq=96, events=_fixed_mixer())
    assert model.dynamic_mixer(dynamic) is True
    assert model.dynamic_mixer(fixed) is False
    assert plan_mod.max_insert(dynamic) == plan_mod.MAX_INSERT_DYNAMIC
    assert plan_mod.max_insert(fixed) == plan_mod.MAX_INSERT_FIXED
    assert plan_mod.max_instances(plan_mod.MAX_INSERT_FIXED) == 7
    assert plan_mod.max_instances(plan_mod.MAX_INSERT_DYNAMIC) == 29


# --- growing the mixer -----------------------------------------------------

def test_mixer_grows_to_exactly_what_the_layout_needs():
    plan = _plan([[1, 2], [1]])                       # buses 1-2, blocks 3-18 and 19-34
    grown = apply_mod._grow_mixer(_mixer(16), plan)
    project = flp.Project(fmt=0, nch=0, ppq=96, events=grown)
    assert plan.highest_insert == 34
    assert model.insert_count(project) == 36          # Master, 1..34, "current"
    declared = [p for eid, p in grown if eid == flp.EV_INSERT_COUNT]
    assert declared == [(36).to_bytes(2, "little")]


def test_growth_leaves_the_current_insert_last():
    """New blocks go before it, so no existing insert changes index."""
    original = _mixer(16)
    marker = b"\xaa\xbb\xcc\xdd"
    last = max(i for i, (eid, _) in enumerate(original) if eid == flp.EV_INSERT_ICON)
    original[last] = (flp.EV_INSERT_ICON, marker)

    grown = apply_mod._grow_mixer(original, _plan([[1, 2], [1]]))
    icons = [payload for eid, payload in grown if eid == flp.EV_INSERT_ICON]
    assert icons[-1] == marker
    assert marker not in icons[:-1]


def test_every_new_block_gets_its_own_color_flag():
    """FL keeps the flag in front of the block, not inside it."""
    grown = apply_mod._grow_mixer(_mixer(16), _plan([[1, 2], [1]]))
    blocks = apply_mod._insert_bodies(grown)
    for first, _ in blocks:
        assert grown[first - 1][0] == flp.EV_INSERT_HAS_COLOR


def test_a_mixer_that_is_already_big_enough_is_left_alone():
    events = _mixer(64)
    assert apply_mod._grow_mixer(events, _plan([[1]])) == events


def test_a_fixed_mixer_is_never_grown():
    """FL 21 cannot add inserts, so needing one past 125 is a refusal, not a rewrite."""
    plan = _plan([[1]] * 8)                            # up to insert 136
    with pytest.raises(ValueError, match="cannot be grown"):
        apply_mod._grow_mixer(_fixed_mixer(), plan)


def test_insert_bodies_end_on_the_icon():
    events = _mixer(2)
    bodies = apply_mod._insert_bodies(events)
    assert len(bodies) == 4
    for first, last in bodies:
        assert events[first][0] == flp.EV_INSERT_PARAMS
        assert events[last][0] == flp.EV_INSERT_ICON


def test_a_block_with_no_icon_is_reported():
    events = [(eid, p) for eid, p in _mixer(2) if eid != flp.EV_INSERT_ICON]
    with pytest.raises(ValueError, match="do not pair up"):
        apply_mod._insert_bodies(events)


# --- routing ---------------------------------------------------------------

def test_routing_array_widens_to_reach_its_destination():
    """FL 2026 trims the array; a route to insert 3 needs four bytes to say so."""
    plan = _plan([[1, 2], [1]])
    rewritten = apply_mod._rewrite_mixer(_mixer(16), plan)

    index = -1
    routes: dict[int, list[int]] = {}
    for eid, payload in rewritten:
        if eid == flp.EV_INSERT_PARAMS:
            index += 1
        elif eid == flp.EV_INSERT_ROUTING and index >= 0:
            routes[index] = [i for i, byte in enumerate(payload) if byte]

    assert routes[1] == [0] and routes[2] == [0]       # section buses -> Master
    assert routes[3] == [1] and routes[18] == [1]      # first block -> bus 1
    assert routes[19] == [2] and routes[34] == [2]     # second block -> bus 2
    assert routes[35] == []                            # the "current" insert


def test_a_full_width_routing_array_is_never_narrowed():
    """An FL 21 project keeps its 127-byte arrays, byte for byte."""
    plan = _plan([[1]])
    rewritten = apply_mod._rewrite_mixer(_fixed_mixer(), plan)
    widths = {len(p) for eid, p in rewritten if eid == flp.EV_INSERT_ROUTING}
    assert widths == {127}
