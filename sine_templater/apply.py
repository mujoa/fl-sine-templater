"""Turn a Plan into a mutated project. Only touches what the plan describes.

Five passes, ordered so that no pass invalidates positions the next one needs:

1. donor -> BRSO channels -- a slice replacement at the end of the channel region,
   which is where the donor is required to be, so the SINE blocks ahead of it keep
   the event positions the plan was derived from
2. SINE channel edits     -- a slice replacement per block, last block first, since
   a block that gains a color or group event moves everything after it
3. mixer rewrite          -- creates the inserts the plan needs and the project
   does not have, then a stateful walk, so it needs no precomputed positions
   (it may insert name and color events for inserts that have none)
4. filter groups          -- also a walk; it changes offsets ahead of the channels,
   so nothing position-based may follow it
5. registration stamp     -- a flat map over the events, so position-independent,
   which is why it is safe to run after the pass that moves everything
"""
from __future__ import annotations

import struct

from . import brso, flp, model, plan as plan_mod, sine, wrapper

Events = list[tuple[int, bytes]]


def apply(project: flp.Project, plan: plan_mod.Plan) -> flp.Project:
    blocks = {b.index: b for b in model.channel_blocks(project)}
    donor = plan.donor_channel

    events: Events = list(project.events)
    donor_events = events[donor.span]

    generated = _build_brso_channels(donor_events, plan)
    events = events[: donor.start] + generated + events[donor.end + 1 :]

    _rewrite_sine_channels(events, blocks, plan)

    events = _rewrite_mixer(events, plan)
    events = _rewrite_filter_groups(events, plan)
    events = _blank_registration(events)

    return flp.Project(
        fmt=project.fmt,
        nch=project.nch - 1 + len(plan.instruments),
        ppq=project.ppq,
        events=events,
    )


# --- pass 1: generated BRSO channels ---------------------------------------

def _build_brso_channels(donor_events: Events, plan: plan_mod.Plan) -> Events:
    out: Events = []
    index = plan.first_new_channel
    for instance in plan.instances:
        for instrument in instance.instruments:
            state = brso.build(
                keyswitches=[a.keyswitch for a in instrument.articulations],
                names=[a.title for a in instrument.articulations],
                colors=[a.color for a in instrument.articulations],
                port=instance.port,
                channel=instrument.midi_channel - 1,
                tail=plan.donor_tail,
            )
            out.extend(
                _clone_channel(
                    donor_events,
                    index=index,
                    name=instrument.title,
                    state=brso.serialize(state),
                    color=instance.color,
                    group=instance.port,
                )
            )
            index += 1
    return out


# --- pass 2: SINE channels -------------------------------------------------

def _rewrite_sine_channels(events: Events, blocks, plan: plan_mod.Plan) -> None:
    """Point each SINE channel at its section bus, color it, and group it.

    Rewritten last block first: a block with no color or group event of its own
    gains one, which moves every event after it, and the blocks still to be done
    are the ones in front.
    """
    ordered = sorted(plan.instances, key=lambda i: i.channel_index, reverse=True)
    for instance in ordered:
        block = blocks[instance.channel_index]
        buses = {i.midi_channel: i.bus for i in instance.instruments}
        rewritten: Events = []
        for eid, payload in events[block.span]:
            if eid in flp.CHAN_INSERT_EVENTS:
                # The channel sits on its section bus, and every plugin output
                # offset is measured from there.
                payload = _insert_payload(eid, instance.bus_insert)
            elif eid == flp.EV_CHAN_COLOR:
                payload = flp.encode_color(instance.color)
            elif eid == flp.EV_CHAN_GROUP:
                payload = struct.pack("<I", instance.port)
            elif eid == flp.EV_PLUGIN_STATE:
                payload = _rewrite_sine_state(payload, instance, buses)
            rewritten.append((eid, payload))
        events[block.span] = _with_color_and_group(
            rewritten, color=instance.color, group=instance.port
        )


def _rewrite_sine_state(payload: bytes, instance, buses: dict[int, int]) -> bytes:
    kind, chunks = wrapper.parse(payload)
    chunks = wrapper.set_midi_port(chunks, instance.port)
    outputs = len(wrapper.output_offsets(chunks))
    chunks = wrapper.set_output_offsets(chunks, instance.output_offsets(outputs))
    state = sine.parse(wrapper.get_chunk(chunks, wrapper.CHUNK_VST_STATE))
    for instrument in state.instruments:
        sine.set_output_bus(instrument, buses[int(instrument["midiChannel"])])
    chunks = wrapper.set_chunk(chunks, wrapper.CHUNK_VST_STATE, sine.serialize(state))
    return wrapper.serialize(kind, chunks)


def _insert_payload(eid: int, insert: int) -> bytes:
    """A channel's mixer insert, in the width the event id it was found under uses.

    One byte up to FL 21, two from FL 2026 on; the project keeps whichever it was
    saved with rather than being converted either way.
    """
    return insert.to_bytes(flp.payload_width(eid), "little")


def _with_color_and_group(block: Events, *, color: int, group: int) -> Events:
    """Add the color and filter-group events a channel block may not carry.

    FL writes no event for something it has nothing to say about, so a channel
    that was never colored has no color event and one never put in a filter group
    has no group event -- and rewriting events that are not there would leave the
    channel gray and in the first section's group. Both are added just before the
    event that closes the block; FL reads a block by event id, not by position.
    """
    wanted = (
        (flp.EV_CHAN_COLOR, flp.encode_color(color)),
        (flp.EV_CHAN_GROUP, struct.pack("<I", group)),
    )
    present = {eid for eid, _ in block}
    missing = [event for event in wanted if event[0] not in present]
    if not missing:
        return block
    end = next(
        (i for i, (eid, _) in enumerate(block) if eid == flp.EV_CHAN_END), len(block)
    )
    return block[:end] + missing + block[end:]


def _clone_channel(donor_events: Events, *, index, name, state, color, group) -> Events:
    cloned: Events = []
    for eid, payload in donor_events:
        if eid == flp.EV_CHAN_NEW:
            payload = struct.pack("<H", index)
        elif eid == flp.EV_PLUGIN_NAME:
            payload = flp.encode_text(name)
        elif eid == flp.EV_PLUGIN_STATE:
            payload = state
        elif eid == flp.EV_CHAN_COLOR:
            payload = flp.encode_color(color)
        elif eid == flp.EV_CHAN_ORDINAL:
            payload = struct.pack("<HH", index + 1, index + 1)
        elif eid == flp.EV_CHAN_GROUP:
            payload = struct.pack("<I", group)
        elif eid in flp.CHAN_INSERT_EVENTS:
            payload = _insert_payload(eid, 0)   # BRSO produces no audio
        cloned.append((eid, payload))
    return _with_color_and_group(cloned, color=color, group=group)


# --- pass 4: channel rack filter groups (last of the positional passes) -----

def _rewrite_filter_groups(events: Events, plan: plan_mod.Plan) -> Events:
    """Define one filter group per SINE instance, in port order.

    Two things FL does that are easy to get wrong:

    * A fresh project defines only "Unsorted", so the list has to be written at all --
      otherwise the generated indices point at groups that do not exist and those
      channels disappear from every filter but "All".
    * **FL sorts these names, and a channel's group index refers to the sorted
      position, not the order they appear in the file.** The names are therefore
      numbered ("01 SINE Strings", ...) so that sorted order equals port order. The
      hand-built reference template does exactly this, which is why its groups line up.
    """
    defined = [
        (flp.EV_CHAN_GROUP_NAME, flp.encode_text(instance.group_name))
        for instance in plan.instances
    ]
    if sorted(n.group_name for n in plan.instances) != [n.group_name for n in plan.instances]:
        raise ValueError(
            "filter group names do not sort into port order; FL indexes them by sorted "
            "position, so the groups would be mislabeled"
        )

    out: Events = []
    placed = False
    for eid, payload in events:
        if eid == flp.EV_CHAN_GROUP_NAME:
            if not placed:
                out.extend(defined)
                placed = True
            continue                      # drop whatever groups were there before
        if not placed and eid == flp.EV_CHAN_NEW:
            out.extend(defined)           # no groups existed; they belong before the channels
            placed = True
        out.append((eid, payload))

    if not placed:
        raise ValueError("project has no channels to attach filter groups to")
    return out


# --- pass 3: mixer ---------------------------------------------------------

def _insert_bodies(events: Events) -> list[tuple[int, int]]:
    """(first, last) event index of each insert's own events, in mixer order.

    A block runs from its EV_INSERT_PARAMS to its EV_INSERT_ICON. The color, the
    color flag and the name that follow the icon describe the *next* insert, which
    is how FL writes them and how `_rewrite_mixer` reads them back.
    """
    starts = [i for i, (eid, _) in enumerate(events) if eid == flp.EV_INSERT_PARAMS]
    icons = [i for i, (eid, _) in enumerate(events) if eid == flp.EV_INSERT_ICON]
    if len(starts) != len(icons) or any(i <= s for s, i in zip(starts, icons)):
        raise ValueError(
            f"the mixer has {len(starts)} insert block(s) and {len(icons)} icon event(s), "
            f"which do not pair up one to one; this project is not shaped the way FL "
            f"writes one"
        )
    return list(zip(starts, icons))


def _grow_mixer(events: Events, plan: plan_mod.Plan) -> Events:
    """Create the insert blocks the plan needs and the project does not have.

    From FL 2026 on the mixer is only as large as the project made it -- 16 inserts
    in a new one, up to 500 -- so most of the inserts this tool wires up do not
    exist yet and have to be written. A new block is a copy of the last ordinary
    one, which gives it FL's own defaults for everything the plan says nothing
    about; the walk below then names, colors and routes it like any other.

    The copies go after the last ordinary insert and before the mixer's trailing
    "current" block, so no existing insert changes index. Each one is preceded by
    its own EV_INSERT_HAS_COLOR, which is the slot FL keeps for it in front of the
    block rather than inside it.

    An older project carries a fixed 127-block mixer that cannot grow, and the
    plan's ceiling keeps the layout inside it, so it comes back untouched.
    """
    bodies = _insert_bodies(events)
    wanted = plan.highest_insert + 2        # Master .. highest, plus "current"
    if len(bodies) >= wanted:
        return events
    if not any(eid == flp.EV_INSERT_COUNT for eid, _ in events):
        raise ValueError(
            f"the layout needs mixer insert {plan.highest_insert}, but this project has "
            f"a fixed mixer of {len(bodies)} blocks that cannot be grown"
        )
    if len(bodies) < 3:
        raise ValueError(
            f"the mixer has {len(bodies)} insert block(s), so there is no ordinary "
            f"insert to copy new ones from"
        )

    first, icon = bodies[-2]                # the last insert before "current"
    template: Events = [(flp.EV_INSERT_HAS_COLOR, b"\x00"), *events[first : icon + 1]]
    grown = events[: icon + 1] + template * (wanted - len(bodies)) + events[icon + 1 :]
    count = wanted.to_bytes(flp.payload_width(flp.EV_INSERT_COUNT), "little")
    return [
        (eid, count if eid == flp.EV_INSERT_COUNT else payload) for eid, payload in grown
    ]


def _rewrite_mixer(events: Events, plan: plan_mod.Plan) -> Events:
    names: dict[int, str] = {}
    routes: dict[int, int] = {}
    colors: dict[int, int] = {}
    clear: set[int] = set()
    for instance in plan.instances:
        names[instance.bus_insert] = instance.bus_name
        routes[instance.bus_insert] = 0                 # section bus -> Master
        colors[instance.bus_insert] = instance.color
        # Every insert in the block feeds the section bus. With reserved slots that
        # includes the unused ones, so an instrument added by hand later lands
        # somewhere already routed; a compact block simply has none to spare.
        # Coloring the whole block matches the channel rack and makes the section
        # boundaries visible in the mixer.
        for insert in instance.block_inserts:
            routes[insert] = instance.bus_insert
            colors[insert] = instance.color
        for instrument in instance.instruments:
            names[instrument.insert] = instrument.title
        # Reserved slots are left unnamed. Any name already sitting there belongs to
        # a previous layout and would otherwise linger as exactly the kind of drift
        # this tool exists to remove.
        clear |= set(instance.block_inserts) - set(names)

    out: Events = []
    index = -1
    pending: tuple[int, bytes] | None = None

    for eid, payload in _grow_mixer(events, plan):
        if eid == flp.EV_INSERT_NAME:
            # Belongs to the insert whose parameter block comes next.
            pending = (eid, payload)
            continue
        if eid == flp.EV_INSERT_PARAMS:
            index += 1
            if index in names:
                out.append((flp.EV_INSERT_NAME, flp.encode_text(names[index])))
            elif pending is not None and index not in clear:
                out.append(pending)
            pending = None
            out.append((eid, payload))
            continue
        if eid == flp.EV_INSERT_ROUTING and index in routes:
            # A byte per destination insert. FL 21 wrote all 127 of them; FL 2026
            # trims the array to the last destination it has anything to say about,
            # so it has to be widened to reach this one -- and never narrowed, in
            # case a longer array is carrying something further along.
            table = bytearray(max(len(payload), routes[index] + 1))
            table[routes[index]] = 1
            out.append((eid, bytes(table)))
            continue
        # The color and its flag sit at the tail of the *previous* insert's block,
        # alongside the name event -- they describe the insert whose block comes next.
        # Inside a uniformly colored run this is invisible; it only shows at the
        # boundaries, which is why it survived the first round of testing.
        if eid == flp.EV_INSERT_COLOR and index + 1 in colors:
            continue                     # replaced below, right after the icon event
        if eid == flp.EV_INSERT_HAS_COLOR and index + 1 in colors:
            # Without this flag FL ignores the color event and draws the insert gray.
            out.append((eid, b"\x01"))
            continue
        if pending is not None:
            out.append(pending)
            pending = None
        out.append((eid, payload))
        if eid == flp.EV_INSERT_ICON and index + 1 in colors:
            # An insert with no color has no color event at all, so it has to be
            # inserted; FL writes it directly after the icon event.
            out.append((flp.EV_INSERT_COLOR, flp.encode_color(colors[index + 1])))

    if pending is not None:
        out.append(pending)
    return out


# --- pass 5: the registration stamp ----------------------------------------

def _blank_registration(events: Events) -> Events:
    """Empty the registration string the input was stamped with.

    FL writes event 200 on every save, carrying the registration of the installation
    that saved the project. Copied through untouched, it would mean a template built
    for somebody else still carries the registration of whoever generated it.

    Emptying it costs nothing, because FL does not read the field back: a project
    whose event 200 is empty -- or missing outright -- opens, renders and saves, and
    that save rewrites the field with the saving installation's own value. So the
    recipient's copy is stamped with the recipient's registration the first time they
    touch it, which is what it should have said all along.

    An empty payload rather than a deleted event, for two reasons: it is the shape FL
    itself writes for every other empty text field, and it is the shape FL restores a
    deleted event to anyway.

    The project author, event 207, is deliberately left alone. FL does not rewrite
    that one on save, so whatever an input carries there was put there on purpose.
    """
    blank = flp.encode_text("")
    return [
        (eid, blank if eid == flp.EV_REGNAME else payload) for eid, payload in events
    ]
