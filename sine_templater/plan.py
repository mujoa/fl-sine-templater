"""Derive the wiring plan from an input project. Pure: reads, never mutates."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import brso, flp, model, sine, wrapper

SLOTS_PER_INSTANCE = 16           # SINE's 16 MIDI channels
MAX_INSERT = 126                  # FL has 127 inserts, 0 = Master

# Mixer layout for K instances:
#   insert 0            Master
#   inserts 1..K        section buses, one per instance
#   K + 1 ...           the instance blocks, laid end to end in port order
#
# The SINE channel itself sits on its section bus, so a block holds nothing but
# instrument inserts. With reserved slots (the default) it is padded out to all 16
# MIDI channels, so a block is SLOTS_PER_INSTANCE wide and the instrument for MIDI
# channel c always sits at base + c - 1. Highest insert used is then 17K, capping K
# at 7. Compact blocks drop the padding: only the instruments that exist get an
# insert, packed in MIDI-channel order, which is what lifts that cap.
#
# Nothing is routed to SINE's own main bus, but whatever reaches it -- an instrument
# added by hand inside SINE, an audition from its browser -- lands on the section
# bus alongside the summed instruments rather than on an insert of its own.
MAX_INSTANCES = MAX_INSERT // (SLOTS_PER_INSTANCE + 1)

# The channel rack / mixer palette, one 0xRRGGBB color per SINE instance. Kept in
# a YAML file next to this module so colors can be tried out without touching the
# code; flp.encode_color puts the bytes in the order FL wants.
DEFAULT_COLORS_FILE = Path(__file__).with_name("channel_colors.yaml")

# The palette the tool ships with, as (color, name) pairs -- the names are only
# there to become the trailing comments in a written file, so a palette stays
# readable in a text editor. This is the canonical copy: channel_colors.yaml is
# what `write_channel_colors` makes of it, and a test keeps the two in step.
DEFAULT_COLORS: tuple[tuple[str, str], ...] = (
    ("#c30e0e", "red"),
    ("#ffc000", "amber"),
    ("#b45f06", "brown"),
    ("#56b82c", "green"),
    ("#3d85c6", "blue"),
    ("#eeeeee", "near-white"),
    ("#333333", "near-black"),
    ("#7b1ec9", "violet"),
    ("#e87d14", "orange"),
    ("#ed72e3", "pink"),
    ("#1bd69a", "mint"),
    ("#1de4eb", "cyan"),
)

HEX_DIGITS = set("0123456789abcdefABCDEF")


def default_channel_colors() -> list[int]:
    """`DEFAULT_COLORS` as the integers the rest of the code works in."""
    return [int(value[1:], 16) for value, _ in DEFAULT_COLORS]


def load_channel_colors(path: Path | str | None = None) -> list[int]:
    """Read the palette from a colors file; see channel_colors.yaml for the format.

    A `colors:` key holding a list of "#RRGGBB" strings, or a bare top-level list.
    Duplicates are allowed -- two sections sharing a color is a choice, not a
    mistake.

    This reads the small YAML subset the file needs rather than depending on
    PyYAML, which the project would otherwise not require at all. Block lists,
    quoted or bare scalars and `#` comments are understood; anything else is
    reported rather than guessed at, so an unsupported construct can never be
    silently misread as a color.
    """
    path = Path(path) if path is not None else DEFAULT_COLORS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read the colors file {path}: {exc}") from exc

    colors: list[int] = []
    seen_key = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        where = f"{path}:{number}"
        if not line.startswith("-"):
            key = line[:-1].strip() if line.endswith(":") else None
            if key == "colors" and not seen_key:
                seen_key = True
                continue
            raise ValueError(
                f"{where}: expected a color list item like '- \"#c30e0e\"', got {line!r}"
            )
        colors.append(_parse_color(line[1:].strip(), where, raw))

    if not colors:
        raise ValueError(
            f"{path} defines no colors; it needs a 'colors:' list with at least one "
            f'"#RRGGBB" entry'
        )
    return colors


def _strip_comment(line: str) -> str:
    """Drop a trailing YAML comment, leaving '#' inside quotes alone."""
    quote = None
    for position, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (position == 0 or line[position - 1].isspace()):
            return line[:position]
    return line


def _parse_color(token: str, where: str, raw: str) -> int:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        token = token[1:-1].strip()
    if not token:
        # '- #c30e0e' unquoted: YAML reads the color as a comment and leaves the
        # item empty. Easy to write and baffling without this, so name the fix.
        commented_out = _strip_comment(raw) != raw
        raise ValueError(
            f"{where}: no color on this line"
            + ('; an unquoted # starts a YAML comment, so quote it as "#c30e0e"'
               if commented_out else "")
        )
    digits = token[1:] if token.startswith("#") else token
    if len(digits) != 6 or not all(c in HEX_DIGITS for c in digits):
        raise ValueError(
            f"{where}: {token!r} is not a #RRGGBB color (six hex digits, e.g. #c30e0e)"
        )
    return int(digits, 16)


PALETTE_HEADER = """# Channel rack and mixer palette.
#
# Colors are used in section order: the first SINE instance in the rack takes the
# first color, the second takes the second, and so on. Sections past the end of
# the list wrap back to the top, so the list can be any length from one color
# upwards -- add, remove and reorder freely.
#
# A section's color is used for its SINE channel, every BRSO channel generated
# from it, and its whole mixer block (section bus, main out, instrument inserts,
# and any reserved slots).
#
# Keep the quotes: an unquoted # starts a comment in YAML, so "#c30e0e" needs them.
#
# Point the tool at a different file with:  build ... --colors <path>
"""


def dump_channel_colors(colors: Sequence[int]) -> str:
    """Render a palette as the YAML `load_channel_colors` reads back.

    Trailing names are carried over for whichever entries still hold their shipped
    color; a color the user picked has no name to give it, so it gets none.
    """
    named = {int(value[1:], 16): name for value, name in DEFAULT_COLORS}
    lines = [PALETTE_HEADER, "colors:"]
    for color in colors:
        if not 0 <= color <= 0xFFFFFF:
            raise ValueError(f"{color!r} is not a 24-bit RGB value")
        entry = f'  - "#{color:06x}"'
        name = named.get(color)
        lines.append(f"{entry}   # {name}" if name else entry)
    return "\n".join(lines) + "\n"


def write_channel_colors(path: Path | str, colors: Sequence[int]) -> Path:
    """Save a palette, replacing whatever was at `path`.

    Written to a neighboring temporary file and moved into place, so a failure
    part way through leaves the old palette intact rather than a half-written one
    the next run cannot read.
    """
    if not colors:
        raise ValueError("a palette needs at least one color")
    path = Path(path)
    body = dump_channel_colors(colors)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".new")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise ValueError(f"cannot write the colors file {path}: {exc}") from exc
    return path


def channel_color(colors: list[int], port: int) -> int:
    """The palette entry for an instance, cycling once past the end of the list.

    Compact blocks allow well past 12 instances, so a 13th section repeats the first
    section's color rather than running out.
    """
    return colors[port % len(colors)]


# BRSO per-cell color indices, one per grid cell so articulations differ within an
# instrument. Must hold at least brso.MAX_CELLS distinct values; validate.check
# reports it if it ever stops doing so.
ARTICULATION_COLORS = [4, 10, 28, 55, 90, 120, 150, 180, 210, 231, 40, 70, 100, 140, 170, 200]


@dataclass
class ArticulationPlan:
    title: str
    keyswitch: int
    color: int


@dataclass
class InstrumentPlan:
    title: str
    midi_channel: int
    insert: int
    articulations: list[ArticulationPlan]

    @property
    def bus(self) -> int:
        return self.midi_channel


@dataclass
class InstancePlan:
    channel_index: int
    name: str
    port: int
    bus_insert: int                # the section bus this block feeds; the SINE
                                   # channel sits on it, so output offsets are
                                   # counted from here
    bus_name: str            # the section bus insert, '<name> (main)'
    group_name: str          # rack filter group; numbered so sorted order == port order
    base_insert: int               # first insert of the block (MIDI channel 1)
    color: int
    reserve_slots: bool            # pad the block out to all 16 MIDI channels
    instruments: list[InstrumentPlan] = field(default_factory=list)

    @property
    def block_size(self) -> int:
        """Inserts this block occupies: one per slot, or one per instrument."""
        return SLOTS_PER_INSTANCE if self.reserve_slots else len(self.instruments)

    @property
    def block_inserts(self) -> range:
        return range(self.base_insert, self.base_insert + self.block_size)

    @property
    def block_end(self) -> int:
        return self.base_insert + self.block_size - 1

    @property
    def block_range(self) -> str:
        """'7-22'; a compact block with no instruments occupies nothing at all."""
        return f"{self.base_insert}-{self.block_end}" if self.block_size else "(none)"

    def output_offsets(self, outputs: int) -> list[int]:
        """Plugin output N -> insert offset from `bus_insert`, for `outputs` outputs.

        SINE emits an instrument's audio on the output matching its MIDI channel, so
        the table is what ties an instrument to its insert; everything here is
        relative to the SINE channel's own insert, which is the section bus.

        With reserved slots every MIDI channel gets an insert whether an instrument
        sits on it or not, so one added by hand later is already routed. A compact
        block has no insert to spare for that, so only the outputs behind real
        instruments are mapped. Everything else -- output 0, which is SINE's own
        main bus, and in a compact block the empty MIDI channels -- keeps offset 0,
        FL's default for an unrouted output, and so lands on the section bus.
        """
        if self.reserve_slots:
            mapping = {
                channel: self.base_insert + channel - 1
                for channel in range(1, SLOTS_PER_INSTANCE + 1)
            }
        else:
            mapping = {i.midi_channel: i.insert for i in self.instruments}
        return [mapping.get(n, self.bus_insert) - self.bus_insert for n in range(outputs)]


@dataclass
class Plan:
    instances: list[InstancePlan]
    donor_channel: model.ChannelBlock
    donor_tail: bytes
    first_new_channel: int

    @property
    def instruments(self) -> list[tuple[InstancePlan, InstrumentPlan]]:
        return [(inst, i) for inst in self.instances for i in inst.instruments]

    @property
    def highest_insert(self) -> int:
        return max(instance.block_end for instance in self.instances)


def _bus_name(channel_name: str) -> str:
    """The section bus insert's name; the rack filter group keeps the plain name."""
    return f"{channel_name.strip()} (main)"


def derive(
    project: flp.Project, *, compact: bool = False, colors: list[int] | None = None
) -> Plan:
    """Plan the rewiring.

    `compact` gives each instance only the inserts it uses; `colors` overrides the
    palette, which otherwise comes from DEFAULT_COLORS_FILE.
    """
    palette = colors if colors is not None else load_channel_colors()
    blocks = model.channel_blocks(project)
    sine_blocks = [b for b in blocks if model.is_sine(b)]
    brso_blocks = [b for b in blocks if model.is_brso(b)]

    if not sine_blocks:
        raise ValueError("no SINE Player instances found in the project")
    if not brso_blocks:
        raise ValueError(
            "no BRSO Articulate channel found. Add one empty BRSO Articulate as the "
            "last channel of the rack; it is the donor for the generated instances."
        )
    if len(brso_blocks) > 1:
        shown = ", ".join(repr(b.name) for b in brso_blocks[:3])
        more = f" and {len(brso_blocks) - 3} more" if len(brso_blocks) > 3 else ""
        raise ValueError(
            f"expected exactly one BRSO Articulate channel to use as the donor, found "
            f"{len(brso_blocks)} ({shown}{more}). The input should contain only SINE "
            f"instances plus one empty BRSO Articulate as the last channel."
        )

    donor = brso_blocks[0]
    if donor.index != len(blocks) - 1:
        raise ValueError(
            f"the BRSO Articulate donor must be the last channel in the rack "
            f"(it is channel {donor.index + 1} of {len(blocks)})"
        )

    donor_tail = brso.extract_tail(model.plugin_state(project, donor))
    brso.validate_tail(donor_tail)

    count = len(sine_blocks)
    # Reserved blocks have a fixed width, so this is knowable before the (large)
    # plugin states are parsed. Compact blocks are sized in _assign_inserts.
    if not compact and count > MAX_INSTANCES:
        raise ValueError(
            f"{count} SINE instances need mixer inserts up to {count * (SLOTS_PER_INSTANCE + 1)}, "
            f"but FL only has {MAX_INSERT}. Maximum is {MAX_INSTANCES} instances with "
            f"reserved slots; --compact-mixer allocates only the inserts actually used."
        )

    instances = []
    for port, block in enumerate(sine_blocks):
        state = sine.parse(_vst_state(project, block))
        plan = InstancePlan(
            channel_index=block.index,
            name=block.name,
            port=port,
            bus_insert=port + 1,
            bus_name=_bus_name(block.name),
            group_name=f"{port + 1:02d} {block.name.strip()}",
            base_insert=0,       # blocks are placed once every size is known, below
            color=channel_color(palette, port),
            reserve_slots=not compact,
        )
        for instrument in state.instruments:
            channel = int(instrument["midiChannel"])
            if not 1 <= channel <= SLOTS_PER_INSTANCE:
                raise ValueError(
                    f"{block.name!r} / {instrument['title']!r} is on MIDI channel {channel}; "
                    f"only 1..{SLOTS_PER_INSTANCE} are supported"
                )
            arts = [
                ArticulationPlan(
                    title=title,
                    keyswitch=keyswitch,
                    color=ARTICULATION_COLORS[i % len(ARTICULATION_COLORS)],
                )
                for i, (title, keyswitch) in enumerate(sine.articulations(instrument))
            ]
            plan.instruments.append(
                InstrumentPlan(
                    title=instrument["title"],
                    midi_channel=channel,
                    insert=0,                  # assigned in _assign_inserts
                    articulations=arts,
                )
            )
        duplicates = _duplicate_channels(plan)
        if duplicates:
            raise ValueError(
                f"{block.name!r} has more than one instrument on MIDI channel(s) "
                f"{', '.join(map(str, duplicates))}"
            )
        instances.append(plan)

    _assign_inserts(instances)

    return Plan(
        instances=instances,
        donor_channel=donor,
        donor_tail=donor_tail,
        first_new_channel=donor.index,      # donor is dropped, so its index is reused
    )


def _assign_inserts(instances: list[InstancePlan]) -> None:
    """Place the blocks after the section buses and give every instrument its insert.

    Inserts 1..K are the section buses, one per instance, so the first block starts
    at K + 1 and the blocks follow each other with no gap. A block holds instrument
    inserts and nothing else -- at their MIDI channel when slots are reserved,
    otherwise packed in MIDI-channel order so the mixer reads the same way in both
    layouts.
    """
    count = len(instances)
    base = count + 1
    for instance in instances:
        instance.base_insert = base
        if instance.reserve_slots:
            for instrument in instance.instruments:
                instrument.insert = base + instrument.midi_channel - 1
        else:
            ordered = sorted(instance.instruments, key=lambda i: i.midi_channel)
            for slot, instrument in enumerate(ordered):
                instrument.insert = base + slot
        base += instance.block_size

    highest = base - 1
    if highest > MAX_INSERT:
        total = sum(len(i.instruments) for i in instances)
        raise ValueError(
            f"{count} SINE instances with {total} instruments need mixer inserts up to "
            f"{highest}, but FL only has {MAX_INSERT}."
        )


def _vst_state(project: flp.Project, block: model.ChannelBlock) -> bytes:
    _, chunks = wrapper.parse(model.plugin_state(project, block))
    return wrapper.get_chunk(chunks, wrapper.CHUNK_VST_STATE)


def _duplicate_channels(plan: InstancePlan) -> list[int]:
    seen, dupes = set(), set()
    for instrument in plan.instruments:
        if instrument.midi_channel in seen:
            dupes.add(instrument.midi_channel)
        seen.add(instrument.midi_channel)
    return sorted(dupes)


def describe(plan: Plan) -> str:
    lines = []
    for instance in plan.instances:
        lines.append(
            f"{instance.name}  port {instance.port}  "
            f"inserts {instance.block_range} "
            f"-> bus {instance.bus_insert} ({instance.bus_name})"
        )
        for instrument in instance.instruments:
            arts = ", ".join(f"{a.title}={a.keyswitch}" for a in instrument.articulations)
            lines.append(
                f"    ch{instrument.midi_channel:<2} insert {instrument.insert:<4} "
                f"{instrument.title}"
            )
            lines.append(f"         BRSO port {instance.port} / channel "
                         f"{instrument.midi_channel - 1}: {arts or '(no articulations)'}")
        used = {i.midi_channel for i in instance.instruments}
        free = [slot for slot in range(1, SLOTS_PER_INSTANCE + 1) if slot not in used]
        if instance.reserve_slots and free:
            lines.append(
                f"    {len(free)} slot(s) reserved and pre-routed, unnamed "
                f"(SINE MIDI channels {', '.join(map(str, free))})"
            )
    total = len(plan.instruments)
    lines.append(f"\n{total} BRSO channels to generate, replacing the donor at "
                 f"channel index {plan.first_new_channel}")
    layout = "reserved slots" if plan.instances[0].reserve_slots else "compact"
    lines.append(f"mixer layout: {layout}, highest insert used {plan.highest_insert} "
                 f"of {MAX_INSERT}")
    return "\n".join(lines)
