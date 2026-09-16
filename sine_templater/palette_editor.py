"""The palette as chips you can click: a modal editor over a colors file.

The palette has always been a YAML file, and still is -- `plan.load_channel_colors`
reads it and `plan.write_channel_colors` writes it. This module is only a way to
edit that file without opening a text editor: it holds no colors of its own, it
starts from whatever palette is in effect and hands back a list of colors for the
caller to save.

Two ways to set a color, because neither one suits everybody: click the chip for
the system color picker, or type the value in the box beside it. The box takes
"#c30e0e", a bare "c30e0e", the short "#c00", and RGB as "195, 14, 14" or
"rgb(195, 14, 14)" -- whatever a color was copied from, it is likely one of those.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, font, messagebox, ttk

from . import plan as plan_mod

COLUMNS = 2                 # chips are laid out down one column, then the next
SLOTS = len(plan_mod.DEFAULT_COLORS)

CHIP_WIDTH = 46
CHIP_HEIGHT = 22
BORDER = "#8a8a8a"          # a near-white chip needs an edge to be a chip at all
BORDER_HOVER = "#1a5fb4"
BAD = "#b00020"
NORMAL = "#1f1f1f"

INTRO = (
    "Each SINE section takes the next color in this list. Click a color to pick "
    "a new one, or type a hex or RGB value."
)

BAD_VALUE_HELP = "Use #c30e0e, c30e0e, #c00, or 195, 14, 14."


def parse_color(text: str) -> int | None:
    """A color as typed by a person, or None if it is not one.

    Accepts the notations a color is likely to have been copied from: #rrggbb,
    a bare rrggbb, the short #rgb, and three 0-255 numbers with or without an
    rgb(...) around them.
    """
    token = text.strip()
    if not token:
        return None
    if token.lower().startswith("rgb") and "(" in token:
        token = token[token.index("(") + 1:].rstrip(") ")
    if "," in token or " " in token:
        parts = token.replace(",", " ").split()
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return None
        red, green, blue = (int(part) for part in parts)
        if max(red, green, blue) > 255:
            return None
        return red << 16 | green << 8 | blue
    digits = token[1:] if token.startswith("#") else token
    if len(digits) == 3 and all(c in plan_mod.HEX_DIGITS for c in digits):
        digits = "".join(c * 2 for c in digits)   # "#c00" is "#cc0000"
    if len(digits) != 6 or not all(c in plan_mod.HEX_DIGITS for c in digits):
        return None
    return int(digits, 16)


def format_color(color: int) -> str:
    return f"#{color:06x}"


class _Slot:
    """One position in the palette: a chip, and a box holding the same color.

    The two are kept in step from the box outwards -- the picker writes into the
    box and the chip follows the box -- so a color lives in one place and the
    chip can never disagree with the text beside it.
    """

    def __init__(self, parent: tk.Misc, index: int, color: int, fixed: font.Font) -> None:
        self.number = index + 1
        self.frame = ttk.Frame(parent)
        ttk.Label(self.frame, text=str(self.number), width=3, anchor="e").pack(side="left")

        self.chip = tk.Frame(
            self.frame,
            width=CHIP_WIDTH,
            height=CHIP_HEIGHT,
            background=format_color(color),
            highlightthickness=1,
            highlightbackground=BORDER,
            cursor="hand2",
        )
        self.chip.pack(side="left", padx=(6, 8))
        self.chip.bind("<Button-1>", self._pick)
        self.chip.bind("<Enter>", lambda _e: self.chip.configure(highlightbackground=BORDER_HOVER))
        self.chip.bind("<Leave>", lambda _e: self.chip.configure(highlightbackground=BORDER))

        self.var = tk.StringVar(value=format_color(color))
        self.entry = ttk.Entry(self.frame, textvariable=self.var, width=15, font=fixed)
        self.entry.pack(side="left")
        self.entry.bind("<FocusOut>", self._normalize)
        self.var.trace_add("write", self._typed)

    @property
    def value(self) -> int | None:
        return parse_color(self.var.get())

    def set(self, color: int) -> None:
        self.var.set(format_color(color))

    def _typed(self, *_args: object) -> None:
        """Follow the box as it is typed in, and say so when it is not a color."""
        color = self.value
        if color is None:
            self.entry.configure(foreground=BAD)
            return
        self.entry.configure(foreground=NORMAL)
        self.chip.configure(background=format_color(color))

    def _normalize(self, _event: tk.Event) -> None:
        """Leaving the box rewrites what it holds as #rrggbb, so a palette typed in
        four different notations still reads as one list."""
        color = self.value
        if color is not None:
            self.set(color)

    def _pick(self, _event: tk.Event) -> None:
        current = self.value
        rgb, _hex = colorchooser.askcolor(
            color=format_color(current if current is not None else 0),
            parent=self.frame.winfo_toplevel(),
            title=f"Color {self.number}",
        )
        if rgb is None:
            return
        red, green, blue = (round(channel) for channel in rgb)
        self.set(red << 16 | green << 8 | blue)


class PaletteEditor(tk.Toplevel):
    """Modal. `result` is the chosen palette once it closes, or None if canceled."""

    def __init__(self, master: tk.Misc, colors: list[int], target: Path | str) -> None:
        super().__init__(master)
        self.result: list[int] | None = None
        self.target = Path(target)
        self._slots: list[_Slot] = []

        self.title("Channel colors")
        self.resizable(False, False)
        self._build(self._seed(colors))

        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._save())
        self._center(master)
        self.grab_set()
        self._slots[0].entry.focus_set()
        self.wait_window(self)

    @staticmethod
    def _seed(colors: list[int]) -> list[int]:
        """The palette to start from, never shorter than the shipped twelve.

        A longer one keeps its own length rather than being cut down: a palette
        someone wrote by hand is not this window's to shorten.
        """
        seeded = list(colors)
        defaults = plan_mod.default_channel_colors()
        while len(seeded) < SLOTS:
            seeded.append(defaults[len(seeded) % len(defaults)])
        return seeded

    def _build(self, colors: list[int]) -> None:
        body = ttk.Frame(self, padding=14)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(body, text=INTRO, wraplength=430, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )

        fixed = font.nametofont("TkFixedFont")
        grid = ttk.Frame(body)
        grid.grid(row=1, column=0, sticky="w")
        rows = -(-len(colors) // COLUMNS)   # ceiling: the last column is the short one
        for index, color in enumerate(colors):
            slot = _Slot(grid, index, color, fixed)
            slot.frame.grid(
                row=index % rows, column=index // rows, sticky="w", pady=3, padx=(0, 18)
            )
            self._slots.append(slot)

        ttk.Separator(body).grid(row=2, column=0, sticky="ew", pady=(14, 8))
        ttk.Label(body, text="Saved to", foreground="#6f6f6f").grid(row=3, column=0, sticky="w")
        ttk.Label(body, text=str(self.target), foreground="#6f6f6f").grid(
            row=4, column=0, sticky="w", pady=(0, 12)
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text="Restore defaults", command=self._restore).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(buttons, text="Cancel", command=self._cancel).grid(row=0, column=1)
        ttk.Button(buttons, text="Save", command=self._save).grid(row=0, column=2, padx=(6, 0))

    def _center(self, master: tk.Misc) -> None:
        """Over the parent window, which is where the eye already is."""
        self.update_idletasks()
        top = master.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_width()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _restore(self) -> None:
        defaults = plan_mod.default_channel_colors()
        for index, slot in enumerate(self._slots):
            slot.set(defaults[index % len(defaults)])

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _save(self) -> None:
        colors = [slot.value for slot in self._slots]
        bad = [str(slot.number) for slot, color in zip(self._slots, colors) if color is None]
        if bad:
            messagebox.showwarning(
                "Not a color",
                f"Color {', '.join(bad)} is not a value this understands.\n\n"
                + BAD_VALUE_HELP,
                parent=self,
            )
            return
        self.result = [color for color in colors if color is not None]
        self.destroy()
