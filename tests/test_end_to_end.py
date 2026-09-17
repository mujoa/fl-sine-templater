"""Full build against a contract-shaped fixture derived from the reference project."""
from __future__ import annotations

import pytest

import make_fixture
import paths
from sine_templater import (
    apply as apply_mod, brso, flp, model, plan as plan_mod, validate, wrapper,
)

EXPECTED_INSTANCES = 6
EXPECTED_INSTRUMENTS = 36

pytestmark = pytest.mark.skipif(
    not make_fixture.SOURCE.is_file(),
    reason=f"{make_fixture.SOURCE} not available; {paths.MISSING}",
)


@pytest.fixture(scope="module")
def built():
    project = flp.parse(make_fixture.build())
    plan = plan_mod.derive(project)
    data = flp.serialize(apply_mod.apply(project, plan))
    return project, plan, data


@pytest.fixture(scope="module")
def built_compact():
    project = flp.parse(make_fixture.build())
    plan = plan_mod.derive(project, compact=True)
    data = flp.serialize(apply_mod.apply(project, plan))
    return project, plan, data


def test_fixture_matches_the_input_contract():
    project = flp.parse(make_fixture.build())
    blocks = model.channel_blocks(project)
    assert sum(1 for b in blocks if model.is_sine(b)) == EXPECTED_INSTANCES
    brso_blocks = [b for b in blocks if model.is_brso(b)]
    assert len(brso_blocks) == 1
    assert brso_blocks[0].index == len(blocks) - 1


def test_plan_shape(built):
    _, plan, _ = built
    assert len(plan.instances) == EXPECTED_INSTANCES
    assert len(plan.instruments) == EXPECTED_INSTRUMENTS

    # blocks are contiguous, non-overlapping, and start after the section buses
    previous_end = EXPECTED_INSTANCES
    for instance in plan.instances:
        assert instance.base_insert == previous_end + 1
        previous_end = instance.base_insert + plan_mod.SLOTS_PER_INSTANCE - 1
    assert previous_end <= plan.max_insert


def test_sine_channels_sit_on_their_section_bus(built):
    """The channel has no insert of its own; the block is instruments and nothing else."""
    _, plan, data = built
    project = flp.parse(data)
    blocks = {b.name: b for b in model.channel_blocks(project) if model.is_sine(b)}
    for instance in plan.instances:
        inserts = [
            int.from_bytes(payload, "little")
            for eid, payload in project.events[blocks[instance.name].span]
            if eid in flp.CHAN_INSERT_EVENTS
        ]
        assert inserts == [instance.bus_insert]
        assert instance.bus_insert not in instance.block_inserts
        assert set(instance.block_inserts) == set(
            range(instance.base_insert, instance.base_insert + plan_mod.SLOTS_PER_INSTANCE)
        )


def test_output_validates(built):
    _, plan, data = built
    assert validate.check(data, plan) == []


def test_output_roundtrips(built):
    _, _, data = built
    assert flp.serialize(flp.parse(data)) == data


def test_generated_channels(built):
    _, plan, data = built
    project = flp.parse(data)
    blocks = model.channel_blocks(project)
    assert project.nch == len(blocks) == EXPECTED_INSTANCES + EXPECTED_INSTRUMENTS
    assert sum(1 for b in blocks if model.is_brso(b)) == EXPECTED_INSTRUMENTS
    # the donor is gone: every BRSO channel corresponds to a planned instrument
    titles = {i.title for _, i in plan.instruments}
    assert {b.name for b in blocks if model.is_brso(b)} == titles


def test_articulations_are_verbatim_and_distinctly_colored(built):
    _, plan, data = built
    project = flp.parse(data)
    by_name = {b.name: b for b in model.channel_blocks(project) if model.is_brso(b)}
    for _, instrument in plan.instruments:
        state = brso.parse(model.plugin_state(project, by_name[instrument.title]))
        assert state.names == [a.title for a in instrument.articulations]
        assert state.arrays[brso.ARRAY_KEYSWITCH] == [
            a.keyswitch for a in instrument.articulations
        ]
        colors = state.arrays[brso.ARRAY_COLOR]
        assert len(set(colors)) == len(colors)


def test_reserved_slots_are_routed_but_unnamed(built):
    _, plan, data = built
    project = flp.parse(data)
    names = {
        insert: flp.decode_text(project.events[position][1])
        for insert, position in model.insert_name_events(project).items()
    }
    for instance in plan.instances:
        used = {i.insert for i in instance.instruments}
        for insert in instance.block_inserts:
            if insert not in used:
                assert insert not in names


def test_sections_take_the_palette_in_port_order(built):
    _, plan, _ = built
    palette = plan_mod.load_channel_colors()
    assert [i.color for i in plan.instances] == palette[:EXPECTED_INSTANCES]


def test_channel_rack_is_colored_by_section(built):
    """Both the SINE channel and every BRSO channel it generated take its color."""
    _, plan, data = built
    project = flp.parse(data)
    by_name = {b.name: b for b in model.channel_blocks(project)}
    for instance in plan.instances:
        titles = [instance.name] + [i.title for i in instance.instruments]
        for title in titles:
            colors = [
                flp.decode_color(payload)
                for eid, payload in project.events[by_name[title].span]
                if eid == flp.EV_CHAN_COLOR
            ]
            assert colors == [instance.color], f"{title} is colored {colors}"


def test_compact_plan_packs_the_blocks(built_compact):
    _, plan, _ = built_compact
    previous_end = EXPECTED_INSTANCES              # blocks start after the section buses
    for instance in plan.instances:
        assert instance.base_insert == previous_end + 1
        # exactly one insert per instrument, and nothing else
        assert instance.block_size == len(instance.instruments)
        expected = [
            instance.base_insert + slot
            for slot, _ in enumerate(sorted(instance.instruments, key=lambda i: i.midi_channel))
        ]
        assert sorted(i.insert for i in instance.instruments) == expected
        previous_end = instance.block_end
    assert plan.highest_insert == EXPECTED_INSTANCES + EXPECTED_INSTRUMENTS


def test_compact_uses_fewer_inserts_than_reserved_slots(built, built_compact):
    _, reserved, _ = built
    _, compact, _ = built_compact
    assert compact.highest_insert < reserved.highest_insert
    assert reserved.highest_insert == EXPECTED_INSTANCES * (
        plan_mod.SLOTS_PER_INSTANCE + 1
    )


def test_compact_output_validates(built_compact):
    _, plan, data = built_compact
    assert validate.check(data, plan) == []


def test_compact_output_roundtrips(built_compact):
    _, _, data = built_compact
    assert flp.serialize(flp.parse(data)) == data


def test_compact_changes_only_the_mixer_layout(built, built_compact):
    """Same instruments, same ports, same MIDI channels -- only the inserts move."""
    _, reserved, _ = built
    _, compact, _ = built_compact

    def identity(plan):
        return [
            (inst.name, inst.port, inst.channel_index, inst.group_name, inst.color,
             i.title, i.midi_channel, [(a.title, a.keyswitch, a.color) for a in i.articulations])
            for inst, i in plan.instruments
        ]

    assert identity(compact) == identity(reserved)
    assert compact.first_new_channel == reserved.first_new_channel


def test_compact_wires_each_instrument_to_its_own_insert(built_compact):
    """Follow SINE's own output table: MIDI channel c -> output c -> one insert."""
    _, plan, data = built_compact
    project = flp.parse(data)
    blocks = {b.name: b for b in model.channel_blocks(project) if model.is_sine(b)}
    fed: dict[int, str] = {}
    for instance in plan.instances:
        _, chunks = wrapper.parse(model.plugin_state(project, blocks[instance.name]))
        offsets = wrapper.output_offsets(chunks)
        for instrument in instance.instruments:
            insert = instance.bus_insert + offsets[instrument.midi_channel]
            assert insert == instrument.insert
            assert insert not in fed, f"{fed.get(insert)} and {instrument.title} share {insert}"
            fed[insert] = instrument.title
        # outputs with no instrument behind them fall back to the section bus,
        # never onto a neighbor's insert
        for offset in offsets:
            insert = instance.bus_insert + offset
            assert insert == instance.bus_insert or insert in instance.block_inserts
    assert len(fed) == EXPECTED_INSTRUMENTS


def test_compact_leaves_no_reserved_slots(built_compact):
    _, plan, data = built_compact
    project = flp.parse(data)
    names = model.insert_name_events(project)
    for instance in plan.instances:
        used = {i.insert for i in instance.instruments}
        assert set(instance.block_inserts) == used
        assert all(insert in names for insert in instance.block_inserts)


def test_hand_built_template_is_rejected_as_input():
    """The reference file has 36 BRSO channels, so it fails the single-donor rule."""
    project = flp.parse(paths.reference().read_bytes())
    with pytest.raises(ValueError, match="exactly one BRSO Articulate"):
        plan_mod.derive(project)


def test_donor_must_be_the_last_channel():
    project = flp.parse(make_fixture.build())
    blocks = model.channel_blocks(project)
    donor = blocks[-1]
    others = blocks[0].start, blocks[-2].end + 1
    # move the donor ahead of the SINE instances
    moved = (
        project.events[: others[0]]
        + project.events[donor.span]
        + project.events[others[0] : others[1]]
        + project.events[donor.end + 1 :]
    )
    shuffled = flp.Project(project.fmt, project.nch, project.ppq, moved)
    with pytest.raises(ValueError, match="must be the last channel"):
        plan_mod.derive(shuffled)


def test_no_sine_instances_is_rejected():
    project = flp.parse(make_fixture.build())
    blocks = model.channel_blocks(project)
    sine_start, sine_end = blocks[0].start, blocks[-2].end + 1
    stripped = flp.Project(
        project.fmt,
        1,
        project.ppq,
        project.events[:sine_start] + project.events[sine_end:],
    )
    with pytest.raises(ValueError, match="no SINE Player instances"):
        plan_mod.derive(stripped)
