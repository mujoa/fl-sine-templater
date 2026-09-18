"""Project metadata: what the tool clears, and what it deliberately leaves alone.

FL stamps event 200 with the registration of the installation that saved the
project. The tool empties it, so a generated template does not carry the
registration of whoever built it. Event 207, the project author, is left as it was
found -- FL does not rewrite that one on save, so a value there was put there on
purpose.

Most of this needs no .flp: the pass is a flat map over an event list.
"""
from __future__ import annotations

import pytest

import make_fixture
import paths
from sine_templater import apply as apply_mod, flp, plan as plan_mod, validate

STAMP = flp.encode_text("gC7;W62vFHDH@>A?")
AUTHOR = 207


def _events() -> list[tuple[int, bytes]]:
    """The head of a project, in the order FL writes it."""
    return [
        (flp.EV_REGNAME, STAMP),
        (194, flp.encode_text("")),                 # title
        (AUTHOR, flp.encode_text("Someone Else")),  # author
        (flp.EV_CHAN_NEW, b"\x00\x00\x00\x00"),
        (flp.EV_PLUGIN_NAME, flp.encode_text("SINE Player")),
    ]


def test_the_stamp_is_emptied():
    out = apply_mod._blank_registration(_events())
    stamps = [p for eid, p in out if eid == flp.EV_REGNAME]
    assert stamps == [flp.encode_text("")]
    assert flp.decode_text(stamps[0]) == ""


def test_the_event_stays_where_it_was():
    """An empty payload, not a deleted event -- FL restores a deleted one anyway."""
    before = _events()
    after = apply_mod._blank_registration(before)
    assert [eid for eid, _ in after] == [eid for eid, _ in before]


def test_nothing_else_is_touched():
    before = _events()
    after = apply_mod._blank_registration(before)
    assert [(e, p) for e, p in after if e != flp.EV_REGNAME] == \
           [(e, p) for e, p in before if e != flp.EV_REGNAME]


def test_the_author_survives():
    out = apply_mod._blank_registration(_events())
    author = [flp.decode_text(p) for eid, p in out if eid == AUTHOR]
    assert author == ["Someone Else"]


def test_blanking_twice_changes_nothing():
    once = apply_mod._blank_registration(_events())
    assert apply_mod._blank_registration(once) == once


def test_a_project_without_the_event_is_left_alone():
    events = [(eid, p) for eid, p in _events() if eid != flp.EV_REGNAME]
    assert apply_mod._blank_registration(events) == events


def test_validate_catches_a_stamp_that_survived():
    project = flp.Project(fmt=0, nch=1, ppq=96, events=_events())
    problems = validate._registration(project)
    assert len(problems) == 1 and "event 200" in problems[0]

    project.events = apply_mod._blank_registration(project.events)
    assert validate._registration(project) == []


@pytest.mark.skipif(
    not make_fixture.SOURCE.is_file(),
    reason=f"{make_fixture.SOURCE} not available; {paths.MISSING}",
)
def test_a_built_template_carries_no_stamp_but_keeps_its_author():
    source = flp.parse(make_fixture.build())
    built = apply_mod.apply(source, plan_mod.derive(source))

    def field(project, eid):
        got = [flp.decode_text(p) for e, p in project.events if e == eid]
        return got[0] if got else None

    assert field(source, flp.EV_REGNAME), "the fixture should carry a stamp to clear"
    assert field(built, flp.EV_REGNAME) == ""
    assert field(built, AUTHOR) == field(source, AUTHOR)
