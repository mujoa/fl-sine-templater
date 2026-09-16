"""Turn a Plan into a mutated project. Only touches what the plan describes.

Four passes, ordered so that no pass invalidates positions the next one needs:

1. SINE channel edits     -- in place, event count unchanged
2. donor -> BRSO channels -- a slice replacement at the end of the channel region
3. mixer rewrite          -- a stateful walk, so it needs no precomputed positions
   (it may insert name and color events for inserts that have none)
4. filter groups          -- also a walk; it changes offsets ahead of the channels,
   so nothing position-based may follow it
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

    _rewrite_sine_channels(events, blocks, plan)

    generated = _build_brso_channels(donor_events, plan)
    events = events[: donor.start] + generated + events[donor.end + 1 :]

    events = _rewrite_mixer(events, plan)
    events = _rewrite_filter_groups(events, plan)

    return flp.Project(
        fmt=project.fmt,
        nch=project.nch - 1 + len(plan.instruments),
        ppq=project.ppq,
        events=events,
    )


# --- pass 1: SINE channels -------------------------------------------------

def _rewrite_sine_channels(events: Events, blocks, plan: plan_mod.Plan) -> None:
    for instance in plan.instances:
        block = blocks[instance.channel_index]
        buses = {i.midi_channel: i.bus for i in instance.instruments}
        for position in range(block.start, block.end + 1):
            eid, payload = events[position]
            if eid == flp.EV_CHAN_INSERT:
                # The channel sits on its section bus, and every plugin output
                # offset is measured from there.
                events[position] = (eid, struct.pack("<B", instance.bus_insert))
            elif eid == flp.EV_CHAN_COLOR:
                events[position] = (eid, flp.encode_color(instance.color))
            elif eid == flp.EV_CHAN_GROUP:
                events[position] = (eid, struct.pack("<I", instance.port))
            elif eid == flp.EV_PLUGIN_STATE:
                events[position] = (eid, _rewrite_sine_state(payload, instance, buses))


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


# --- pass 2: generated BRSO channels ---------------------------------------

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
        elif eid == flp.EV_CHAN_INSERT:
            payload = struct.pack("<B", 0)      # BRSO produces no audio
        cloned.append((eid, payload))
    return cloned


# --- pass 4: channel rack filter groups (runs last; see module docstring) ---

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

    for eid, payload in events:
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
            table = bytearray(len(payload))
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
