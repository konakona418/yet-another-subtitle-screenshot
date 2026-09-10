from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image

from .extract import ExtractionError, extract_frame
from .ffprobe import MediaInfo, SubtitleTrack
from .layout import Direction, LayoutSpec, compose
from .subs import PreparedSubtitle, SubtitleError, extract_fonts, prepare_subtitle
from .timestamps import TimestampEntry, format_filename, format_timestamp
from .tools import ToolError

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class GenerationOptions:
    video: Path
    media: MediaInfo
    entries: list[TimestampEntry]
    output_root: Path
    subtitle_track: SubtitleTrack | None = None
    external_subtitle: Path | None = None
    direction: Direction = Direction.GRID
    columns: int = 3
    gap: int = 10
    margin: int = 0
    background: tuple[int, int, int] = (0, 0, 0)
    show_labels: bool = False
    strip: int = 160
    image_format: str = "png"
    jpeg_quality: int = 90


@dataclass
class GenerationResult:
    output_dir: Path
    still_paths: list[Path] = field(default_factory=list)
    sheet_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


@dataclass
class PreviewResult:
    sheet_path: Path
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_generation(
    options: GenerationOptions,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> GenerationResult:
    cancel = cancel or threading.Event()
    output_dir = _create_run_dir(options.output_root, options.video.stem)
    result = GenerationResult(output_dir=output_dir)
    tmpdir = Path(tempfile.mkdtemp(prefix="yass-"))
    try:
        try:
            prepared, fonts_dir = _prepare_assets(options, tmpdir, result)
        except (SubtitleError, ToolError) as exc:
            result.errors.append(str(exc))
            return result

        entries = _in_range_entries(options)
        rendered = _render_frames(
            options, entries, output_dir, prepared, fonts_dir, progress, cancel, result
        )
        result.still_paths = [path for _, path in rendered]

        if cancel.is_set():
            result.cancelled = True
        elif rendered:
            result.sheet_path = _build_contact_sheet(options, rendered, output_dir)

        if progress and not result.cancelled:
            progress(len(entries), len(entries), "Done")
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def render_layout_preview(
    options: GenerationOptions,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> PreviewResult:
    """Render every timestamp into a temporary directory and compose the sheet.

    The caller owns ``result.sheet_path.parent`` and should remove it once the
    preview is no longer needed.
    """
    cancel = cancel or threading.Event()
    entries = _in_range_entries(options)
    if not entries:
        raise ValueError("no timestamps to preview")

    tmpdir = Path(tempfile.mkdtemp(prefix="yass-preview-"))
    try:
        result = GenerationResult(output_dir=tmpdir)
        try:
            prepared, fonts_dir = _prepare_assets(options, tmpdir, result)
        except (SubtitleError, ToolError) as exc:
            raise PreviewError(str(exc)) from exc

        preview_options = replace(options, image_format="png")
        rendered = _render_frames(
            preview_options, entries, tmpdir, prepared, fonts_dir, progress, cancel, result
        )
        if not rendered:
            raise PreviewError("; ".join(result.errors) or "nothing could be rendered")

        sheet_path = _build_contact_sheet(preview_options, rendered, tmpdir)
        return PreviewResult(
            sheet_path=sheet_path,
            warnings=result.warnings,
            errors=result.errors,
        )
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


class PreviewError(RuntimeError):
    """Raised when a layout preview cannot be rendered."""


def _in_range_entries(options: GenerationOptions) -> list[TimestampEntry]:
    return [entry for entry in options.entries if entry.in_range]


def _render_frames(
    options: GenerationOptions,
    entries: list[TimestampEntry],
    output_dir: Path,
    prepared: PreparedSubtitle | None,
    fonts_dir: Path | None,
    progress: ProgressCallback | None,
    cancel: threading.Event,
    result: GenerationResult,
) -> list[tuple[TimestampEntry, Path]]:
    total = len(entries)
    rendered: list[tuple[TimestampEntry, Path]] = []
    extension = _extension(options)

    for index, entry in enumerate(entries, start=1):
        if cancel.is_set():
            result.cancelled = True
            break
        if progress:
            progress(index - 1, total, f"Rendering {format_timestamp(entry.seconds)}")
        target = output_dir / f"{index:03d}_{format_filename(entry.seconds)}.{extension}"
        try:
            extract_frame(
                options.video,
                entry.seconds,
                target,
                subtitle=prepared,
                fonts_dir=fonts_dir,
                image_format=options.image_format,
                jpeg_quality=options.jpeg_quality,
            )
        except ExtractionError as exc:
            result.errors.append(f"{format_timestamp(entry.seconds)}: {exc}")
            continue
        rendered.append((entry, target))
        if prepared is not None and prepared.is_active(entry.seconds) is False:
            result.warnings.append(
                f"{format_timestamp(entry.seconds)}: no subtitle is displayed at this timestamp"
            )
        if progress:
            progress(index, total, f"Rendered {format_timestamp(entry.seconds)}")

    return rendered


def render_preview(options: GenerationOptions) -> Path:
    """Render a single still into a temporary directory and return its path.

    The caller owns the returned file's parent directory and should remove it
    when the preview is no longer needed.
    """
    if not options.entries:
        raise ValueError("no timestamp to preview")
    entry = options.entries[0]
    tmpdir = Path(tempfile.mkdtemp(prefix="yass-preview-"))
    try:
        prepared, fonts_dir = _prepare_assets(
            options, tmpdir, GenerationResult(output_dir=tmpdir)
        )
        target = tmpdir / f"preview.{_extension(options)}"
        extract_frame(
            options.video,
            entry.seconds,
            target,
            subtitle=prepared,
            fonts_dir=fonts_dir,
            image_format=options.image_format,
            jpeg_quality=options.jpeg_quality,
        )
        return target
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _prepare_assets(
    options: GenerationOptions,
    tmpdir: Path,
    result: GenerationResult,
) -> tuple[PreparedSubtitle | None, Path | None]:
    prepared: PreparedSubtitle | None = None
    if options.external_subtitle is not None:
        prepared = prepare_subtitle(options.video, None, options.external_subtitle, tmpdir)
    elif options.subtitle_track is not None:
        if not options.subtitle_track.is_text:
            raise SubtitleError(
                f"Subtitle track {options.subtitle_track.label} is not text-based "
                "and cannot be burned in"
            )
        prepared = prepare_subtitle(options.video, options.subtitle_track, None, tmpdir)

    fonts_dir: Path | None = None
    if options.media.font_attachments:
        try:
            fonts_dir = extract_fonts(
                options.video, tmpdir / "fonts", options.media.font_attachments
            )
        except SubtitleError as exc:
            result.warnings.append(f"Could not extract embedded fonts: {exc}")
    return prepared, fonts_dir


def _build_contact_sheet(
    options: GenerationOptions,
    rendered: list[tuple[TimestampEntry, Path]],
    output_dir: Path,
) -> Path:
    spec = LayoutSpec(
        direction=options.direction,
        columns=options.columns,
        gap=options.gap,
        margin=options.margin,
        background=options.background,
        show_labels=options.show_labels,
        strip=options.strip,
    )
    labels = [format_timestamp(entry.seconds) for entry, _ in rendered]
    sheet_path = output_dir / f"contact_sheet.{_extension(options)}"
    image = compose([path for _, path in rendered], spec, labels=labels)
    try:
        _save_image(image, sheet_path, options)
    finally:
        image.close()
    return sheet_path


def _save_image(image: Image.Image, path: Path, options: GenerationOptions) -> None:
    if options.image_format == "jpg":
        image.save(path, format="JPEG", quality=options.jpeg_quality, subsampling=0)
    else:
        image.save(path, format="PNG")


def _extension(options: GenerationOptions) -> str:
    return "jpg" if options.image_format == "jpg" else "png"


def _create_run_dir(root: Path, stem: str) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = root / f"{stem}_{timestamp}"
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = root / f"{base.name}_{counter}"
        counter += 1
    candidate.mkdir()
    return candidate
