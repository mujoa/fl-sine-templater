"""Post-generation checks. Re-parses the output and verifies the wiring holds."""
from __future__ import annotations

from . import brso, flp, model, plan as plan_mod, sine, wrapper


def check(data: bytes, plan: plan_mod.Plan) -> list[str]:
    """Return a list of problems; empty means the output is consistent."""
    problems: list[str] = []
    project = flp.parse(data)

    starts = sum(1 for eid, _ in project.events if eid == flp.EV_CHAN_NEW)
    ends = sum(1 for eid, _ in project.events if eid == flp.EV_CHAN_END)
    if not (project.nch == starts == ends):
        problems.append(f"channel count mismatch: header {project.nch}, {starts} starts, {ends} ends")
        return problems

    problems += _mixer_size(project, plan)

    blocks = model.channel_blocks(project)
    names = _insert_names(project)
    colors = _insert_colors(project)
    problems += _articulation_limits(plan)
    problems += _layout_conflicts(plan)

    groups = [
        flp.decode_text(payload)
        for eid, payload in project.events
        if eid == flp.EV_CHAN_GROUP_NAME
    ]
    expected_groups = [instance.group_name for instance in plan.instances]
    if sorted(groups) != groups:
        problems.append(
            f"filter group names {groups} are not in sorted order; FL indexes them by "
            f"sorted position, so the groups would be mislabeled"
        )
    if groups != expected_groups:
        problems.append(f"channel filter groups are {groups}, expected {expected_groups}")
    referenced = {
        int.from_bytes(payload, "little")
        for eid, payload in project.events
        if eid == flp.EV_CHAN_GROUP
    }
    dangling = sorted(g for g in referenced if not 0 <= g < len(groups))
    if dangling:
        problems.append(
            f"channels reference filter groups {dangling} that do not exist "
            f"({len(groups)} defined); those channels would vanish from the rack filter"
        )

    wanted = {
        (inst.port, i.midi_channel - 1): (inst, i)
        for inst in plan.instances
        for i in inst.instruments
    }
    seen: dict[tuple[int, int], str] = {}

    for block in blocks:
        if not model.is_brso(block):
            continue
        state = brso.parse(model.plugin_state(project, block))
        key = (state.port, state.channel)
        if key in seen:
            problems.append(
                f"two BRSO channels target port {key[0]} / channel {key[1]}: "
                f"{seen[key]!r} and {block.name!r}"
            )
        seen[key] = block.name
        if key not in wanted:
            problems.append(f"BRSO {block.name!r} targets unplanned port/channel {key}")
            continue
        owner, instrument = wanted[key]
        problems += _channel_appearance(
            project, block, color=owner.color, group=owner.port
        )
        expected = [(a.title, a.keyswitch) for a in instrument.articulations]
        actual = [(name, ks) for _, ks, name in state.used_cells()]
        if actual != expected:
            problems.append(
                f"BRSO {block.name!r} articulations are {actual}, expected {expected}"
            )
        cell_colors = [state.arrays[brso.ARRAY_COLOR][i] for i in range(state.cells)]
        if len(cell_colors) <= brso.MAX_CELLS:      # over that, _articulation_limits speaks
            clash = _color_clash(block.name, cell_colors)
            if clash:
                problems.append(clash)

    for key, (_, instrument) in wanted.items():
        if key not in seen:
            problems.append(f"no BRSO channel generated for {instrument.title!r} (port/channel {key})")

    for instance in plan.instances:
        block = _sine_block(blocks, instance)
        if block is None:
            problems.append(
                f"channel {instance.channel_index + 1} should be the SINE instance "
                f"{instance.name!r} and is not; the output does not match the plan"
            )
            continue
        problems += _channel_appearance(
            project, block, color=instance.color, group=instance.port
        )
        _, chunks = wrapper.parse(model.plugin_state(project, block))
        port = wrapper.get_midi_port(chunks)
        if port != instance.port:
            problems.append(f"{instance.name!r} MIDI input port is {port}, expected {instance.port}")
        channel_insert = _channel_insert(project, block)
        if channel_insert != instance.bus_insert:
            problems.append(
                f"{instance.name!r} sits on mixer insert {channel_insert}, expected its "
                f"section bus {instance.bus_insert}; every plugin output offset is "
                f"relative to it, so the whole block would be shifted"
            )
        offsets = wrapper.output_offsets(chunks)
        expected_offsets = instance.output_offsets(len(offsets))
        if offsets != expected_offsets:
            problems.append(
                f"{instance.name!r} plugin output offsets are {offsets}, expected "
                f"{expected_offsets}; instrument audio would land on the wrong inserts"
            )
        # An offset table shorter than the highest MIDI channel in use leaves an
        # instrument's insert with nothing feeding it -- silent, and easy to miss.
        fed = {instance.bus_insert + offset for offset in offsets}
        for instrument in instance.instruments:
            if instrument.insert not in fed:
                problems.append(
                    f"{instance.name!r} / {instrument.title!r} is on insert "
                    f"{instrument.insert}, which no plugin output reaches "
                    f"(SINE exposes {len(offsets)} outputs, MIDI channels 1-{len(offsets) - 1})"
                )
        state = sine.parse(wrapper.get_chunk(chunks, wrapper.CHUNK_VST_STATE))
        for instrument in state.instruments:
            channel = int(instrument["midiChannel"])
            for mic in instrument["micPositions"]:
                if int(mic["outputBusIndex"]) != channel:
                    problems.append(
                        f"{instance.name!r} / {instrument['title']!r} mic {mic['title']!r} "
                        f"is on bus {mic['outputBusIndex']}, expected {channel}"
                    )
        if names.get(instance.bus_insert) != instance.bus_name:
            problems.append(
                f"insert {instance.bus_insert} is named {names.get(instance.bus_insert)!r}, "
                f"expected {instance.bus_name!r}"
            )
        for instrument in instance.instruments:
            if names.get(instrument.insert) != instrument.title:
                problems.append(
                    f"insert {instrument.insert} is named {names.get(instrument.insert)!r}, "
                    f"expected {instrument.title!r}"
                )
        used = {i.insert for i in instance.instruments}
        for insert in instance.block_inserts:
            if insert not in used and insert in names:
                problems.append(
                    f"reserved insert {insert} still carries the name "
                    f"{names[insert]!r}; it should be unnamed"
                )
        for insert in (instance.bus_insert, *instance.block_inserts):
            if colors.get(insert) != instance.color:
                problems.append(
                    f"insert {insert} color is {colors.get(insert)}, "
                    f"expected {instance.color}"
                )

    return problems


def warnings(plan: plan_mod.Plan) -> list[str]:
    """Things worth saying out loud that are not reasons to refuse to write.

    SINE lets two articulations of one instrument carry the same keyswitch note.
    They are copied through verbatim, but in BRSO both cells then answer to that
    note and only the first is reachable. That is the SINE instrument's own doing,
    not a wiring fault, so it is reported and the template is still written.

    The other one is a name BRSO cannot store as it stands: its cells are ASCII,
    so an en dash or an accent is folded to its plain equivalent. The template is
    correct either way, but the name in the piano roll is not quite the one in
    SINE, which is worth knowing before wondering why.
    """
    out: list[str] = []
    out += _folded_names(plan)
    for instance in plan.instances:
        for instrument in instance.instruments:
            by_note: dict[int, list[str]] = {}
            for articulation in instrument.articulations:
                by_note.setdefault(articulation.keyswitch, []).append(articulation.title)
            clashes = {note: t for note, t in by_note.items() if len(t) > 1}
            if not clashes:
                continue
            detail = "; ".join(
                f"note {note}: " + ", ".join(repr(title) for title in titles)
                for note, titles in sorted(clashes.items())
            )
            out.append(
                f"{instance.name!r} / {instrument.title!r} (MIDI channel "
                f"{instrument.midi_channel}) has {len(instrument.articulations)} "
                f"articulations on {len(by_note)} keyswitch notes; where a note is "
                f"shared, only the first articulation on it can be selected -- {detail}"
            )
    return out


def _folded_names(plan: plan_mod.Plan) -> list[str]:
    """Articulation names BRSO's ASCII cells could not hold verbatim."""
    out: list[str] = []
    for instance, instrument in plan.instruments:
        folded = [
            a for a in instrument.articulations
            if a.source_title and a.source_title != a.title
        ]
        if not folded:
            continue
        detail = "; ".join(f"{a.source_title!r} -> {a.title!r}" for a in folded)
        out.append(
            f"{instance.name!r} / {instrument.title!r} (MIDI channel "
            f"{instrument.midi_channel}): BRSO stores articulation names as ASCII, so "
            f"{len(folded)} name(s) are written with plain characters -- {detail}"
        )
    return out


def _mixer_size(project: flp.Project, plan: plan_mod.Plan) -> list[str]:
    """The mixer must actually hold every insert the layout puts something on.

    An insert past the end of the mixer is not an error FL reports: the routing
    and the names simply have nowhere to land, and the template opens missing
    whatever was meant to sit there. On a dynamic mixer the blocks are created
    during the build, so this is the check that they all got written.
    """
    problems: list[str] = []
    blocks = model.insert_count(project)
    # The last block is the mixer's "current" insert, not one the layout may use.
    if plan.highest_insert + 2 > blocks:
        problems.append(
            f"the mixer holds {blocks} insert blocks, but the layout uses inserts up "
            f"to {plan.highest_insert}; the ones past the end were never created"
        )
    declared = [
        int.from_bytes(payload, "little")
        for eid, payload in project.events
        if eid == flp.EV_INSERT_COUNT
    ]
    for count in declared:
        if count != blocks:
            problems.append(
                f"the mixer says it has {count} inserts but carries {blocks}"
            )
    return problems


def _articulation_limits(plan: plan_mod.Plan) -> list[str]:
    """No BRSO channel may hold more articulations than its grid has cells.

    This is a refusal, not a truncation: writing more cells than BRSO draws would
    produce a template that loads and looks right while the articulations past the
    grid simply cannot be selected. Better to say so before anything is written.
    """
    problems: list[str] = []
    for instance in plan.instances:
        for instrument in instance.instruments:
            count = len(instrument.articulations)
            if count > brso.MAX_CELLS:
                problems.append(
                    f"{instance.name!r} / {instrument.title!r} (MIDI channel "
                    f"{instrument.midi_channel}) has {count} articulations; BRSO holds "
                    f"{brso.MAX_CELLS} per channel ({brso.GRID_COLUMNS}x{brso.GRID_ROWS} "
                    f"grid), so {count - brso.MAX_CELLS} of them cannot be placed. "
                    f"Drop the extras in SINE, or split the instrument across two MIDI "
                    f"channels."
                )
    return problems


def _color_clash(name: str, cell_colors: list[int]) -> str | None:
    """Two articulations of one instrument must not share a piano-roll color.

    Overflowing the grid is _articulation_limits' business and says so plainly, so
    what is left for this check is a palette that has stopped carrying one distinct
    color per cell -- which would make two articulations look alike in the piano
    roll for no visible reason.
    """
    if len(set(cell_colors)) == len(cell_colors):
        return None
    repeated = sorted({c for c in cell_colors if cell_colors.count(c) > 1})
    return (
        f"BRSO {name!r} gives color(s) {repeated} to more than one of its "
        f"{len(cell_colors)} articulations; ARTICULATION_COLORS in plan.py must hold "
        f"{brso.MAX_CELLS} distinct values and holds "
        f"{len(set(plan_mod.ARTICULATION_COLORS))}"
    )


def _layout_conflicts(plan: plan_mod.Plan) -> list[str]:
    """The plan's own mixer layout: nothing may claim an insert twice.

    Block widths vary with the layout, so this is the guard that the blocks still
    tile the mixer without overlapping each other, the section buses, or Master.
    """
    problems: list[str] = []
    owners: dict[int, str] = {0: "Master"}
    for instance in plan.instances:
        owner = f"bus {instance.bus_name!r}"
        if instance.bus_insert in owners:
            problems.append(
                f"insert {instance.bus_insert} is claimed by both "
                f"{owners[instance.bus_insert]} and {owner}"
            )
        owners.setdefault(instance.bus_insert, owner)

    for instance in plan.instances:
        for insert in instance.block_inserts:
            owner = f"the {instance.name!r} block"
            if insert in owners:
                problems.append(
                    f"insert {insert} is claimed by both {owners[insert]} and {owner}"
                )
            owners.setdefault(insert, owner)

        taken: dict[int, str] = {}
        for instrument in instance.instruments:
            if instrument.insert not in instance.block_inserts:
                problems.append(
                    f"{instance.name!r} / {instrument.title!r} is on insert "
                    f"{instrument.insert}, outside its block "
                    f"{instance.block_range}"
                )
            if instrument.insert in taken:
                problems.append(
                    f"{instance.name!r}: {taken[instrument.insert]!r} and "
                    f"{instrument.title!r} share insert {instrument.insert}"
                )
            taken.setdefault(instrument.insert, instrument.title)
    return problems


def _sine_block(
    blocks: list[model.ChannelBlock], instance: plan_mod.InstancePlan
) -> model.ChannelBlock | None:
    """An instance's own channel, found by position rather than by name.

    Two SINE instances can carry the same display name -- FL gives every one it
    adds the same default -- so a lookup by name would check both against the
    first block and report the second's port, insert and routing as wrong. The
    plan carries the channel's index for exactly this, and `apply` leaves the
    SINE channels where it found them.
    """
    if not 0 <= instance.channel_index < len(blocks):
        return None
    block = blocks[instance.channel_index]
    return block if model.is_sine(block) else None


def _channel_appearance(
    project: flp.Project, block: model.ChannelBlock, *, color: int, group: int
) -> list[str]:
    """A channel's rack color and filter group.

    FL writes neither event for a channel that has never had one, so on a donor
    without them the generated channels would come out gray and all in the first
    section's group -- visible at a glance in the rack, and nothing else here
    looks at them.
    """
    problems: list[str] = []
    payload = _channel_event(project, block, flp.EV_CHAN_COLOR)
    actual = flp.decode_color(payload) if payload is not None else None
    if actual != color:
        problems.append(
            f"channel {block.name!r} color is "
            f"{'unset' if actual is None else actual}, expected {color}"
        )
    payload = _channel_event(project, block, flp.EV_CHAN_GROUP)
    actual = int.from_bytes(payload, "little") if payload is not None else None
    if actual != group:
        problems.append(
            f"channel {block.name!r} is in filter group "
            f"{'none' if actual is None else actual}, expected {group}"
        )
    return problems


def _channel_event(
    project: flp.Project, block: model.ChannelBlock, eid: int
) -> bytes | None:
    for event_id, payload in project.events[block.span]:
        if event_id == eid:
            return payload
    return None


def _channel_insert(project: flp.Project, block: model.ChannelBlock) -> int | None:
    for eid, payload in project.events[block.span]:
        if eid in flp.CHAN_INSERT_EVENTS:
            return int.from_bytes(payload, "little")
    return None


def _insert_colors(project: flp.Project) -> dict[int, int]:
    """Effective color per insert.

    Both events sit in the *previous* insert's block, so they are attributed to
    index + 1, and the color only applies when the flag is set.
    """
    values: dict[int, int] = {}
    enabled: dict[int, bool] = {}
    index = -1
    for eid, payload in project.events:
        if eid == flp.EV_INSERT_PARAMS:
            index += 1
        elif index < 0:
            continue
        elif eid == flp.EV_INSERT_COLOR:
            values[index + 1] = flp.decode_color(payload)
        elif eid == flp.EV_INSERT_HAS_COLOR:
            enabled[index + 1] = bool(payload[0])
    return {i: v for i, v in values.items() if enabled.get(i)}


def _insert_names(project: flp.Project) -> dict[int, str]:
    positions = model.insert_name_events(project)
    return {
        insert: flp.decode_text(project.events[position][1])
        for insert, position in positions.items()
    }
