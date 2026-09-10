from __future__ import annotations

from pathlib import Path

from .subs import PreparedSubtitle
from .tools import ffmpeg_binary, run_command

_FILTER_SPECIALS = "\\':,[];"


class ExtractionError(RuntimeError):
    """Raised when ffmpeg fails to render a frame."""


def jpeg_qscale(quality: int) -> int:
    """Map a 1-100 quality value to ffmpeg's 2-31 JPEG qscale."""
    quality = max(1, min(100, quality))
    return max(2, min(31, round(31 - (quality / 100) * 29)))


def escape_filter_value(value: str) -> str:
    return "".join(f"\\{char}" if char in _FILTER_SPECIALS else char for char in value)


def build_frame_command(
    video: Path,
    seconds: float,
    output: Path,
    subtitle: PreparedSubtitle | None = None,
    fonts_dir: Path | None = None,
    image_format: str = "png",
    jpeg_quality: int = 90,
) -> list[str]:
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{seconds:.3f}",
        "-copyts",
        "-i",
        str(video),
        "-map",
        "0:v:0",
    ]

    if subtitle is not None:
        filter_arg = f"subtitles=filename={escape_filter_value(str(subtitle.path))}"
        if fonts_dir is not None:
            filter_arg += f":fontsdir={escape_filter_value(str(fonts_dir))}"
        command += ["-vf", filter_arg]

    command += ["-frames:v", "1"]

    if image_format == "jpg":
        command += ["-q:v", str(jpeg_qscale(jpeg_quality))]

    command.append(str(output))
    return command


def extract_frame(
    video: Path,
    seconds: float,
    output: Path,
    subtitle: PreparedSubtitle | None = None,
    fonts_dir: Path | None = None,
    image_format: str = "png",
    jpeg_quality: int = 90,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = build_frame_command(
        video,
        seconds,
        output,
        subtitle=subtitle,
        fonts_dir=fonts_dir,
        image_format=image_format,
        jpeg_quality=jpeg_quality,
    )
    completed = run_command(command)
    if completed.returncode != 0 or not output.is_file():
        raise ExtractionError(
            completed.stderr.strip() or f"ffmpeg failed at {seconds:.3f}s"
        )
    return output
