"""Lay the plan out for the window.

`plan.describe` writes the plan as one flat block of text, which is what the
console wants. A window can do better: this module returns the same facts as
(text, tags) segments, so the Text widget can color a keyswitch differently
from an instrument name and show each section's mixer color as a real swatch.
`gui` decides what every tag looks like; nothing here knows about Tk.

Tags used:
    title heading label dim name num ks rule warning
    swatch:#rrggbb   a color chip -- the section's rack/mixer color
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from . import plan as plan_mod

WIDTH = 86                 # the rules, and where wrapped prose folds

Segment = tuple[str, tuple[str, ...]]


class _Out:
    """Accumulates segments; `line` is `add` plus the newline."""

    def __init__(self) -> None:
        self.segments: list[Segment] = []

    def add(self, text: str, *tags: str) -> None:
        if text:
            self.segments.append((text, tags))

    def line(self, text: str = "", *tags: str) -> None:
        self.add(text, *tags)
        self.add("\n")

    def rule(self, char: str = "─", indent: str = "  ") -> None:
        self.line(indent + char * (WIDTH - len(indent)), "rule")

    def field(self, label: str, indent: str = "     ", width: int = 14) -> None:
        """A label column; the caller adds the value and its own newline."""
        self.add(indent + label.ljust(width), "label")


def render(
    plan: plan_mod.Plan,
    *,
    warnings: list[str] | None = None,
    colors_file: Path | str | None = None,
    color_count: int = 0,
) -> list[Segment]:
    out = _Out()
    _summary(out, plan, colors_file, color_count)
    _warnings(out, warnings or [])
    for position, instance in enumerate(plan.instances, start=1):
        _instance(out, instance, position)
    return out.segments


def plain(segments: list[Segment]) -> str:
    """The same layout without the colors -- handy in a terminal or a test."""
    return "".join(text for text, _ in segments)


# ------------------------------------------------------------------ summary


def _summary(
    out: _Out, plan: plan_mod.Plan, colors_file: Path | str | None, color_count: int
) -> None:
    instruments = plan.instruments
    articulations = sum(len(i.articulations) for _, i in instruments)
    lowest = min(instance.bus_insert for instance in plan.instances)

    out.line()
    out.line("  Build plan", "title")
    out.rule("═")
    out.line()

    out.field("Sections")
    out.add(str(len(plan.instances)), "num")
    out.add(_plural(" SINE instance", len(plan.instances)) + "   ·   ", "dim")
    out.add(str(len(instruments)), "num")
    out.add(_plural(" instrument", len(instruments)) + "   ·   ", "dim")
    out.add(str(articulations), "num")
    out.line(_plural(" articulation", articulations), "dim")

    out.field("Mixer layout")
    if plan.instances[0].reserve_slots:
        out.add("reserved slots")
        out.line(
            f"   —   all {plan_mod.SLOTS_PER_INSTANCE} MIDI slots kept per section",
            "dim",
        )
    else:
        out.add("compact")
        out.line("   —   only the inserts each instrument needs", "dim")

    out.field("Inserts")
    out.add(f"{lowest}–{plan.highest_insert}", "num")
    out.add(" of ", "dim")
    out.add(str(plan.max_insert), "num")
    out.line(f"   ({plan.max_insert - plan.highest_insert} still free)", "dim")

    out.field("New channels")
    out.add(str(len(instruments)), "num")
    out.add(_plural(" BRSO channel", len(instruments)))
    out.line(f", replacing the donor at channel index {plan.first_new_channel}", "dim")

    if colors_file is not None:
        out.field("Palette")
        out.add(str(color_count), "num")
        out.add(_plural(" color", color_count) + "   ·   ", "dim")
        out.line(str(colors_file), "dim")
    out.line()


def _warnings(out: _Out, warnings: list[str]) -> None:
    if not warnings:
        return
    out.line(
        f"  ⚠  {len(warnings)} {_plural('warning', len(warnings))}"
        f"   —   the template is still written",
        "warning",
    )
    for warning in warnings:
        lines = wrap(warning, indent=8)
        out.line("     •  " + lines[0], "warning")
        for line in lines[1:]:
            out.line(line, "warning")
    out.line()


# ----------------------------------------------------------------- sections


def _instance(out: _Out, instance: plan_mod.InstancePlan, position: int) -> None:
    swatch = f"#{instance.color:06x}"
    lead = f"  {position}. "
    tail = f"port {instance.port}   ·   {swatch}"

    out.rule()
    out.add("  ")
    out.add("  ", f"swatch:{swatch}")
    out.add(lead, "dim")
    out.add(instance.name, "heading")
    gap = WIDTH - 4 - len(lead) - len(instance.name) - len(tail)
    out.add(" " * max(gap, 2))
    out.line(tail, "dim")
    out.rule()

    out.field("Section bus")
    out.add("insert ")
    out.add(str(instance.bus_insert), "num")
    out.add("   ·   ", "dim")
    out.line(instance.bus_name)

    out.field("Block")
    if instance.block_size:
        out.add("inserts ")
        out.add(f"{instance.base_insert}–{instance.block_end}", "num")
    else:
        out.add("no inserts")
    out.add("   ·   ", "dim")
    out.add(str(len(instance.instruments)), "num")
    out.line(_plural(" instrument", len(instance.instruments)), "dim")

    out.field("Rack group")
    out.line(instance.group_name)
    out.line()

    for instrument in instance.instruments:
        _instrument(out, instance, instrument)

    used = {i.midi_channel for i in instance.instruments}
    free = [s for s in range(1, plan_mod.SLOTS_PER_INSTANCE + 1) if s not in used]
    if instance.reserve_slots and free:
        out.add("     ")
        out.add(str(len(free)), "num")
        out.line(
            f"{_plural(' slot', len(free))} reserved, pre-routed and unnamed"
            f"   —   SINE MIDI channels {_ranges(free)}",
            "dim",
        )
        out.line()


def _instrument(
    out: _Out, instance: plan_mod.InstancePlan, instrument: plan_mod.InstrumentPlan
) -> None:
    out.add("     ch ", "dim")
    out.add(f"{instrument.midi_channel:<2}", "num")
    out.add("   →   insert ", "dim")
    out.add(f"{instrument.insert:<4}", "num")
    out.add("  ")
    out.line(instrument.title, "name")

    count = len(instrument.articulations)
    out.add(" " * 14)
    out.add(
        f"BRSO port {instance.port} / channel {instrument.midi_channel - 1}", "dim"
    )
    if not count:
        out.line("   ·   no articulations", "dim")
        out.line()
        return
    out.add("   ·   ", "dim")
    out.add(str(count), "num")
    out.line(_plural(" articulation", count), "dim")
    _articulations(out, instrument.articulations)
    out.line()


def _articulations(out: _Out, articulations: list[plan_mod.ArticulationPlan]) -> None:
    """The keyswitch table, flowed into as many columns as the width allows.

    One articulation per line reads well but runs to hundreds of lines on a full
    orchestra, and one line per instrument runs off the right edge. Columns sized
    to the longest title keep a section on screen with the notes still lined up.
    Filled left to right, so the reading order stays the order SINE lists them in.
    """
    indent = " " * 14
    note = 4                                   # "999 "
    column = max(len(a.title) for a in articulations) + note + 3
    columns = max(1, min((WIDTH - len(indent)) // column, len(articulations)))

    for start in range(0, len(articulations), columns):
        row = articulations[start:start + columns]
        out.add(indent)
        for position, articulation in enumerate(row, start=1):
            out.add(f"{articulation.keyswitch:>3} ", "ks")
            last = position == len(row)
            out.add(
                articulation.title if last
                else articulation.title.ljust(column - note)
            )
        out.line()


# ------------------------------------------------------------------ helpers


def _plural(word: str, count: int) -> str:
    return word + ("" if count == 1 else "s")


def _ranges(numbers: list[int]) -> str:
    """[3, 4, 5, 7] -> '3–5, 7'. Fourteen slot numbers in a row read as noise."""
    parts: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:] + [None]:
        if number == previous + 1:
            previous = number
            continue
        parts.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = number
    return ", ".join(parts)


def wrap(text: str, *, indent: int) -> list[str]:
    """Fold prose to WIDTH with a hanging indent; the widget never wraps for us."""
    return textwrap.wrap(
        text,
        width=WIDTH - indent,
        subsequent_indent=" " * indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [text]
