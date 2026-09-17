"""The palette: the shipped defaults, writing a colors file, and the values the
editor accepts from a keyboard.

Reading a colors file is covered in test_layout.py; this is the other direction.
"""
from __future__ import annotations

import pytest

from sine_templater import core, palette_editor, plan as plan_mod

DEFAULTS = [0xC30E0E, 0xFFC000, 0xB45F06, 0x56B82C, 0x3D85C6, 0xEEEEEE,
            0x333333, 0x7B1EC9, 0xE87D14, 0xED72E3, 0x1BD69A, 0x1DE4EB]


def test_shipped_file_is_what_the_defaults_render_to():
    """The two copies of the palette -- the tuple in `plan` and the YAML beside it
    -- have to agree, and the writer is what keeps them agreeing."""
    shipped = plan_mod.DEFAULT_COLORS_FILE.read_text(encoding="utf-8")
    assert plan_mod.dump_channel_colors(plan_mod.default_channel_colors()) == shipped


def test_defaults_are_the_twelve_documented_colors():
    assert plan_mod.default_channel_colors() == DEFAULTS
    assert len(set(DEFAULTS)) == len(DEFAULTS), "two sections would share a color"


def test_written_palette_reads_back_unchanged(tmp_path):
    colors = [0x000000, 0xFFFFFF, 0x0A0B0C, 0xC30E0E]
    path = plan_mod.write_channel_colors(tmp_path / "custom.yaml", colors)
    assert plan_mod.load_channel_colors(path) == colors


def test_written_palette_names_only_the_colors_that_have_names(tmp_path):
    body = plan_mod.dump_channel_colors([0xC30E0E, 0x0A0B0C])
    assert '- "#c30e0e"   # red' in body
    assert '- "#0a0b0c"\n' in body


def test_writing_replaces_what_was_there(tmp_path):
    path = tmp_path / "custom.yaml"
    plan_mod.write_channel_colors(path, [0x111111, 0x222222, 0x333333])
    plan_mod.write_channel_colors(path, [0x444444])
    assert plan_mod.load_channel_colors(path) == [0x444444]
    assert not list(tmp_path.glob("*.new")), "the temporary file was left behind"


@pytest.mark.parametrize(
    "colors, match",
    [
        ([], "at least one color"),
        ([0x1000000], "not a 24-bit RGB value"),
        ([-1], "not a 24-bit RGB value"),
    ],
)
def test_writing_refuses_what_could_not_be_read_back(tmp_path, colors, match):
    with pytest.raises(ValueError, match=match):
        plan_mod.write_channel_colors(tmp_path / "custom.yaml", colors)


def test_unwritable_destination_is_reported_as_a_build_error(tmp_path):
    blocked = tmp_path / "file.txt"
    blocked.write_text("not a directory", encoding="utf-8")
    with pytest.raises(core.BuildError, match="cannot write the colors file"):
        core.save_colors([0xC30E0E], blocked / "palette.yaml")


def test_a_saved_palette_is_what_the_next_run_reads(tmp_path, monkeypatch):
    """From a checkout the bundled default is used until something is saved, and
    the saved file after that -- without the checkout's own file being touched."""
    monkeypatch.setattr(core, "_config_dir", lambda: tmp_path)
    assert core.resolve_colors_file() == plan_mod.DEFAULT_COLORS_FILE

    core.save_colors([0x123456, 0x654321])
    assert core.resolve_colors_file() == tmp_path / "channel_colors.yaml"
    assert plan_mod.load_channel_colors(core.resolve_colors_file()) == [0x123456, 0x654321]
    assert plan_mod.load_channel_colors() == DEFAULTS, "the bundled palette was edited"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("#c30e0e", 0xC30E0E),
        ("c30e0e", 0xC30E0E),
        ("  #C30E0E  ", 0xC30E0E),
        ("#c00", 0xCC0000),
        ("195, 14, 14", 0xC30E0E),
        ("195 14 14", 0xC30E0E),
        ("rgb(195, 14, 14)", 0xC30E0E),
        ("RGB(0,0,0)", 0x000000),
    ],
)
def test_editor_takes_a_color_in_the_notation_it_was_copied_in(text, expected):
    assert palette_editor.parse_color(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "#12345", "#c30e0ee", "#gggggg", "300, 0, 0", "1, 2", "1, 2, 3, 4",
     "rgb(1, 2)", "red", "-1, 0, 0"],
)
def test_editor_rejects_what_is_not_a_color(text):
    assert palette_editor.parse_color(text) is None


def test_editor_pads_a_short_palette_but_keeps_a_long_one(tmp_path):
    seed = palette_editor.PaletteEditor._seed
    assert seed([0x111111]) == [0x111111] + DEFAULTS[1:]
    assert seed(DEFAULTS) == DEFAULTS
    longer = DEFAULTS + [0x111111, 0x222222]
    assert seed(longer) == longer
