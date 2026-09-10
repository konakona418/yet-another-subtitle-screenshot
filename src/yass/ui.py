from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .ffprobe import MediaInfo, ProbeError, SubtitleTrack, probe
from .layout import Direction
from .pipeline import (
    GenerationOptions,
    GenerationResult,
    PreviewResult,
    render_layout_preview,
    render_preview,
    run_generation,
)
from .timestamps import TimestampEntry, format_timestamp, parse_timestamp, parse_timestamp_list
from .tools import ToolError

DIRECTIONS = {
    "Horizontal": Direction.HORIZONTAL,
    "Vertical": Direction.VERTICAL,
    "Grid": Direction.GRID,
    "Stack": Direction.STACK,
}


def _pick_family(candidates: tuple[str, ...], available: dict[str, str]) -> str | None:
    for candidate in candidates:
        if candidate.lower() in available:
            return available[candidate.lower()]
    return None

VIDEO_FILETYPES = [
    ("Video files", "*.mkv *.mp4 *.avi *.mov *.ts *.m2ts *.webm *.flv *.wmv"),
    ("All files", "*.*"),
]

SUBTITLE_FILETYPES = [
    ("Subtitle files", "*.srt *.ass *.ssa"),
    ("All files", "*.*"),
]

UI_FONT_CANDIDATES = (
    "Inter",
    "IBM Plex Sans",
    "Adwaita Sans",
    "Noto Sans",
    "Cantarell",
    "Ubuntu",
    "DejaVu Sans",
    "Liberation Sans",
    "Segoe UI",
    "Helvetica",
)

MONO_FONT_CANDIDATES = (
    "JetBrains Mono",
    "IBM Plex Mono",
    "Hack",
    "Cascadia Code",
    "Adwaita Mono",
    "Noto Sans Mono",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Consolas",
    "Menlo",
)


class ImageViewer(ttk.Frame):
    """Canvas-backed image viewer with wheel zoom and drag panning."""

    def __init__(self, master: tk.Misc, image: Image.Image, scale: float = 1.0) -> None:
        super().__init__(master)
        self._image = image
        self._scale = max(0.05, min(8.0, scale))
        self._photo: ImageTk.PhotoImage | None = None

        self.canvas = tk.Canvas(self, highlightthickness=0, background="#2b2b2b")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _event: self._zoom(1.1))
        self.canvas.bind("<Button-5>", lambda _event: self._zoom(1 / 1.1))
        self._render()

    def _on_press(self, event: tk.Event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_wheel(self, event: tk.Event) -> None:
        self._zoom(1.1 if event.delta > 0 else 1 / 1.1)

    def _zoom(self, factor: float) -> None:
        self._scale = max(0.05, min(8.0, self._scale * factor))
        self._render()

    def _render(self) -> None:
        width = max(1, round(self._image.width * self._scale))
        height = max(1, round(self._image.height * self._scale))
        resized = self._image.resize((width, height), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, width, height))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Yet Another Subtitle Screenshot")
        self.minsize(860, 720)

        self.media: MediaInfo | None = None
        self.external_subtitle: Path | None = None
        self._tracks: list[SubtitleTrack] = []
        self._events: queue.Queue[tuple] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_output: Path | None = None
        self._preview_dirs: list[Path] = []

        self.video_path = tk.StringVar()
        self.video_info = tk.StringVar(value="No file loaded.")
        self.track_choice = tk.StringVar(value="None")
        self.subtitle_info = tk.StringVar(value="No subtitles.")
        self.external_info = tk.StringVar(value="No external subtitle loaded.")
        self.direction_choice = tk.StringVar(value="Grid")
        self.columns = tk.IntVar(value=3)
        self.gap = tk.IntVar(value=10)
        self.margin = tk.IntVar(value=0)
        self.strip = tk.IntVar(value=160)
        self.show_labels = tk.BooleanVar(value=False)
        self.bg_color: tuple[int, int, int] = (0, 0, 0)
        self.output_root = tk.StringVar()
        self.format_choice = tk.StringVar(value="PNG")
        self.jpeg_quality = tk.IntVar(value=90)
        self.status = tk.StringVar(value="Ready.")
        self.progress_value = tk.DoubleVar(value=0.0)

        self._configure_theme()
        self._build_ui()
        self._update_layout_state()
        self._update_quality_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._check_tools()

    def _configure_theme(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        available = {name.lower(): name for name in tkfont.families(self)}
        self.ui_family = _pick_family(UI_FONT_CANDIDATES, available)
        mono_family = _pick_family(MONO_FONT_CANDIDATES, available)

        for name, size in (
            ("TkDefaultFont", 10),
            ("TkTextFont", 10),
            ("TkMenuFont", 10),
            ("TkHeadingFont", 10),
            ("TkTooltipFont", 9),
        ):
            font = tkfont.nametofont(name)
            if self.ui_family:
                font.configure(family=self.ui_family, size=size)
            else:
                font.configure(size=size)

        fixed = tkfont.nametofont("TkFixedFont")
        if mono_family:
            fixed.configure(family=mono_family, size=10)

        style.configure("TButton", padding=(10, 5))
        style.configure("TEntry", padding=3)
        style.configure("TSpinbox", padding=3)
        style.configure("TCombobox", padding=3)
        style.configure("Horizontal.TProgressbar", thickness=14)
        if self.ui_family:
            style.configure("TLabelframe.Label", font=(self.ui_family, 10, "bold"))

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        frame.rowconfigure(7, weight=1)

        self._build_video_section(frame, 0)
        self._build_subtitle_section(frame, 1)
        self._build_timestamps_section(frame, 2)
        self._build_layout_section(frame, 3)
        self._build_output_section(frame, 4)
        self._build_actions_section(frame, 5)
        self._build_progress_section(frame, 6)
        self._build_log_section(frame, 7)

    def _build_video_section(self, parent: ttk.Frame, row: int) -> None:
        section = ttk.LabelFrame(parent, text="Video")
        section.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(0, weight=1)
        ttk.Entry(section, textvariable=self.video_path, state="readonly").grid(
            row=0, column=0, sticky="ew", padx=(8, 4), pady=8
        )
        ttk.Button(section, text="Browse...", command=self._browse_video).grid(
            row=0, column=1, padx=(0, 8), pady=8
        )
        ttk.Label(section, textvariable=self.video_info).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8)
        )

    def _build_subtitle_section(self, parent: ttk.Frame, row: int) -> None:
        section = ttk.LabelFrame(parent, text="Subtitle")
        section.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(1, weight=1)
        ttk.Label(section, text="Embedded track:").grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )
        self.track_combo = ttk.Combobox(
            section, textvariable=self.track_choice, state="readonly", values=["None"]
        )
        self.track_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=(8, 4))
        self.track_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_subtitle_info())
        ttk.Button(section, text="Use external file...", command=self._choose_external).grid(
            row=0, column=2, padx=4, pady=(8, 4)
        )
        ttk.Button(section, text="Clear", command=self._clear_external).grid(
            row=0, column=3, padx=(4, 8), pady=(8, 4)
        )
        ttk.Label(section, textvariable=self.subtitle_info).grid(
            row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4)
        )
        ttk.Label(section, textvariable=self.external_info).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8)
        )

    def _build_timestamps_section(self, parent: ttk.Frame, row: int) -> None:
        section = ttk.LabelFrame(parent, text="Timestamps")
        section.grid(row=row, column=0, sticky="nsew", pady=(0, 8))
        section.columnconfigure(0, weight=1)
        section.rowconfigure(0, weight=1)
        self.timestamps_text = tk.Text(section, height=7, wrap="none", undo=True, font="TkFixedFont")
        self.timestamps_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scrollbar = ttk.Scrollbar(section, orient="vertical", command=self.timestamps_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.timestamps_text.configure(yscrollcommand=scrollbar.set)
        ttk.Label(
            section,
            text=(
                "One timestamp per line: HH:MM:SS.mmm, MM:SS or seconds. "
                "Out-of-order input is sorted automatically; duplicates are removed."
            ),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))

    def _build_layout_section(self, parent: ttk.Frame, row: int) -> None:
        section = ttk.LabelFrame(parent, text="Layout")
        section.grid(row=row, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(section, text="Direction:").grid(
            row=0, column=0, sticky="w", padx=(8, 4), pady=(8, 4)
        )
        self.direction_combo = ttk.Combobox(
            section,
            textvariable=self.direction_choice,
            state="readonly",
            values=list(DIRECTIONS),
            width=10,
        )
        self.direction_combo.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=(8, 4))
        self.direction_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_layout_state())

        ttk.Label(section, text="Columns:").grid(
            row=0, column=2, sticky="w", padx=(0, 4), pady=(8, 4)
        )
        self.columns_spin = ttk.Spinbox(
            section, from_=1, to=50, textvariable=self.columns, width=5
        )
        self.columns_spin.grid(row=0, column=3, sticky="w", padx=(0, 12), pady=(8, 4))

        ttk.Label(section, text="Gap (px):").grid(
            row=0, column=4, sticky="w", padx=(0, 4), pady=(8, 4)
        )
        self.gap_spin = ttk.Spinbox(section, from_=0, to=500, textvariable=self.gap, width=5)
        self.gap_spin.grid(row=0, column=5, sticky="w", padx=(0, 12), pady=(8, 4))

        ttk.Label(section, text="Margin (px):").grid(
            row=0, column=6, sticky="w", padx=(0, 4), pady=(8, 4)
        )
        ttk.Spinbox(section, from_=0, to=500, textvariable=self.margin, width=5).grid(
            row=0, column=7, sticky="w", padx=(0, 8), pady=(8, 4)
        )

        ttk.Label(section, text="Background:").grid(
            row=1, column=0, sticky="w", padx=(8, 4), pady=(0, 8)
        )
        self.bg_button = tk.Button(
            section,
            text="#000000",
            command=self._choose_background,
            bg="#000000",
            fg="#ffffff",
            width=9,
            relief="ridge",
        )
        self.bg_button.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 8))

        ttk.Label(section, text="Exposed strip (px):").grid(
            row=1, column=2, sticky="w", padx=(0, 4), pady=(0, 8)
        )
        self.strip_spin = ttk.Spinbox(section, from_=1, to=2000, textvariable=self.strip, width=5)
        self.strip_spin.grid(row=1, column=3, sticky="w", padx=(0, 12), pady=(0, 8))

        ttk.Checkbutton(
            section, text="Show timestamp labels on tiles", variable=self.show_labels
        ).grid(row=1, column=4, columnspan=4, sticky="w", padx=(0, 8), pady=(0, 8))

    def _build_output_section(self, parent: ttk.Frame, row: int) -> None:
        section = ttk.LabelFrame(parent, text="Output")
        section.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(0, weight=1)
        ttk.Entry(section, textvariable=self.output_root).grid(
            row=0, column=0, sticky="ew", padx=(8, 4), pady=8
        )
        ttk.Button(section, text="Browse...", command=self._browse_output).grid(
            row=0, column=1, padx=(0, 8), pady=8
        )

        format_frame = ttk.Frame(section)
        format_frame.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Label(format_frame, text="Format:").pack(side="left")
        self.format_combo = ttk.Combobox(
            format_frame,
            textvariable=self.format_choice,
            state="readonly",
            values=["PNG", "JPEG"],
            width=8,
        )
        self.format_combo.pack(side="left", padx=(4, 16))
        self.format_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_quality_state())
        ttk.Label(format_frame, text="JPEG quality:").pack(side="left")
        self.quality_spin = ttk.Spinbox(
            format_frame, from_=1, to=100, textvariable=self.jpeg_quality, width=5
        )
        self.quality_spin.pack(side="left", padx=(4, 0))

    def _build_actions_section(self, parent: ttk.Frame, row: int) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self.preview_button = ttk.Button(bar, text="Preview frame", command=self._preview)
        self.preview_button.pack(side="left")
        self.preview_layout_button = ttk.Button(
            bar, text="Preview layout", command=self._preview_layout
        )
        self.preview_layout_button.pack(side="left", padx=8)
        self.generate_button = ttk.Button(bar, text="Generate", command=self._generate)
        self.generate_button.pack(side="left")
        self.cancel_button = ttk.Button(bar, text="Cancel", command=self._cancel_run)
        self.cancel_button.pack(side="left", padx=8)
        self.cancel_button.state(["disabled"])
        self.open_button = ttk.Button(bar, text="Open output folder", command=self._open_output)
        self.open_button.pack(side="right")
        self.open_button.state(["disabled"])

    def _build_progress_section(self, parent: ttk.Frame, row: int) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(0, weight=1)
        ttk.Progressbar(bar, maximum=1.0, variable=self.progress_value).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Label(bar, textvariable=self.status).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_log_section(self, parent: ttk.Frame, row: int) -> None:
        section = ttk.LabelFrame(parent, text="Log")
        section.grid(row=row, column=0, sticky="nsew")
        section.columnconfigure(0, weight=1)
        section.rowconfigure(0, weight=1)
        self.log_text = tk.Text(section, height=8, wrap="word", state="disabled", font="TkTextFont")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scrollbar = ttk.Scrollbar(section, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _check_tools(self) -> None:
        missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
        if missing:
            message = (
                f"Missing required tool(s): {', '.join(missing)}. "
                "Install FFmpeg and restart the application."
            )
            self._log(message)
            self.generate_button.state(["disabled"])
            self.preview_button.state(["disabled"])
            messagebox.showerror("FFmpeg not found", message)

    def _browse_video(self) -> None:
        path = filedialog.askopenfilename(title="Choose a video file", filetypes=VIDEO_FILETYPES)
        if not path:
            return
        try:
            media = probe(path)
        except (ProbeError, ToolError) as exc:
            messagebox.showerror("Cannot read video", str(exc))
            return

        self.media = media
        self.video_path.set(path)
        self.external_subtitle = None
        self.external_info.set("No external subtitle loaded.")
        self.video_info.set(
            f"Duration {format_timestamp(media.duration)} | {media.width}x{media.height} | "
            f"{len(media.text_subtitle_tracks)} text subtitle track(s)"
        )
        if not self.output_root.get().strip():
            self.output_root.set(str(Path(path).parent))
        self._populate_tracks()

        self._log(f"Loaded {Path(path).name} ({format_timestamp(media.duration)}, {media.width}x{media.height})")
        for track in media.text_subtitle_tracks:
            self._log(f"  subtitle {track.label}")
        for track in media.bitmap_subtitle_tracks:
            self._log(f"  ignoring bitmap subtitle {track.label} (not supported)")
        if media.font_attachments:
            self._log(
                f"  {len(media.font_attachments)} embedded font attachment(s) will be used for rendering"
            )

    def _populate_tracks(self) -> None:
        self._tracks = list(self.media.text_subtitle_tracks) if self.media else []
        self.track_combo.configure(values=["None"] + [track.label for track in self._tracks])
        default = self.media.default_track() if self.media else None
        self.track_choice.set(default.label if default else "None")
        self._update_subtitle_info()

    def _selected_track(self) -> SubtitleTrack | None:
        choice = self.track_choice.get()
        for track in self._tracks:
            if track.label == choice:
                return track
        return None

    def _update_subtitle_info(self) -> None:
        if self.external_subtitle is not None:
            self.subtitle_info.set(f"Using external file: {self.external_subtitle.name}")
        elif self._selected_track() is not None:
            self.subtitle_info.set(f"Using embedded track: {self._selected_track().label}")
        else:
            self.subtitle_info.set("No subtitles: stills will be rendered without burned-in subtitles.")

    def _choose_external(self) -> None:
        path = filedialog.askopenfilename(title="Choose a subtitle file", filetypes=SUBTITLE_FILETYPES)
        if not path:
            return
        suffix = Path(path).suffix.lower()
        if suffix not in {".srt", ".ass", ".ssa"}:
            messagebox.showerror("Unsupported subtitle", f"Unsupported subtitle format: {suffix or path}")
            return
        self.external_subtitle = Path(path)
        self.external_info.set(f"External subtitle: {path}")
        self._update_subtitle_info()

    def _clear_external(self) -> None:
        self.external_subtitle = None
        self.external_info.set("No external subtitle loaded.")
        self._update_subtitle_info()

    def _choose_background(self) -> None:
        current = "#%02x%02x%02x" % self.bg_color
        chosen = colorchooser.askcolor(color=current, title="Background colour")
        if not chosen or not chosen[0]:
            return
        self.bg_color = tuple(int(round(value)) for value in chosen[0])
        hex_color = "#%02x%02x%02x" % self.bg_color
        luminance = (0.299 * self.bg_color[0] + 0.587 * self.bg_color[1] + 0.114 * self.bg_color[2]) / 255
        self.bg_button.configure(bg=hex_color, text=hex_color, fg="#000000" if luminance > 0.5 else "#ffffff")

    def _update_layout_state(self) -> None:
        direction = DIRECTIONS.get(self.direction_choice.get(), Direction.GRID)
        self.columns_spin.state(["!disabled"] if direction is Direction.GRID else ["disabled"])
        self.gap_spin.state(["disabled"] if direction is Direction.STACK else ["!disabled"])
        self.strip_spin.state(["!disabled"] if direction is Direction.STACK else ["disabled"])

    def _update_quality_state(self) -> None:
        if self.format_choice.get() == "JPEG":
            self.quality_spin.state(["!disabled"])
        else:
            self.quality_spin.state(["disabled"])

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose an output folder")
        if path:
            self.output_root.set(path)

    def _int_from(self, variable: tk.IntVar, fallback: int, minimum: int, maximum: int) -> int:
        try:
            value = int(variable.get())
        except (tk.TclError, ValueError):
            return fallback
        return max(minimum, min(maximum, value))

    def _build_options(self, entries: list[TimestampEntry]) -> GenerationOptions:
        if self.media is None:
            raise RuntimeError("no video loaded")
        return GenerationOptions(
            video=Path(self.video_path.get()),
            media=self.media,
            entries=entries,
            output_root=Path(self.output_root.get()).expanduser(),
            subtitle_track=None if self.external_subtitle else self._selected_track(),
            external_subtitle=self.external_subtitle,
            direction=DIRECTIONS.get(self.direction_choice.get(), Direction.GRID),
            columns=self._int_from(self.columns, 3, 1, 200),
            gap=self._int_from(self.gap, 10, 0, 5000),
            margin=self._int_from(self.margin, 0, 0, 5000),
            strip=self._int_from(self.strip, 160, 1, 2000),
            background=self.bg_color,
            show_labels=self.show_labels.get(),
            image_format="jpg" if self.format_choice.get() == "JPEG" else "png",
            jpeg_quality=self._int_from(self.jpeg_quality, 90, 1, 100),
        )

    def _generate(self) -> None:
        if self.media is None:
            messagebox.showwarning("No video", "Load a video file first.")
            return
        if not self.output_root.get().strip():
            messagebox.showwarning("No output folder", "Choose an output folder.")
            return

        parsed = parse_timestamp_list(
            self.timestamps_text.get("1.0", "end"), duration=self.media.duration
        )
        if parsed.errors:
            detail = "\n".join(parsed.errors[:10])
            if len(parsed.errors) > 10:
                detail += f"\n... and {len(parsed.errors) - 10} more"
            messagebox.showerror("Invalid timestamps", detail)
            return

        entries = [entry for entry in parsed.entries if entry.in_range]
        if not entries:
            messagebox.showwarning("No timestamps", "Enter at least one valid timestamp.")
            return

        for warning in parsed.warnings:
            self._log(f"Warning: {warning}")

        try:
            options = self._build_options(entries)
            options.output_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output folder", f"Could not create the output folder: {exc}")
            return

        self._set_running(True)
        self._cancel.clear()
        self._worker = threading.Thread(target=self._run_worker, args=(options,), daemon=True)
        self._worker.start()
        self.after(100, self._poll_events)

    def _run_worker(self, options: GenerationOptions) -> None:
        def progress(done: int, total: int, message: str) -> None:
            self._events.put(("progress", done, total, message))

        try:
            result = run_generation(options, progress=progress, cancel=self._cancel)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self._events.put(("error", exc))
        else:
            self._events.put(("done", result))

    def _poll_events(self) -> None:
        if not self.winfo_exists():
            return
        finished = False
        try:
            while True:
                event = self._events.get_nowait()
                if event[0] == "progress":
                    _, done, total, message = event
                    self.progress_value.set(done / total if total else 0.0)
                    self.status.set(message)
                elif event[0] == "done":
                    self._finish(event[1])
                    finished = True
                    break
                elif event[0] == "preview_done":
                    self._finish_preview(event[1])
                    finished = True
                    break
                elif event[0] == "error":
                    self._set_running(False)
                    self.status.set("Failed.")
                    self._log(f"Error: {event[1]}")
                    messagebox.showerror("Generation failed", str(event[1]))
                    finished = True
                    break
        except queue.Empty:
            pass
        if not finished:
            self.after(100, self._poll_events)

    def _finish(self, result: GenerationResult) -> None:
        self._set_running(False)
        self._last_output = result.output_dir
        self.open_button.state(["!disabled"])
        for warning in result.warnings:
            self._log(f"Warning: {warning}")
        for error in result.errors:
            self._log(f"Error: {error}")
        if result.cancelled:
            self.status.set("Cancelled.")
            self._log(f"Cancelled. Partial output in {result.output_dir}")
        else:
            sheet = result.sheet_path.name if result.sheet_path else "none"
            self.status.set(f"Done: {len(result.still_paths)} still(s), contact sheet: {sheet}")
            self._log(f"Output folder: {result.output_dir}")
        if result.errors and not result.still_paths:
            messagebox.showerror("Generation failed", "\n".join(result.errors[:10]))

    def _set_running(self, running: bool) -> None:
        buttons = (self.generate_button, self.preview_button, self.preview_layout_button)
        if running:
            for button in buttons:
                button.state(["disabled"])
            self.cancel_button.state(["!disabled"])
            self.progress_value.set(0.0)
        else:
            for button in buttons:
                button.state(["!disabled"])
            self.cancel_button.state(["disabled"])

    def _cancel_run(self) -> None:
        self._cancel.set()
        self.status.set("Cancelling...")

    def _open_output(self) -> None:
        if self._last_output is None or not self._last_output.exists():
            return
        path = str(self._last_output)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            messagebox.showerror("Open folder", str(exc))

    def _preview(self) -> None:
        if self.media is None:
            messagebox.showwarning("No video", "Load a video file first.")
            return
        line = self.timestamps_text.get("insert linestart", "insert lineend").strip()
        if not line:
            messagebox.showerror(
                "No timestamp",
                "The line with the caret is empty. Put the caret on a timestamp line and try again.",
            )
            return
        try:
            seconds = parse_timestamp(line)
        except ValueError:
            messagebox.showerror("Invalid timestamp", f"Cannot parse {line!r}.")
            return
        if seconds >= self.media.duration:
            messagebox.showerror(
                "Out of range",
                f"{line} is beyond the video duration ({format_timestamp(self.media.duration)}).",
            )
            return

        entry = TimestampEntry(seconds=seconds, raw=line, line=1)
        try:
            options = self._build_options([entry])
        except RuntimeError as exc:
            messagebox.showerror("Preview", str(exc))
            return

        self.status.set(f"Rendering preview at {format_timestamp(seconds)}...")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            path = render_preview(options)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.status.set("Preview failed.")
            messagebox.showerror("Preview failed", str(exc))
            return
        finally:
            self.config(cursor="")
        self._preview_dirs.append(path.parent)
        self._show_image_window(path, f"Preview {format_timestamp(seconds)}")
        self.status.set("Ready.")

    def _preview_layout(self) -> None:
        if self.media is None:
            messagebox.showwarning("No video", "Load a video file first.")
            return
        parsed = parse_timestamp_list(
            self.timestamps_text.get("1.0", "end"), duration=self.media.duration
        )
        if parsed.errors:
            detail = "\n".join(parsed.errors[:10])
            if len(parsed.errors) > 10:
                detail += f"\n... and {len(parsed.errors) - 10} more"
            messagebox.showerror("Invalid timestamps", detail)
            return
        entries = [entry for entry in parsed.entries if entry.in_range]
        if not entries:
            messagebox.showwarning("No timestamps", "Enter at least one valid timestamp.")
            return
        for warning in parsed.warnings:
            self._log(f"Warning: {warning}")
        try:
            options = self._build_options(entries)
        except RuntimeError as exc:
            messagebox.showerror("Preview", str(exc))
            return

        self._set_running(True)
        self._cancel.clear()
        self.status.set("Rendering layout preview...")
        self._worker = threading.Thread(
            target=self._run_layout_worker, args=(options,), daemon=True
        )
        self._worker.start()
        self.after(100, self._poll_events)

    def _run_layout_worker(self, options: GenerationOptions) -> None:
        def progress(done: int, total: int, message: str) -> None:
            self._events.put(("progress", done, total, message))

        try:
            result = render_layout_preview(options, progress=progress, cancel=self._cancel)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self._events.put(("error", exc))
        else:
            self._events.put(("preview_done", result))

    def _finish_preview(self, result: PreviewResult) -> None:
        self._set_running(False)
        for warning in result.warnings:
            self._log(f"Warning: {warning}")
        for error in result.errors:
            self._log(f"Error: {error}")
        self._preview_dirs.append(result.sheet_path.parent)
        self._show_image_window(result.sheet_path, "Layout preview")
        self.status.set("Ready.")

    def _show_image_window(self, path: Path, title: str) -> None:
        try:
            image = Image.open(path)
            image.load()
        except OSError as exc:
            messagebox.showerror(title, f"Could not open the preview: {exc}")
            return

        window = tk.Toplevel(self)
        window.title(f"{title} - {path.name}")
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        scale = min(1.0, (screen_w * 0.9) / image.width, (screen_h * 0.8) / image.height)
        width = min(screen_w, int(image.width * scale) + 40)
        height = min(screen_h, int(image.height * scale) + 80)
        window.geometry(f"{max(320, width)}x{max(240, height)}")
        viewer = ImageViewer(window, image, scale)
        viewer.pack(fill="both", expand=True)
        ttk.Label(window, text=str(path)).pack(padx=8, pady=(0, 8))

    def _on_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno("Quit", "A generation is running. Cancel it and quit?"):
                return
            self._cancel.set()
        for directory in self._preview_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()
