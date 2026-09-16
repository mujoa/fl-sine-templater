"""Tkinter front end.

A second front end over `core`, for people who will not open a terminal. The build
itself is the same three calls in the same order, with the plan shown before
anything is written.

Three deliberate differences from the CLI:

* The plan is never hidden. `--dry-run` is a flag on the command line; here the
  plan and its warnings appear as soon as a project is chosen, and writing needs
  a second, separate click.
* An existing output is a question rather than a refusal. The CLI cannot ask, so
  it demands `--force`; a window can just ask. Writing over the *input* stays a
  hard error in both.
* The palette is edited here (`palette_editor`) rather than pointed at. The window
  has no path box for it: there is one saved palette, "Set colors…" edits it, and
  that is the whole of it. The CLI keeps `--colors <file>` for a one-off palette,
  which is the shape that suits a script and not a window.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

from . import __version__, core, palette_editor, plan as plan_mod, report

FILETYPES = [("FL Studio project", "*.flp"), ("All files", "*.*")]

COMPACT_LABEL = "Compact mixer (only the inserts each instrument uses)"

# What the tool was built and tested against, read out of the reference project
# rather than assumed: FL Studio writes its own version into the .flp, SINE
# records `samplerVersion` in its state, and BRSO's state carries a format
# version and a settings block whose length identifies the release.
#
# Deliberately "tested against" and not "required": nothing here is enforced.
# The tool reads what a project actually contains -- BRSO settings are located by
# name so a release that adds one still works, and both SINE plugin formats are
# handled -- so a version not listed here is untested, not refused.
TESTED_AGAINST = (
    ("FL Studio", "21.1.1 and 2026 (26.1.6)"),
    ("SINE Player", "1.3.0  (VST2 and VST3)"),
    ("BRSO Articulate", "1.17 and 1.33"),
)

ABOUT_NOTE = (
    "Other versions are untested rather than unsupported: the tool reads what "
    "the project actually contains instead of checking version numbers."
)

# What Text.insert takes: one tag, several, or none.
Tags = str | tuple[str, ...] | None


def _icon_file() -> Path | None:
    """The .ico on disk, or None.

    The executable carries the icon as a PE resource, which is what Explorer and
    the taskbar read -- but Tk does not inherit it, and `iconbitmap` wants a real
    path, so the spec bundles the file as data as well. Absent from a source
    checkout without a built icon, hence the fallback to None.
    """
    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "sine_templater.ico")
    candidates.append(Path(__file__).resolve().parent.parent / "packaging" / "sine_templater.ico")
    return next((path for path in candidates if path.is_file()), None)


def _apply_icon(root: tk.Tk) -> None:
    """`default=` so dialogs inherit it too. A missing or unreadable icon is not
    worth failing a launch over."""
    icon = _icon_file()
    if icon is None:
        return
    try:
        root.iconbitmap(default=str(icon))
    except tk.TclError:
        pass


def _enable_dpi_awareness() -> None:
    """Without this the window is blurry on a scaled Windows display."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - a blurry window is not worth failing over
        pass


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.preview: core.Preview | None = None
        self._swatches: set[str] = set()   # swatch tags already configured

        self.input_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.folder_var = tk.StringVar()
        self.compact_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Choose a SINE project to begin.")

        self._build_widgets()
        self.grid(row=0, column=0, sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

    # ---------------------------------------------------------------- layout

    def _build_widgets(self) -> None:
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="SINE project").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(self, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Browse…", command=self._choose_input).grid(row=0, column=2)

        ttk.Label(self, text="Save as").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(self, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", padx=6)

        ttk.Label(self, text="Folder").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(self, textvariable=self.folder_var).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Browse…", command=self._choose_folder).grid(row=2, column=2)

        ttk.Checkbutton(
            self, text=COMPACT_LABEL, variable=self.compact_var, command=self._refresh
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Button(self, text="Set colors…", command=self._edit_palette).grid(
            row=3, column=2, sticky="e", pady=(8, 2)
        )

        ttk.Separator(self).grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)

        pane = ttk.Frame(self)
        pane.grid(row=5, column=0, columnspan=3, sticky="nsew")
        pane.rowconfigure(0, weight=1)
        pane.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

        fixed = font.nametofont("TkFixedFont")
        self.text = tk.Text(
            pane,
            wrap="none",
            height=20,
            width=94,
            font=fixed,
            background="#ffffff",
            foreground="#1f1f1f",
            padx=10,
            pady=6,
            relief="solid",
            borderwidth=1,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        self.text.configure(state="disabled")
        self._configure_tags(fixed)
        vbar = ttk.Scrollbar(pane, orient="vertical", command=self.text.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttk.Scrollbar(pane, orient="horizontal", command=self.text.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        footer = ttk.Frame(self)
        footer.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        # Beside the one button that matters, but not competing with it: see
        # `_quiet_button_style`.
        ttk.Button(
            footer, text="About", style=self._quiet_button_style(), command=self._show_about
        ).grid(row=0, column=1, sticky="e", padx=(0, 6))
        self.build_button = ttk.Button(
            footer, text="Build template", command=self._build, state="disabled"
        )
        self.build_button.grid(row=0, column=2, sticky="e")

    @staticmethod
    def _quiet_button_style() -> str:
        """A borderless button style, for an action that should not read as one of
        the real ones.

        `Toolbutton` is ttk's own answer to this and is what a toolbar icon uses:
        flat at rest, with the frame appearing only under the pointer. Deriving
        from it rather than flattening `TButton` keeps that native behavior --
        the style name has to end in `Toolbutton` for ttk to inherit its layout.

        A plain Label styled to look clickable would be quieter still, but it
        would stop being a button: no keyboard focus, no space-to-press, nothing
        for a screen reader to announce.
        """
        style = ttk.Style()
        style.configure("Quiet.Toolbutton", foreground="#6f6f6f", padding=(8, 2))
        return "Quiet.Toolbutton"

    # ----------------------------------------------------------- text output

    def _configure_tags(self, fixed: font.Font) -> None:
        """What `report`'s tags look like. Colors are picked to stay legible on
        white and to separate the three things worth picking out at a glance: a
        name, a mixer number, a keyswitch note."""
        # Bold, not bigger: `report` right-aligns against a character count, which
        # only lands on the rule if every tag on the line keeps the fixed width.
        # The title is on a line of its own, so it can grow.
        heading = fixed.copy()
        heading.configure(weight="bold")
        title = fixed.copy()
        title.configure(weight="bold", size=abs(fixed.cget("size")) + 3)
        # Kept on the instance: Tk does not own a Font object, and a collected
        # one takes the tag's styling with it.
        self._fonts = (heading, title)

        self.text.tag_configure("title", font=title, foreground="#111111", spacing3=2)
        self.text.tag_configure("heading", font=heading, foreground="#111111")
        self.text.tag_configure("name", font=heading, foreground="#1f1f1f")
        self.text.tag_configure("label", foreground="#6f6f6f")
        self.text.tag_configure("dim", foreground="#8a8a8a")
        self.text.tag_configure("rule", foreground="#c4c4c4")
        self.text.tag_configure("num", foreground="#1a5fb4")
        self.text.tag_configure("ks", foreground="#7a3fa8")
        self.text.tag_configure("warning", foreground="#a05a00")
        self.text.tag_configure("error", foreground="#b00020")
        self.text.tag_configure("good", foreground="#006400")

    def _swatch(self, tag: str) -> None:
        """A `swatch:#rrggbb` tag, made the first time that color is shown.

        The border matters: a near-white section color is otherwise an invisible
        chip on a white page.
        """
        if tag in self._swatches:
            return
        self._swatches.add(tag)
        self.text.tag_configure(
            tag, background=tag.split(":", 1)[1], borderwidth=1, relief="solid"
        )

    def _show_plan(self, segments: list[report.Segment]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for body, tags in segments:
            for tag in tags:
                if tag.startswith("swatch:"):
                    self._swatch(tag)
            self.text.insert("end", body, tags)
        self.text.configure(state="disabled")

    def _show_failure(self, exc: core.BuildError, heading: str, *, keep: bool = False) -> None:
        """A failure laid out like the plan is: a heading, then the message folded
        to the width, then one bullet per itemised problem. The widget does not
        wrap, so a long message would otherwise run off the right edge."""
        if not keep:
            self._show("")
        self._append("\n  " + heading + "\n", ("heading", "error"))
        self._append("  " + "─" * (report.WIDTH - 2) + "\n", "rule")
        for line in report.wrap(str(exc), indent=5):
            self._append("  " + line + "\n", "error")
        for problem in exc.problems:
            lines = report.wrap(problem, indent=8)
            self._append("     •  " + lines[0] + "\n", "error")
            for line in lines[1:]:
                self._append(line + "\n", "error")

    def _show(self, body: str, tag: Tags = None) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", body, tag or ())
        self.text.configure(state="disabled")

    def _append(self, body: str, tag: Tags = None) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", body, tag or ())
        self.text.see("end")
        self.text.configure(state="disabled")

    # -------------------------------------------------------------- pickers

    def _choose_input(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose a SINE project", filetypes=FILETYPES)
        if not chosen:
            return
        self.input_var.set(chosen)
        path = Path(chosen)
        self.folder_var.set(str(path.parent))
        self.name_var.set(core.default_output_name(path))
        self._refresh()

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(
            title="Where should the template go?", initialdir=self.folder_var.get() or None
        )
        if chosen:
            self.folder_var.set(chosen)

    def _edit_palette(self) -> None:
        """Edit the palette in use and save it back.

        Read from wherever `core.resolve_colors_file` found it, written where
        `core.user_colors_file` keeps it. The two differ only on a first run from
        a checkout, where the palette being read is the bundled default and
        saving must not edit it.
        """
        source = core.resolve_colors_file()
        target = core.user_colors_file()
        try:
            colors = plan_mod.load_channel_colors(source)
        except ValueError as exc:
            # An unreadable palette is a reason to offer a way out, not a dead end:
            # starting from the defaults is exactly how it gets fixed.
            if not messagebox.askyesno(
                "That palette cannot be read",
                f"{exc}\n\nStart from the default colors instead?",
            ):
                return
            colors = plan_mod.default_channel_colors()

        editor = palette_editor.PaletteEditor(self, colors, target)
        if editor.result is None:
            return
        try:
            saved = core.save_colors(editor.result, target)
        except core.BuildError as exc:
            messagebox.showerror("Colors not saved", str(exc))
            return
        self._refresh()   # the plan carries the colors, so it has to be re-shown
        self._idle(f"Colors saved to {saved}")

    def _show_about(self) -> None:
        """The version, and what it was tested against.

        A window of its own rather than a messagebox: the compatibility list only
        lines up in a fixed font, and a system dialog does not give one.
        """
        window = tk.Toplevel(self)
        window.title("About SINE Templater")
        window.resizable(False, False)
        window.transient(self.winfo_toplevel())

        body = ttk.Frame(window, padding=16)
        body.grid(row=0, column=0, sticky="nsew")

        # Kept on the instance: Tk does not own a Font object, and a collected one
        # takes the label's styling with it.
        self._about_font = font.nametofont("TkDefaultFont").copy()
        self._about_font.configure(
            size=abs(self._about_font.cget("size")) + 4, weight="bold"
        )
        ttk.Label(body, text="SINE Templater", font=self._about_font).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(body, text=f"Version {__version__}", foreground="#6f6f6f").grid(
            row=1, column=0, sticky="w", pady=(2, 10)
        )
        ttk.Label(
            body,
            text="Wires SINE Player instruments to BRSO Articulate and the FL mixer.",
            wraplength=360,
            justify="left",
        ).grid(row=2, column=0, sticky="w")

        ttk.Separator(body).grid(row=3, column=0, sticky="ew", pady=12)
        ttk.Label(body, text="Built and tested against").grid(row=4, column=0, sticky="w")

        fixed = font.nametofont("TkFixedFont")
        table = ttk.Frame(body)
        table.grid(row=5, column=0, sticky="w", pady=(6, 10))
        width = max(len(name) for name, _ in TESTED_AGAINST)
        for row, (name, value) in enumerate(TESTED_AGAINST):
            ttk.Label(table, text=name.ljust(width), font=fixed).grid(
                row=row, column=0, sticky="w"
            )
            ttk.Label(table, text=value, font=fixed, foreground="#1a5fb4").grid(
                row=row, column=1, sticky="w", padx=(12, 0)
            )

        ttk.Label(
            body, text=ABOUT_NOTE, wraplength=360, justify="left", foreground="#6f6f6f"
        ).grid(row=6, column=0, sticky="w")

        close = ttk.Button(body, text="Close", command=window.destroy)
        close.grid(row=7, column=0, sticky="e", pady=(14, 0))
        window.bind("<Escape>", lambda _e: window.destroy())
        window.bind("<Return>", lambda _e: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        palette_editor.center_over(window, self)
        window.grab_set()
        close.focus_set()

    # -------------------------------------------------------------- pipeline

    def _output_path(self) -> Path | None:
        name = self.name_var.get().strip()
        folder = self.folder_var.get().strip()
        if not name or not folder:
            return None
        if not name.lower().endswith(".flp"):
            name += ".flp"
        return Path(folder) / name

    def _busy(self, message: str) -> None:
        self.status_var.set(message)
        self.master.configure(cursor="watch")
        self.update_idletasks()

    def _idle(self, message: str) -> None:
        self.status_var.set(message)
        self.master.configure(cursor="")

    def _refresh(self) -> None:
        """Re-derive the plan and show it. Reads only -- nothing is written."""
        source = self.input_var.get().strip()
        if not source:
            return
        self.preview = None
        self.build_button.configure(state="disabled")
        self._busy("Reading the project…")
        try:
            preview = core.preview(source, compact=self.compact_var.get())
        except core.BuildError as exc:
            self._show_failure(exc, "This project cannot be used as it stands")
            self._idle("The project cannot be used as it stands.")
            return

        self.preview = preview
        warnings = preview.warnings
        self._show_plan(
            report.render(
                preview.plan,
                warnings=warnings,
                colors_file=preview.colors_file,
                color_count=preview.color_count,
            )
        )
        self.text.yview_moveto(0.0)  # a fresh plan reads from the top
        self.build_button.configure(state="normal")
        count = len(preview.plan.instruments)
        noted = f", {len(warnings)} warning(s)" if warnings else ""
        self._idle(f"Ready: {count} BRSO channel(s) to generate{noted}.")

    def _build(self) -> None:
        if self.preview is None:
            return
        output = self._output_path()
        if output is None:
            messagebox.showwarning(
                "Nowhere to save", "Choose a folder and give the template a name first."
            )
            return

        force = False
        try:
            core.check_output(output, self.preview.input_path, force=False)
        except core.BuildError as exc:
            if not output.exists():
                messagebox.showerror("Cannot save there", str(exc))
                return
            if not messagebox.askyesno(
                "Replace it?", f"{output.name} already exists in that folder.\n\nReplace it?"
            ):
                self._idle("Nothing written.")
                return
            force = True
            try:
                core.check_output(output, self.preview.input_path, force=True)
            except core.BuildError as second:
                messagebox.showerror("Cannot save there", str(second))
                return

        self._busy("Building…")
        self.build_button.configure(state="disabled")
        try:
            data = core.render(self.preview)
            core.write(data, output, self.preview.input_path, force=force)
        except core.BuildError as exc:
            self._show_failure(exc, "Not written", keep=True)
            self._idle("Nothing was written.")
            self.build_button.configure(state="normal")
            messagebox.showerror("Not written", str(exc))
            return

        self._append("\n  ✔  Written\n", "good")
        self._append(f"     {output}\n     {len(data):,} bytes\n", "dim")
        self._idle(f"Written to {output}")
        self.build_button.configure(state="normal")


def main(input_path: Path | str | None = None, *, compact: bool = False) -> int:
    _enable_dpi_awareness()
    root = tk.Tk()
    root.title(f"SINE Templater {__version__}")
    _apply_icon(root)
    root.minsize(760, 560)
    app = App(root)
    if compact:
        app.compact_var.set(True)
    if input_path:
        # Resolved so a relative path -- from the command line, or a drop onto the
        # executable -- still gives the Folder box somewhere real to start from.
        path = Path(input_path).resolve()
        app.input_var.set(str(path))
        app.folder_var.set(str(path.parent))
        app.name_var.set(core.default_output_name(path))
        app.after(50, app._refresh)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
