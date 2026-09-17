"""Inputs that are legal but awkward, and the failures they used to cause.

Everything here is derived from the same fixture as the end-to-end tests and then
bent in one specific way: two channels given the same name, a donor with no color
of its own, an articulation title FL is happy with and BRSO cannot store, a file
cut in half. Each of these produced either a wrong template or a traceback.
"""
from __future__ import annotations

import json
import struct

import pytest

import make_fixture
import paths
from sine_templater import (
    apply as apply_mod, brso, core, flp, model, plan as plan_mod, sine, validate, wrapper,
)

pytestmark = pytest.mark.skipif(
    not make_fixture.SOURCE.is_file(),
    reason=f"{make_fixture.SOURCE} not available; {paths.MISSING}",
)


def build(project: flp.Project) -> tuple[plan_mod.Plan, bytes]:
    """Derive and apply, the way `core.render` does, without touching the disk."""
    plan = plan_mod.derive(project)
    return plan, flp.serialize(apply_mod.apply(project, plan))


def set_channel_event(project: flp.Project, block, eid: int, payload: bytes) -> None:
    for position in range(block.start, block.end + 1):
        if project.events[position][0] == eid:
            project.events[position] = (eid, payload)
            return
    raise AssertionError(f"channel {block.index} has no event {eid}")


def drop_channel_events(project: flp.Project, block, ids: tuple[int, ...]) -> None:
    project.events[block.span] = [
        event for event in project.events[block.span] if event[0] not in ids
    ]


def channel_event(project: flp.Project, block, eid: int) -> bytes | None:
    for event_id, payload in project.events[block.span]:
        if event_id == eid:
            return payload
    return None


def rewrite_sine_json(project: flp.Project, block, mutate) -> None:
    """Edit a SINE instance's JSON in place, through the codecs that own it."""
    position = next(
        i for i in range(block.start, block.end + 1)
        if project.events[i][0] == flp.EV_PLUGIN_STATE
    )
    kind, chunks = wrapper.parse(project.events[position][1])
    state = sine.parse(wrapper.get_chunk(chunks, wrapper.CHUNK_VST_STATE))
    mutate(state.data)
    chunks = wrapper.set_chunk(chunks, wrapper.CHUNK_VST_STATE, sine.serialize(state))
    project.events[position] = (flp.EV_PLUGIN_STATE, wrapper.serialize(kind, chunks))


@pytest.fixture
def project() -> flp.Project:
    return flp.parse(make_fixture.build())


def sine_blocks(project: flp.Project) -> list[model.ChannelBlock]:
    return [b for b in model.channel_blocks(project) if model.is_sine(b)]


# --- channels that share a display name ------------------------------------

def test_instances_sharing_a_name_validate_against_their_own_channel(project):
    """FL names every SINE Player it adds the same thing.

    Validation used to find the channel by name, so with two the same it checked
    both against the first and reported the second's port, insert and routing as
    wrong -- a refusal to write a template that was in fact correct.
    """
    for block in sine_blocks(project):
        set_channel_event(project, block, flp.EV_PLUGIN_NAME, flp.encode_text("SINE Player"))

    plan, data = build(project)
    assert validate.check(data, plan) == []
    assert [i.channel_index for i in plan.instances] == list(range(len(plan.instances)))


def test_a_sine_channel_in_the_wrong_place_is_reported(project):
    """The index the plan trusts is checked, not assumed."""
    plan, data = build(project)
    plan.instances[1].channel_index = 99
    problems = validate.check(data, plan)
    assert any("does not match the plan" in p for p in problems)


# --- names BRSO cannot store ------------------------------------------------

def test_a_non_ascii_articulation_is_folded_and_reported(project):
    """BRSO's cells are ASCII, and the tool has to agree with itself about that.

    Writing the name with `?` for anything unencodable left the plan expecting
    one string and the file holding another, so the build refused with a message
    that never mentioned the character that caused it.
    """
    first = sine_blocks(project)[0]
    rewrite_sine_json(
        project,
        first,
        lambda data: data["instruments"][0]["articulations"][0].update(
            title="Legato – slow café"
        ),
    )

    plan, data = build(project)
    assert validate.check(data, plan) == []

    articulation = plan.instances[0].instruments[0].articulations[0]
    assert articulation.title == "Legato - slow cafe"
    assert articulation.source_title == "Legato – slow café"

    out = flp.parse(data)
    generated = next(b for b in model.channel_blocks(out) if model.is_brso(b))
    state = brso.parse(model.plugin_state(out, generated))
    assert state.used_cells()[0][2] == "Legato - slow cafe"

    warning = validate.warnings(plan)[0]
    assert "Legato – slow café" in warning and "Legato - slow cafe" in warning


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Legato – slow", "Legato - slow"),
        ("café", "cafe"),
        ("Keys & Plucks", "Keys & Plucks"),
        ("“Con sordino”", '"Con sordino"'),
        ("plain", "plain"),
    ],
)
def test_ascii_name_keeps_what_it_can(value, expected):
    assert brso.ascii_name(value) == expected


def test_write_pascal_refuses_a_name_that_never_went_through_the_fold():
    with pytest.raises(ValueError, match="ascii_name"):
        brso.serialize(
            brso.build(
                keyswitches=[24], names=["Legato – slow"], colors=[4],
                port=0, channel=0, tail=_donor_tail(),
            )
        )


def _donor_tail() -> bytes:
    project = flp.parse(make_fixture.build())
    donor = model.channel_blocks(project)[-1]
    return brso.extract_tail(model.plugin_state(project, donor))


# --- channels with no color or filter group of their own --------------------

def test_a_donor_with_no_color_still_colors_what_it_generates(project):
    """FL writes no event for a channel that was never colored or grouped.

    The generated channels only replaced events the donor already had, so a donor
    straight out of the plugin database produced gray channels, all of them in the
    first section's filter group.
    """
    donor = model.channel_blocks(project)[-1]
    drop_channel_events(project, donor, (flp.EV_CHAN_COLOR, flp.EV_CHAN_GROUP))

    plan, data = build(project)
    assert validate.check(data, plan) == []

    out = flp.parse(data)
    for instance, instrument in plan.instruments:
        block = model.channel_blocks(out)[plan.first_new_channel + _position(plan, instrument)]
        assert flp.decode_color(channel_event(out, block, flp.EV_CHAN_COLOR)) == instance.color
        assert int.from_bytes(channel_event(out, block, flp.EV_CHAN_GROUP), "little") == instance.port


def _position(plan: plan_mod.Plan, instrument) -> int:
    return [i for _, i in plan.instruments].index(instrument)


def test_a_sine_channel_with_no_color_gets_one(project):
    """The same gap on the input side: these blocks are edited, never extended."""
    for block in sine_blocks(project):
        drop_channel_events(project, block, (flp.EV_CHAN_COLOR, flp.EV_CHAN_GROUP))

    plan, data = build(project)
    assert validate.check(data, plan) == []

    out = flp.parse(data)
    blocks = model.channel_blocks(out)
    for instance in plan.instances:
        block = blocks[instance.channel_index]
        assert flp.decode_color(channel_event(out, block, flp.EV_CHAN_COLOR)) == instance.color
        assert int.from_bytes(channel_event(out, block, flp.EV_CHAN_GROUP), "little") == instance.port


def test_a_wrong_channel_color_is_reported(project):
    """The check that keeps the two tests above honest."""
    plan, data = build(project)
    out = flp.parse(data)
    block = model.channel_blocks(out)[plan.instances[1].channel_index]
    set_channel_event(out, block, flp.EV_CHAN_COLOR, flp.encode_color(0x123456))
    problems = validate.check(flp.serialize(out), plan)
    assert any("color is 1193046" in p for p in problems)


# --- a plugin channel that is not SINE --------------------------------------

def test_a_wrapped_plugin_that_is_not_sine_is_named(project):
    """Only the wrapper's name is in the event stream, so any VST gets this far."""
    first = sine_blocks(project)[0]
    position = next(
        i for i in range(first.start, first.end + 1)
        if project.events[i][0] == flp.EV_PLUGIN_STATE
    )
    kind, chunks = wrapper.parse(project.events[position][1])
    chunks = [c for c in chunks if c[0] != wrapper.CHUNK_VST_STATE]
    project.events[position] = (flp.EV_PLUGIN_STATE, wrapper.serialize(kind, chunks))

    with pytest.raises(ValueError, match="not a SINE Player's"):
        plan_mod.derive(project)


# --- files that are not whole -----------------------------------------------

@pytest.mark.parametrize("keep", [6, 20, 0.5])
def test_a_truncated_project_is_a_message_not_a_traceback(tmp_path, keep):
    """`struct.error` and `IndexError` come out of the codecs on a short file.

    Neither is a `BuildError`, and the windowed build catches nothing else -- so
    this reached the user as a window stuck on "Reading the project...".
    """
    raw = make_fixture.build()
    cut = int(len(raw) * keep) if isinstance(keep, float) else keep
    path = tmp_path / "truncated.flp"
    path.write_bytes(raw[:cut])

    with pytest.raises(core.BuildError) as caught:
        core.preview(path)
    assert "is not a project this tool can read" in str(caught.value)
    assert str(caught.value).strip()


def test_a_file_that_is_not_a_project_at_all(tmp_path):
    path = tmp_path / "notes.flp"
    path.write_bytes(b"this is not an FL Studio project")
    with pytest.raises(core.BuildError, match="is not a project this tool can read"):
        core.preview(path)


def test_a_project_that_cannot_be_built_from_keeps_its_own_words(tmp_path, project):
    """A readable project the tool will not build from is a different failure."""
    blocks = model.channel_blocks(project)
    donor = blocks[-1]
    project.events[donor.span] = []
    project.nch -= 1
    path = tmp_path / "no_donor.flp"
    path.write_bytes(flp.serialize(project))

    with pytest.raises(core.BuildError) as caught:
        core.preview(path)
    assert "no BRSO Articulate channel found" in str(caught.value)


# --- SINE state that is not pure ASCII --------------------------------------

def test_split_json_cuts_the_trailer_at_the_right_byte():
    """The decoder counts characters and the container is bytes.

    Adding a character offset to a byte offset left the tail of the JSON in the
    "trailer", which `serialize` then appended again after the rewritten body --
    a plugin state that nothing downstream checks.
    """
    body = json.dumps({"title": "café – far east"}, ensure_ascii=False).encode("utf-8")
    trailer = b"\x00JUCEPrivateData\xff\xfe"
    header = bytearray(sine.VST2_PREAMBLE + bytes(sine.VST2_HEADER_LEN - 4))
    struct.pack_into("<I", header, sine.VST2_OFF_JSON_LEN, len(body) + len(trailer))

    state = sine.parse(bytes(header) + body + trailer)
    assert state.data == {"title": "café – far east"}
    assert state.container.trailer == trailer
    assert sine.parse(sine.serialize(state)).data == state.data
