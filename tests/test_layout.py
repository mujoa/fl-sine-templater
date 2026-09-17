"""Mixer insert allocation, on synthetic plans -- no .flp needed.

The end-to-end tests cover the six-instance reference; these cover the edges of
the layout itself, where the two block widths differ most.
"""
from __future__ import annotations

import pytest

from sine_templater import plan as plan_mod, wrapper


def _instance(port: int, channels: list[int], *, reserve: bool) -> plan_mod.InstancePlan:
    instance = plan_mod.InstancePlan(
        channel_index=port,
        name=f"SINE {port}",
        port=port,
        bus_insert=port + 1,
        bus_name=f"SINE {port} (main)",
        group_name=f"{port + 1:02d} SINE {port}",
        base_insert=0,
        color=0,
        reserve_slots=reserve,
    )
    instance.instruments = [
        plan_mod.InstrumentPlan(title=f"i{c}", midi_channel=c, insert=0, articulations=[])
        for c in channels
    ]
    return instance


def _layout(counts: list[list[int]], *, reserve: bool) -> list[plan_mod.InstancePlan]:
    instances = [_instance(p, c, reserve=reserve) for p, c in enumerate(counts)]
    plan_mod._assign_inserts(instances)
    return instances


def test_blocks_tile_the_mixer_without_gaps_or_overlap():
    counts = [[1], [1, 2, 3], [5], list(range(1, 17)), [2, 4]]
    for reserve in (True, False):
        instances = _layout(counts, reserve=reserve)
        claimed = [i.bus_insert for i in instances]
        for instance in instances:
            claimed += list(instance.block_inserts)
        assert claimed == sorted(claimed), "blocks are not laid out in order"
        assert len(set(claimed)) == len(claimed), "an insert is claimed twice"
        assert claimed == list(range(1, len(claimed) + 1)), "the layout has a gap"


def test_reserved_slots_put_an_instrument_at_its_midi_channel():
    instance = _layout([[3, 1, 9]], reserve=True)[0]
    assert {i.midi_channel: i.insert - instance.base_insert for i in instance.instruments} == {
        3: 2, 1: 0, 9: 8,
    }


def test_compact_packs_instruments_in_midi_channel_order():
    instance = _layout([[9, 1, 3]], reserve=False)[0]
    assert {i.midi_channel: i.insert - instance.base_insert for i in instance.instruments} == {
        1: 0, 3: 1, 9: 2,
    }


def test_compact_fits_far_more_than_seven_instances():
    counts = [[1, 2, 3]] * 20                      # 20 instances, 60 instruments
    instances = _layout(counts, reserve=False)
    assert instances[-1].block_end == 20 + 20 * 3
    assert instances[-1].block_end <= plan_mod.MAX_INSERT_FIXED


def test_reserved_slots_still_run_out_at_eight_instances():
    with pytest.raises(ValueError, match="mixer inserts up to 136"):
        _layout([[1]] * 8, reserve=True)


def test_compact_runs_out_eventually():
    with pytest.raises(ValueError, match="only has 125"):
        _layout([list(range(1, 17))] * 8, reserve=False)   # 8 * 16 + 8 = 136


def test_compact_sends_unused_outputs_to_the_section_bus():
    """Offset 0 is the channel's own insert, which is now the bus itself."""
    instance = _layout([[2, 5]], reserve=False)[0]
    offsets = instance.output_offsets(16)
    assert instance.bus_insert + offsets[2] == instance.instruments[0].insert
    assert instance.bus_insert + offsets[5] == instance.instruments[1].insert
    assert [o for n, o in enumerate(offsets) if n not in (2, 5)] == [0] * 14


def test_reserved_output_offsets_fan_every_midi_channel_out():
    """Output N is MIDI channel N; output 0 is SINE's own main bus."""
    for instance in _layout([[2, 5], [1], [16]], reserve=True):
        offsets = instance.output_offsets(1 + plan_mod.SLOTS_PER_INSTANCE)
        assert offsets[0] == 0, "the main bus stays on the section bus"
        assert [instance.bus_insert + o for o in offsets[1:]] == list(
            instance.block_inserts
        )


def test_shipped_palette_is_usable():
    palette = plan_mod.load_channel_colors()
    assert len(palette) == 12
    assert len(set(palette)) == len(palette), "two sections would share a color"
    assert all(0 <= c <= 0xFFFFFF for c in palette), "not a 24-bit RGB value"
    assert palette[0] == 0xC30E0E


def test_channel_colors_cycle_past_the_palette():
    palette = [0x111111, 0x222222, 0x333333]
    assert [plan_mod.channel_color(palette, p) for p in range(3)] == palette
    # more sections than colors reuses the list from the top
    assert plan_mod.channel_color(palette, 3) == palette[0]
    assert plan_mod.channel_color(palette, 7) == palette[1]
    assert plan_mod.channel_color([0xABCDEF], 40) == 0xABCDEF


def _palette(tmp_path, body: str):
    path = tmp_path / "palette.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_colors_file_accepts_the_shapes_a_yaml_list_comes_in(tmp_path):
    path = _palette(
        tmp_path,
        "# a leading comment\n"
        "\n"
        "colors:\n"
        '  - "#c30e0e"   # double quoted, trailing comment\n'
        "  - '#FFC000'   # single quoted, upper case\n"
        "  - ffffff      # bare, no leading hash\n"
        "  # an indented comment\n",
    )
    assert plan_mod.load_channel_colors(path) == [0xC30E0E, 0xFFC000, 0xFFFFFF]


def test_colors_file_accepts_a_bare_top_level_list(tmp_path):
    path = _palette(tmp_path, '- "#c30e0e"\n- "#ffc000"\n')
    assert plan_mod.load_channel_colors(path) == [0xC30E0E, 0xFFC000]


def test_unquoted_color_names_the_fix(tmp_path):
    """'- #c30e0e' is a comment in real YAML, so say so instead of 'no color'."""
    path = _palette(tmp_path, "colors:\n  - #c30e0e\n")
    with pytest.raises(ValueError, match="unquoted # starts a YAML comment"):
        plan_mod.load_channel_colors(path)


@pytest.mark.parametrize(
    "body, match",
    [
        ('colors:\n  - "#12345"\n', "not a #RRGGBB"),      # five digits
        ('colors:\n  - "#c30e0ee"\n', "not a #RRGGBB"),    # seven
        ('colors:\n  - "#gggggg"\n', "not a #RRGGBB"),     # not hex
        ('colors:\n  - "rgb(195, 14, 14)"\n', "not a #RRGGBB"),
        ('colors: ["#c30e0e"]\n', "expected a color list item"),   # flow style
        ('palette:\n  - "#c30e0e"\n', "expected a color list item"),  # wrong key
        ("# only comments\n\n", "defines no colors"),
        ("colors:\n", "defines no colors"),
        ("", "defines no colors"),
    ],
)
def test_colors_file_rejects_what_it_cannot_read(tmp_path, body, match):
    with pytest.raises(ValueError, match=match):
        plan_mod.load_channel_colors(_palette(tmp_path, body))


def test_missing_colors_file_is_reported_not_silently_defaulted(tmp_path):
    with pytest.raises(ValueError, match="cannot read the colors file"):
        plan_mod.load_channel_colors(tmp_path / "nope.yaml")


def test_set_output_offsets_refuses_a_mismatched_table():
    chunks = [(wrapper.CHUNK_OUTPUT_ROUTING, bytes(4 * wrapper.OUTPUT_ENTRY_LEN))]
    with pytest.raises(ValueError, match="3 output offsets for a plugin with 4 outputs"):
        wrapper.set_output_offsets(chunks, [0, 1, 2])
    written = wrapper.set_output_offsets(chunks, [0, 3, 1, 2])
    assert wrapper.output_offsets(written) == [0, 3, 1, 2]
